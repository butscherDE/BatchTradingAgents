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
    task_type: Optional[str] = Query(default=None),
    ticker: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    query = select(GpuTask).order_by(GpuTask.created_at.desc())

    if status:
        query = query.where(GpuTask.status == status)
    if model_tier:
        query = query.where(GpuTask.model_tier == model_tier)
    if task_type:
        query = query.where(GpuTask.task_type == task_type)
    if ticker:
        query = query.where(GpuTask.ticker.ilike(f"%{ticker}%"))

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
    paused = await scheduler.is_paused()

    completed = await session.execute(
        select(func.count()).where(GpuTask.status == TaskStatus.completed)
    )
    failed = await session.execute(
        select(func.count()).where(GpuTask.status == TaskStatus.failed)
    )

    worker_state = worker_status.get("state") if worker_status else None
    if paused and worker_state in ("executing", "switching_model"):
        worker_state = "pausing"
    elif paused:
        worker_state = "paused"

    return TaskStats(
        queue_depth_quick=depths.get("quick", 0),
        queue_depth_deep=depths.get("deep", 0),
        total_completed=completed.scalar() or 0,
        total_failed=failed.scalar() or 0,
        current_model=worker_status.get("current_model") if worker_status else None,
        worker_state=worker_state,
        model_switches=worker_status.get("model_switches", 0) if worker_status else 0,
    )


@router.post("/pause")
async def pause_worker():
    from service.app import get_scheduler
    scheduler = get_scheduler()
    await scheduler.pause()
    return {"paused": True}


@router.post("/resume")
async def resume_worker():
    from service.app import get_scheduler
    scheduler = get_scheduler()
    await scheduler.resume()
    return {"paused": False}


@router.get("/{task_id}")
async def get_task_detail(task_id: str, session: AsyncSession = Depends(get_session)):
    """Get full task detail including result JSON."""
    from fastapi import HTTPException
    result = await session.execute(
        select(GpuTask).where(GpuTask.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "id": task.id,
        "task_id": task.task_id,
        "model_tier": task.model_tier,
        "task_type": task.task_type,
        "ticker": task.ticker,
        "priority": task.priority,
        "status": task.status.value if hasattr(task.status, "value") else task.status,
        "payload": task.payload,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
    }


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, session: AsyncSession = Depends(get_session)):
    """Cancel a queued or running task."""
    from fastapi import HTTPException

    result = await session.execute(
        select(GpuTask).where(GpuTask.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    status_val = task.status.value if hasattr(task.status, "value") else task.status
    if status_val not in ("queued", "running"):
        raise HTTPException(status_code=409, detail=f"Task is '{status_val}', cannot cancel")

    if status_val == "queued":
        # Remove from Redis queue and mark cancelled
        from service.app import get_scheduler
        scheduler = get_scheduler()
        await scheduler.remove_task(task.task_id, task.model_tier)

    # Mark cancelled in DB (for running tasks, worker will check on next iteration)
    task.status = TaskStatus.cancelled
    task.completed_at = __import__("datetime").datetime.utcnow()
    await session.commit()

    # Publish cancel signal for running tasks
    if status_val == "running":
        from service.app import get_scheduler
        scheduler = get_scheduler()
        await scheduler.publish_cancel(task.task_id)

    return {"cancelled": task.task_id, "was": status_val}


@router.post("/cancel-all")
async def cancel_all_queued(session: AsyncSession = Depends(get_session)):
    """Cancel all queued tasks. Running tasks are not affected."""
    from sqlalchemy import update
    import datetime

    # Clear Redis queues
    from service.app import get_scheduler
    scheduler = get_scheduler()
    await scheduler.clear_queues()

    # Mark all queued tasks as cancelled in DB
    result = await session.execute(
        update(GpuTask)
        .where(GpuTask.status == TaskStatus.queued)
        .values(status=TaskStatus.cancelled, completed_at=datetime.datetime.utcnow())
    )
    await session.commit()

    return {"cancelled_count": result.rowcount}
