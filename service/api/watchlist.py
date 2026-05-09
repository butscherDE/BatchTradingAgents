"""Watchlist management API endpoints (per-account)."""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from service.db.models import WatchlistTicker

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


async def get_session():
    from service.app import get_db_session
    async with get_db_session() as session:
        yield session


class WatchlistTickerResponse(BaseModel):
    id: int
    account_id: str
    symbol: str
    added_by: str
    added_at: datetime.datetime
    removed_at: Optional[datetime.datetime] = None
    remove_reason: Optional[str] = None
    active: bool


class AddTickerRequest(BaseModel):
    account_id: str
    symbol: str


@router.get("", response_model=list[WatchlistTickerResponse])
async def list_watchlist(
    account_id: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
):
    query = select(WatchlistTicker).order_by(WatchlistTicker.account_id, WatchlistTicker.symbol)
    if account_id:
        query = query.where(WatchlistTicker.account_id == account_id)
    if active_only:
        query = query.where(WatchlistTicker.active == 1)
    result = await session.execute(query)
    tickers = result.scalars().all()
    return [
        WatchlistTickerResponse(
            id=t.id,
            account_id=t.account_id,
            symbol=t.symbol,
            added_by=t.added_by,
            added_at=t.added_at,
            removed_at=t.removed_at,
            remove_reason=t.remove_reason,
            active=bool(t.active),
        )
        for t in tickers
    ]


@router.post("", response_model=WatchlistTickerResponse, status_code=201)
async def add_ticker(body: AddTickerRequest, session: AsyncSession = Depends(get_session)):
    symbol = body.symbol.upper().strip()
    account_id = body.account_id.strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol required")
    if not account_id:
        raise HTTPException(status_code=400, detail="account_id required")

    # Validate ticker exists
    import asyncio
    valid = await asyncio.to_thread(_validate_ticker, symbol)
    if not valid:
        raise HTTPException(status_code=400, detail=f"'{symbol}' is not a valid tradeable ticker")

    existing = await session.execute(
        select(WatchlistTicker).where(
            WatchlistTicker.account_id == account_id,
            WatchlistTicker.symbol == symbol,
        )
    )
    ticker = existing.scalar_one_or_none()

    if ticker and ticker.active:
        raise HTTPException(status_code=409, detail=f"{symbol} already on watchlist for {account_id}")

    if ticker and not ticker.active:
        ticker.active = 1
        ticker.removed_at = None
        ticker.remove_reason = None
        ticker.added_at = datetime.datetime.utcnow()
        ticker.added_by = "manual"
        await session.commit()
        await session.refresh(ticker)
    else:
        ticker = WatchlistTicker(
            account_id=account_id,
            symbol=symbol,
            added_by="manual",
            added_at=datetime.datetime.utcnow(),
            active=1,
        )
        session.add(ticker)
        await session.commit()
        await session.refresh(ticker)

    return WatchlistTickerResponse(
        id=ticker.id,
        account_id=ticker.account_id,
        symbol=ticker.symbol,
        added_by=ticker.added_by,
        added_at=ticker.added_at,
        removed_at=ticker.removed_at,
        remove_reason=ticker.remove_reason,
        active=bool(ticker.active),
    )


@router.delete("/{symbol}")
async def remove_ticker(
    symbol: str,
    account_id: str = Query(...),
    reason: str = Query(default="manual removal"),
    session: AsyncSession = Depends(get_session),
):
    symbol = symbol.upper().strip()
    result = await session.execute(
        select(WatchlistTicker).where(
            WatchlistTicker.account_id == account_id,
            WatchlistTicker.symbol == symbol,
            WatchlistTicker.active == 1,
        )
    )
    ticker = result.scalar_one_or_none()
    if not ticker:
        raise HTTPException(status_code=404, detail=f"{symbol} not on active watchlist for {account_id}")

    ticker.active = 0
    ticker.removed_at = datetime.datetime.utcnow()
    ticker.remove_reason = reason
    await session.commit()
    return {"removed": symbol, "account_id": account_id, "reason": reason}


@router.get("/config")
async def get_watchlist_config():
    from service.app import _config
    if not _config:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return {
        "dynamic_discovery": _config.watchlist.dynamic_discovery,
        "auto_prune": _config.watchlist.auto_prune,
    }


@router.get("/search")
async def search_tickers(q: str = Query(min_length=1)):
    """Search for tickers by symbol or company name with live price data."""
    import asyncio
    results = await asyncio.to_thread(_search_tickers, q)
    return results


def _validate_ticker(symbol: str) -> bool:
    from service.app import _config
    if not _config or not _config.accounts:
        return True
    acct = next(iter(_config.accounts.values()))
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(acct.api_key, acct.api_secret, paper=acct.is_paper)
        asset = client.get_asset(symbol)
        return asset.tradable
    except Exception:
        return False


def _search_tickers(query: str) -> list[dict]:
    """Search tickers via yfinance search API, enrich with Alpaca quotes."""
    import yfinance as yf
    from service.app import _config

    results = []

    try:
        search = yf.Search(query, max_results=8)
        for quote in getattr(search, "quotes", []):
            symbol = quote.get("symbol", "")
            if not symbol or "." in symbol:
                continue
            results.append({
                "symbol": symbol,
                "name": quote.get("shortname") or quote.get("longname") or "",
            })
    except Exception:
        return []

    if not results:
        return []

    if _config and _config.accounts:
        acct = next(iter(_config.accounts.values()))
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockSnapshotRequest

            data_client = StockHistoricalDataClient(acct.api_key, acct.api_secret)
            symbols = [r["symbol"] for r in results]
            snapshots = data_client.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=symbols)
            )

            for r in results:
                snap = snapshots.get(r["symbol"])
                if snap and snap.latest_trade:
                    price = float(snap.latest_trade.price)
                    prev_close = float(snap.previous_daily_bar.close) if snap.previous_daily_bar else None
                    if prev_close:
                        day_change = price - prev_close
                        day_change_pct = (day_change / prev_close) * 100
                    else:
                        day_change = 0
                        day_change_pct = 0
                    r["price"] = price
                    r["day_change"] = day_change
                    r["day_change_pct"] = day_change_pct
                else:
                    r["price"] = None
                    r["day_change"] = None
                    r["day_change_pct"] = None
        except Exception:
            for r in results:
                r.setdefault("price", None)
                r.setdefault("day_change", None)
                r.setdefault("day_change_pct", None)
    else:
        for r in results:
            r["price"] = None
            r["day_change"] = None
            r["day_change_pct"] = None

    return results
