"""Trade history API."""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from service.db.models import TradeAction

router = APIRouter(prefix="/api/trades", tags=["trades"])


async def get_session():
    from service.app import get_db_session
    async with get_db_session() as session:
        yield session


class TradeResponse(BaseModel):
    id: int
    account_id: str
    ticker: str
    action: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    trigger: str
    proposal_id: Optional[int] = None
    order_id: Optional[str] = None
    status: str
    error: Optional[str] = None
    submitted_at: datetime.datetime
    filled_qty: Optional[float] = None
    filled_avg_price: Optional[float] = None
    filled_at: Optional[datetime.datetime] = None


@router.get("", response_model=list[TradeResponse])
async def list_trades(
    account_id: Optional[str] = Query(default=None),
    ticker: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    trigger: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=1000),
    session: AsyncSession = Depends(get_session),
):
    query = select(TradeAction).order_by(TradeAction.submitted_at.desc())
    if account_id:
        query = query.where(TradeAction.account_id == account_id)
    if ticker:
        query = query.where(TradeAction.ticker == ticker.upper())
    if status:
        query = query.where(TradeAction.status == status)
    if trigger:
        query = query.where(TradeAction.trigger == trigger)
    query = query.limit(limit)

    result = await session.execute(query)
    return [
        TradeResponse(
            id=t.id,
            account_id=t.account_id,
            ticker=t.ticker,
            action=t.action,
            qty=t.qty,
            notional=t.notional,
            trigger=t.trigger,
            proposal_id=t.proposal_id,
            order_id=t.order_id,
            status=t.status,
            error=t.error,
            submitted_at=t.submitted_at,
            filled_qty=t.filled_qty,
            filled_avg_price=t.filled_avg_price,
            filled_at=t.filled_at,
        )
        for t in result.scalars().all()
    ]
