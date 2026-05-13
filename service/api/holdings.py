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
    from alpaca.trading.client import TradingClient

    try:
        client = TradingClient(acct.api_key, acct.api_secret, paper=acct.is_paper)
        account = client.get_account()

        portfolio_value = float(account.portfolio_value or 0)
        cash = float(account.cash or 0)
        last_equity = float(account.last_equity or portfolio_value)
        day_pl = portfolio_value - last_equity
        day_pl_pct = (day_pl / last_equity * 100) if last_equity > 0 else 0.0

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
    from alpaca.trading.client import TradingClient

    client = TradingClient(acct.api_key, acct.api_secret, paper=acct.is_paper)
    account = client.get_account()
    positions = client.get_all_positions()

    portfolio_value = float(account.portfolio_value or 0)
    cash = float(account.cash or 0)
    last_equity = float(account.last_equity or portfolio_value)
    day_pl_total = portfolio_value - last_equity
    day_pl_pct_total = (day_pl_total / last_equity * 100) if last_equity > 0 else 0.0

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
        qty = float(pos.qty)
        entry = float(pos.avg_entry_price) if pos.avg_entry_price else 0
        current = float(pos.current_price) if pos.current_price else 0
        market_value = float(pos.market_value) if pos.market_value else qty * current
        unrealized_pl = float(pos.unrealized_pl) if pos.unrealized_pl else (current - entry) * qty
        unrealized_plpc = float(pos.unrealized_plpc) if pos.unrealized_plpc else (
            ((current - entry) / entry * 100) if entry > 0 else 0
        )

        change_today = float(pos.change_today) if hasattr(pos, "change_today") and pos.change_today else 0
        day_pl = market_value * change_today if change_today else 0
        day_pl_pct = change_today * 100 if change_today else 0

        pct_of_portfolio = (market_value / portfolio_value * 100) if portfolio_value > 0 else 0

        holdings.append(HoldingResponse(
            symbol=pos.symbol,
            qty=qty,
            avg_entry_price=entry,
            current_price=current,
            total_pl=unrealized_pl,
            total_pl_pct=unrealized_plpc * 100 if abs(unrealized_plpc) < 1 else unrealized_plpc,
            day_pl=day_pl,
            day_pl_pct=day_pl_pct,
            market_value=market_value,
            portfolio_pct=pct_of_portfolio,
        ))

    return summary, holdings
