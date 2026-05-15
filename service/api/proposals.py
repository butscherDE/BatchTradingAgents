"""Trade proposal approval API endpoints."""

import asyncio
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from service import clock

from service.db.models import TradeAction, TradeProposal, ProposalStatus

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


async def get_session():
    from service.app import get_db_session
    async with get_db_session() as session:
        yield session


class ProposalSummary(BaseModel):
    id: int
    account_id: str
    strategy: str
    status: str
    tickers: list[str]
    created_at: datetime.datetime
    decided_at: Optional[datetime.datetime] = None
    superseded_by: Optional[int] = None


class ProposalDetail(BaseModel):
    id: int
    account_id: str
    strategy: str
    status: str
    merge_report: str
    tickers: list[str]
    ticker_data: list[dict]
    allocation: Optional[list[dict]] = None
    allocation_reasoning: Optional[str] = None
    cash_pct: Optional[float] = None
    portfolio_value: Optional[float] = None
    cash_after: Optional[float] = None
    proposed_orders: Optional[list[dict]] = None
    source_task_id: Optional[str] = None
    superseded_by: Optional[int] = None
    created_at: datetime.datetime
    decided_at: Optional[datetime.datetime] = None
    execution_results: Optional[list[dict]] = None


@router.get("", response_model=list[ProposalSummary])
async def list_proposals(
    account_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, le=100),
    session: AsyncSession = Depends(get_session),
):
    query = select(TradeProposal).order_by(TradeProposal.created_at.desc())
    if account_id:
        query = query.where(TradeProposal.account_id == account_id)
    if status:
        query = query.where(TradeProposal.status == status)
    query = query.limit(limit)

    result = await session.execute(query)
    proposals = result.scalars().all()
    return [
        ProposalSummary(
            id=p.id,
            account_id=p.account_id,
            strategy=p.strategy,
            status=p.status.value if hasattr(p.status, "value") else p.status,
            tickers=p.tickers or [],
            created_at=p.created_at,
            decided_at=p.decided_at,
            superseded_by=p.superseded_by,
        )
        for p in proposals
    ]


# --- Merge schedule endpoints (must be before /{proposal_id} to avoid path conflict) ---

class MergeSchedule(BaseModel):
    account_id: str
    days: list[int]  # 0=Mon, 1=Tue, ..., 6=Sun
    times: list[str]  # ["06:00", "09:00", "12:00"]
    enabled: bool = True
    merge_checks: int = 2
    allocation_checks: int = 2
    provider: str = ""


@router.get("/schedule", response_model=list[MergeSchedule])
async def get_schedules():
    from service.app import get_merge_schedules
    return get_merge_schedules()


@router.put("/schedule")
async def set_schedule(schedule: MergeSchedule):
    from service.app import set_merge_schedule
    set_merge_schedule(schedule.account_id, schedule.model_dump(exclude={"account_id"}))
    return {"status": "ok", "schedule": schedule}


@router.delete("/schedule/{account_id}")
async def delete_schedule(account_id: str):
    from service.app import delete_merge_schedule
    delete_merge_schedule(account_id)
    return {"status": "deleted", "account_id": account_id}


@router.get("/{proposal_id}", response_model=ProposalDetail)
async def get_proposal(proposal_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(TradeProposal).where(TradeProposal.id == proposal_id)
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")

    return ProposalDetail(
        id=p.id,
        account_id=p.account_id,
        strategy=p.strategy,
        status=p.status.value if hasattr(p.status, "value") else p.status,
        merge_report=p.merge_report,
        tickers=p.tickers or [],
        ticker_data=p.ticker_data or [],
        allocation=p.allocation,
        allocation_reasoning=p.allocation_reasoning,
        cash_pct=p.cash_pct,
        portfolio_value=p.portfolio_value,
        cash_after=p.cash_after,
        proposed_orders=p.proposed_orders,
        source_task_id=p.source_task_id,
        superseded_by=p.superseded_by,
        created_at=p.created_at,
        decided_at=p.decided_at,
        execution_results=p.execution_results,
    )


@router.post("/{proposal_id}/approve")
async def approve_proposal(proposal_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(TradeProposal).where(TradeProposal.id == proposal_id)
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    status_val = proposal.status.value if hasattr(proposal.status, "value") else proposal.status
    if status_val != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is '{status_val}', not 'pending'. No orders submitted.",
        )

    # Execute orders
    orders = proposal.proposed_orders or []
    execution_results = []

    if orders:
        from service.app import _config
        acct = _config.accounts.get(proposal.account_id) if _config else None
        if acct:
            execution_results = await asyncio.to_thread(
                _execute_orders, acct, orders
            )

    # Persist a TradeAction row per submitted order (success or failure)
    submitted_at = clock.now()
    for order, result in zip(orders, execution_results):
        err = result.get("error")
        status_str = result.get("status") or ("failed" if err else "submitted")
        session.add(TradeAction(
            account_id=proposal.account_id,
            ticker=order.get("ticker", "").upper(),
            action=order.get("side") or result.get("side") or "buy",
            qty=order.get("qty"),
            notional=order.get("notional"),
            trigger="proposal",
            proposal_id=proposal.id,
            order_id=result.get("order_id"),
            status=status_str,
            error=err,
            submitted_at=submitted_at,
        ))

    proposal.status = ProposalStatus.approved
    proposal.decided_at = submitted_at
    proposal.execution_results = execution_results
    await session.commit()

    from service.api.ws import broadcast
    await broadcast("proposal_approved", {
        "proposal_id": proposal_id,
        "account_id": proposal.account_id,
        "order_count": len(execution_results),
    })

    return {"status": "approved", "execution_results": execution_results}


@router.post("/{proposal_id}/reject")
async def reject_proposal(proposal_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(TradeProposal).where(TradeProposal.id == proposal_id)
    )
    proposal = result.scalar_one_or_none()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")

    status_val = proposal.status.value if hasattr(proposal.status, "value") else proposal.status
    if status_val != "pending":
        raise HTTPException(status_code=409, detail=f"Proposal is '{status_val}', not 'pending'")

    proposal.status = ProposalStatus.rejected
    proposal.decided_at = clock.now()
    await session.commit()

    return {"status": "rejected"}


def _execute_orders(acct, orders: list[dict]) -> list[dict]:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    client = TradingClient(acct.api_key, acct.api_secret, paper=acct.is_paper)
    results = []

    for order in orders:
        try:
            side = OrderSide.BUY if order.get("side") == "buy" else OrderSide.SELL
            kwargs = {
                "symbol": order["ticker"],
                "side": side,
                "time_in_force": TimeInForce.DAY,
            }
            if order.get("qty"):
                kwargs["qty"] = order["qty"]
            elif order.get("notional"):
                kwargs["notional"] = order["notional"]
            else:
                results.append({"ticker": order["ticker"], "error": "No qty or notional"})
                continue

            submitted = client.submit_order(MarketOrderRequest(**kwargs))
            results.append({
                "ticker": order["ticker"],
                "side": order.get("side"),
                "qty": order.get("qty"),
                "order_id": str(submitted.id),
                "status": submitted.status.value,
            })
        except Exception as e:
            results.append({
                "ticker": order["ticker"],
                "side": order.get("side"),
                "error": str(e),
            })

    return results


@router.post("/trigger")
async def trigger_merge_allocate(
    account_id: str = Query(...),
    merge_checks: Optional[int] = Query(default=None, description="Override merge validation passes"),
    allocation_checks: Optional[int] = Query(default=None, description="Override allocation validation passes"),
    provider: Optional[str] = Query(default=None, description="Force routing to a specific provider"),
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger a merge+allocate for an account using latest reports on disk."""
    from service.app import get_scheduler, _config, _load_report_from_disk, _extract_decision
    from service.core.gpu_scheduler import TaskSpec
    from service.db.models import GpuTask, TaskStatus, WatchlistTicker
    from fastapi import HTTPException
    from pathlib import Path
    import json

    if not _config:
        raise HTTPException(status_code=503, detail="Service not initialized")

    acct = _config.accounts.get(account_id)
    if not acct:
        raise HTTPException(status_code=404, detail=f"Account '{account_id}' not found")

    # Get active watchlist tickers for this account
    result = await session.execute(
        select(WatchlistTicker.symbol).where(
            WatchlistTicker.account_id == account_id,
            WatchlistTicker.active == 1,
        )
    )
    symbols = list(result.scalars().all())

    # Load latest states from disk
    tickers_data = []
    state_dir = Path("reports") / "_states"
    for sym in symbols:
        state_file = state_dir / f"{sym.replace(':', '_')}.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                decision = _extract_decision(state.get("final_trade_decision", ""))
                tickers_data.append({
                    "ticker": sym,
                    "decision": decision,
                    "final_state": state,
                })
            except Exception:
                pass

    if not tickers_data:
        raise HTTPException(status_code=400, detail="No reports available. Run full analysis first.")

    # Fetch portfolio
    portfolio = None
    try:
        from cli.alpaca_client import create_client, fetch_portfolio
        client = create_client(acct.api_key, acct.api_secret, paper=acct.is_paper)
        port, _, prices, _ = fetch_portfolio(client)
        portfolio = {"holdings": port.holdings, "cash": port.cash, "prices": prices}
    except Exception:
        pass

    # Submit merge+allocate task
    scheduler = get_scheduler()
    task_id = await scheduler.submit(TaskSpec(
        model_tier="deep",
        task_type="merge_and_allocate",
        payload={
            "account_id": account_id,
            "tickers_data": tickers_data,
            "strategy": acct.strategy,
            "portfolio": portfolio,
            "merge_checks_override": merge_checks,
            "allocation_checks_override": allocation_checks,
        },
    ), provider=provider)

    db_task = GpuTask(
        task_id=task_id,
        model_tier="deep",
        task_type="merge_and_allocate",
        priority=1,
        status=TaskStatus.queued,
        payload={"account_id": account_id, "tickers": [t["ticker"] for t in tickers_data]},
    )
    session.add(db_task)
    await session.commit()

    return {
        "submitted": True,
        "task_id": task_id,
        "account_id": account_id,
        "tickers_count": len(tickers_data),
        "tickers": [t["ticker"] for t in tickers_data],
    }
