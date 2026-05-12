"""Account holdings API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class HoldingResponse(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    total_pl: float
    total_pl_pct: float
    day_pl: float
    day_pl_pct: float
    market_value: float
    portfolio_pct: float


class AccountSummary(BaseModel):
    id: str
    name: str
    is_paper: bool
    strategy: str
    watchlist: str
    portfolio_value: float
    cash: float
    day_pl: float
    day_pl_pct: float


class AccountHoldingsResponse(BaseModel):
    account: AccountSummary
    holdings: list[HoldingResponse]


@router.get("", response_model=list[AccountSummary])
async def list_accounts():
    from service.app import _config
    if not _config:
        raise HTTPException(status_code=503, detail="Service not initialized")

    accounts = []
    for name, acct in _config.accounts.items():
        summary = await _fetch_account_summary(name, acct)
        accounts.append(summary)
    return accounts


@router.get("/{account_id}/holdings", response_model=AccountHoldingsResponse)
async def get_holdings(account_id: str):
    from service.app import _config
    if not _config:
        raise HTTPException(status_code=503, detail="Service not initialized")

    acct = _config.accounts.get(account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")

    import asyncio
    summary, holdings = await asyncio.to_thread(
        _fetch_holdings_sync, account_id, acct
    )
    return AccountHoldingsResponse(account=summary, holdings=holdings)


async def _fetch_account_summary(name: str, acct) -> AccountSummary:
    import asyncio
    return await asyncio.to_thread(_fetch_summary_sync, name, acct)


def _fetch_summary_sync(name: str, acct) -> AccountSummary:
    from cli.broker import create_broker_from_config

    try:
        client = create_broker_from_config(acct)
        positions = client.get_positions()
        cash = client.get_account_cash()

        portfolio_value = cash + sum(
            (p.current_price or 0) * p.qty for p in positions
        )
        # No day P/L available generically — compute from positions
        day_pl = 0.0
        day_pl_pct = 0.0

        return AccountSummary(
            id=name,
            name=name,
            is_paper=acct.is_paper,
            strategy=acct.strategy,
            watchlist=acct.watchlist,
            portfolio_value=portfolio_value,
            cash=cash,
            day_pl=day_pl,
            day_pl_pct=day_pl_pct,
        )
    except Exception as e:
        return AccountSummary(
            id=name,
            name=f"{name} (error: {str(e)[:50]})",
            is_paper=acct.is_paper,
            strategy=acct.strategy,
            watchlist=acct.watchlist,
            portfolio_value=0,
            cash=0,
            day_pl=0,
            day_pl_pct=0,
        )


def _fetch_holdings_sync(name: str, acct) -> tuple[AccountSummary, list[HoldingResponse]]:
    from cli.broker import create_broker_from_config

    client = create_broker_from_config(acct)
    positions = client.get_positions()
    cash = client.get_account_cash()

    portfolio_value = cash + sum(
        (p.current_price or 0) * p.qty for p in positions
    )
    day_pl_total = 0.0
    day_pl_pct_total = 0.0

    summary = AccountSummary(
        id=name,
        name=name,
        is_paper=acct.is_paper,
        strategy=acct.strategy,
        watchlist=acct.watchlist,
        portfolio_value=portfolio_value,
        cash=cash,
        day_pl=day_pl_total,
        day_pl_pct=day_pl_pct_total,
    )

    holdings = []
    for pos in positions:
        qty = pos.qty
        entry = pos.avg_entry_price or 0
        current = pos.current_price or 0
        market_value = qty * current
        unrealized_pl = pos.unrealized_pl if pos.unrealized_pl is not None else (current - entry) * qty
        unrealized_plpc = pos.unrealized_plpc if pos.unrealized_plpc is not None else (
            ((current - entry) / entry) if entry > 0 else 0
        )

        pct_of_portfolio = (market_value / portfolio_value * 100) if portfolio_value > 0 else 0

        holdings.append(HoldingResponse(
            symbol=pos.symbol,
            qty=qty,
            avg_entry_price=entry,
            current_price=current,
            total_pl=unrealized_pl,
            total_pl_pct=unrealized_plpc * 100 if abs(unrealized_plpc) < 1 else unrealized_plpc,
            day_pl=0,
            day_pl_pct=0,
            market_value=market_value,
            portfolio_pct=pct_of_portfolio,
        ))

    return summary, holdings
