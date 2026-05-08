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
