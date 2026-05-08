"""Watchlist management API endpoints."""

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
    symbol: str
    added_by: str
    added_at: datetime.datetime
    removed_at: Optional[datetime.datetime] = None
    remove_reason: Optional[str] = None
    active: bool


class AddTickerRequest(BaseModel):
    symbol: str


class RemoveTickerRequest(BaseModel):
    symbol: str
    reason: str = "manual removal"


@router.get("", response_model=list[WatchlistTickerResponse])
async def list_watchlist(
    active_only: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
):
    query = select(WatchlistTicker).order_by(WatchlistTicker.symbol)
    if active_only:
        query = query.where(WatchlistTicker.active == 1)
    result = await session.execute(query)
    tickers = result.scalars().all()
    return [
        WatchlistTickerResponse(
            id=t.id,
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
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol required")

    # Validate ticker exists
    import asyncio
    valid = await asyncio.to_thread(_validate_ticker, symbol)
    if not valid:
        raise HTTPException(status_code=400, detail=f"'{symbol}' is not a valid tradeable ticker")

    existing = await session.execute(
        select(WatchlistTicker).where(WatchlistTicker.symbol == symbol)
    )
    ticker = existing.scalar_one_or_none()

    if ticker and ticker.active:
        raise HTTPException(status_code=409, detail=f"{symbol} already on watchlist")

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
        symbol=ticker.symbol,
        added_by=ticker.added_by,
        added_at=ticker.added_at,
        removed_at=ticker.removed_at,
        remove_reason=ticker.remove_reason,
        active=bool(ticker.active),
    )


@router.delete("/{symbol}")
async def remove_ticker(symbol: str, reason: str = "manual removal", session: AsyncSession = Depends(get_session)):
    symbol = symbol.upper().strip()
    result = await session.execute(
        select(WatchlistTicker).where(WatchlistTicker.symbol == symbol, WatchlistTicker.active == 1)
    )
    ticker = result.scalar_one_or_none()
    if not ticker:
        raise HTTPException(status_code=404, detail=f"{symbol} not on active watchlist")

    ticker.active = 0
    ticker.removed_at = datetime.datetime.utcnow()
    ticker.remove_reason = reason
    await session.commit()
    return {"removed": symbol, "reason": reason}


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
    """Check if a ticker is a valid tradeable asset on Alpaca."""
    from service.app import _config

    if not _config or not _config.accounts:
        return True  # Can't validate without credentials, allow it

    acct = next(iter(_config.accounts.values()))
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(acct.api_key, acct.api_secret, paper=acct.is_paper)
        asset = client.get_asset(symbol)
        return asset.tradable
    except Exception:
        return False


def _search_tickers(query: str) -> list[dict]:
    """Search Alpaca assets by symbol or name, return with live quotes."""
    from service.app import _config

    if not _config or not _config.accounts:
        return []

    acct = next(iter(_config.accounts.values()))

    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import SearchAssetsRequest

    client = TradingClient(acct.api_key, acct.api_secret, paper=acct.is_paper)

    query_upper = query.upper().strip()

    # Search by getting all assets and filtering (Alpaca doesn't have a search endpoint)
    # Use get_asset for exact match first, then fall back to listing
    results = []

    # Try exact symbol match
    try:
        asset = client.get_asset(query_upper)
        if asset.tradable:
            results.append({
                "symbol": asset.symbol,
                "name": asset.name,
            })
    except Exception:
        pass

    # Search through active assets for partial matches
    if len(results) < 8:
        try:
            from alpaca.trading.enums import AssetStatus
            assets = client.get_all_assets(
                filter=SearchAssetsRequest(status=AssetStatus.ACTIVE)
            )
            q_lower = query.lower()
            for asset in assets:
                if not asset.tradable:
                    continue
                if len(results) >= 8:
                    break
                sym = asset.symbol or ""
                name = asset.name or ""
                if (q_lower in sym.lower() or q_lower in name.lower()) and sym not in [r["symbol"] for r in results]:
                    results.append({
                        "symbol": sym,
                        "name": name,
                    })
        except Exception:
            pass

    if not results:
        return []

    # Fetch live quotes for the results
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest, StockSnapshotRequest

        data_client = StockHistoricalDataClient(acct.api_key, acct.api_secret)
        symbols = [r["symbol"] for r in results]

        snapshots = data_client.get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=symbols)
        )

        for r in results:
            snap = snapshots.get(r["symbol"])
            if snap and snap.daily_bar:
                price = snap.latest_trade.price if snap.latest_trade else snap.daily_bar.close
                prev_close = snap.previous_daily_bar.close if snap.previous_daily_bar else snap.daily_bar.open
                day_change = price - prev_close if prev_close else 0
                day_change_pct = (day_change / prev_close * 100) if prev_close else 0
                r["price"] = float(price)
                r["day_change"] = float(day_change)
                r["day_change_pct"] = float(day_change_pct)
            else:
                r["price"] = None
                r["day_change"] = None
                r["day_change_pct"] = None
    except Exception:
        for r in results:
            r.setdefault("price", None)
            r.setdefault("day_change", None)
            r.setdefault("day_change_pct", None)

    return results
