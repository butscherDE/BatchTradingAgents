"""News feed API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from service.db.models import NewsArticle
from service.models.schemas import NewsArticleResponse

router = APIRouter(prefix="/api/news", tags=["news"])


async def get_session():
    from service.app import get_db_session
    async with get_db_session() as session:
        yield session


@router.get("", response_model=list[NewsArticleResponse])
async def list_news(
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None),
    symbol: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    query = select(NewsArticle).order_by(NewsArticle.received_at.desc())

    if status:
        query = query.where(NewsArticle.status == status)
    if symbol:
        query = query.where(NewsArticle.symbols.contains(symbol))

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    articles = result.scalars().all()

    return [
        NewsArticleResponse(
            id=a.id,
            alpaca_id=a.alpaca_id,
            headline=a.headline,
            summary=a.summary,
            source=a.source,
            symbols=a.symbols or [],
            published_at=a.published_at,
            received_at=a.received_at,
            status=a.status.value if hasattr(a.status, "value") else a.status,
            quick_result=a.quick_result,
            deep_result=a.deep_result,
            escalation_reason=a.escalation_reason,
        )
        for a in articles
    ]


@router.get("/{article_id}", response_model=NewsArticleResponse)
async def get_news_article(article_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(NewsArticle).where(NewsArticle.id == article_id)
    )
    article = result.scalar_one_or_none()
    if article is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Article not found")

    return NewsArticleResponse(
        id=article.id,
        alpaca_id=article.alpaca_id,
        headline=article.headline,
        summary=article.summary,
        source=article.source,
        symbols=article.symbols or [],
        published_at=article.published_at,
        received_at=article.received_at,
        status=article.status.value if hasattr(article.status, "value") else article.status,
        quick_result=article.quick_result,
        deep_result=article.deep_result,
        escalation_reason=article.escalation_reason,
    )


@router.get("/{article_id}/state")
async def get_news_state(article_id: int, session: AsyncSession = Depends(get_session)):
    """Dump the full raw state of a news article for debugging."""
    result = await session.execute(
        select(NewsArticle).where(NewsArticle.id == article_id)
    )
    article = result.scalar_one_or_none()
    if article is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Article not found")

    return {
        "id": article.id,
        "alpaca_id": article.alpaca_id,
        "headline": article.headline,
        "summary": article.summary,
        "source": article.source,
        "symbols": article.symbols,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "received_at": article.received_at.isoformat() if article.received_at else None,
        "status": article.status.value if hasattr(article.status, "value") else article.status,
        "quick_result": article.quick_result,
        "deep_result": article.deep_result,
        "escalation_reason": article.escalation_reason,
    }


class InjectNewsRequest(BaseModel):
    headline: str
    summary: str = ""
    symbols: list[str] = []
    source: str = "manual"


from pydantic import BaseModel


@router.post("", response_model=NewsArticleResponse, status_code=201)
async def inject_news(body: InjectNewsRequest, session: AsyncSession = Depends(get_session)):
    """Manually inject a news article (for testing/debugging)."""
    import datetime
    from service.db.models import InvestigationStatus

    article = NewsArticle(
        headline=body.headline,
        summary=body.summary or None,
        source=body.source,
        symbols=body.symbols,
        received_at=datetime.datetime.utcnow(),
        status=InvestigationStatus.queued,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    # Submit to GPU queue if scheduler is available
    try:
        from service.app import _scheduler
        from service.core.gpu_scheduler import TaskSpec
        from service.db.models import GpuTask, TaskStatus

        if _scheduler:
            task_id = await _scheduler.submit(TaskSpec(
                model_tier="quick",
                task_type="news_screen",
                payload={
                    "article_id": article.id,
                    "headline": body.headline,
                    "summary": body.summary,
                    "symbols": body.symbols,
                },
                ticker=body.symbols[0] if body.symbols else None,
            ))
            db_task = GpuTask(
                task_id=task_id,
                model_tier="quick",
                task_type="news_screen",
                ticker=body.symbols[0] if body.symbols else None,
                priority=1,
                status=TaskStatus.queued,
                payload={"article_id": article.id},
            )
            session.add(db_task)
            await session.commit()
    except Exception:
        pass

    return NewsArticleResponse(
        id=article.id,
        alpaca_id=None,
        headline=article.headline,
        summary=article.summary,
        source=article.source,
        symbols=article.symbols or [],
        published_at=None,
        received_at=article.received_at,
        status=article.status.value,
        quick_result=None,
        deep_result=None,
        escalation_reason=None,
    )
