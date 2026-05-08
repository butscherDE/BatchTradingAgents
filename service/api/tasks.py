"""Task manager API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from service.db.models import GpuTask, TaskStatus
from service.models.schemas import TaskResponse, TaskStats

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


async def get_session():
    from service.app import get_db_session
    async with get_db_session() as session:
        yield session


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None),
    model_tier: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    query = select(GpuTask).order_by(GpuTask.created_at.desc())

    if status:
        query = query.where(GpuTask.status == status)
    if model_tier:
        query = query.where(GpuTask.model_tier == model_tier)

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    tasks = result.scalars().all()

    return [
        TaskResponse(
            id=t.id,
            task_id=t.task_id,
            model_tier=t.model_tier,
            task_type=t.task_type,
            ticker=t.ticker,
            priority=t.priority,
            status=t.status.value if hasattr(t.status, "value") else t.status,
            created_at=t.created_at,
            started_at=t.started_at,
            completed_at=t.completed_at,
            error=t.error,
        )
        for t in tasks
    ]


@router.get("/stats", response_model=TaskStats)
async def get_task_stats(session: AsyncSession = Depends(get_session)):
    from service.app import get_scheduler

    scheduler = get_scheduler()
    depths = await scheduler.get_queue_depths()
    worker_status = await scheduler.get_worker_status()

    completed = await session.execute(
        select(func.count()).where(GpuTask.status == TaskStatus.completed)
    )
    failed = await session.execute(
        select(func.count()).where(GpuTask.status == TaskStatus.failed)
    )

    return TaskStats(
        queue_depth_quick=depths.get("quick", 0),
        queue_depth_deep=depths.get("deep", 0),
        total_completed=completed.scalar() or 0,
        total_failed=failed.scalar() or 0,
        current_model=worker_status.get("current_model") if worker_status else None,
        worker_state=worker_status.get("state") if worker_status else None,
        model_switches=worker_status.get("model_switches", 0) if worker_status else 0,
    )
