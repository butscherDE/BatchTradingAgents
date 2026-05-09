"""News source status endpoint."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("/news-sources")
async def get_news_source_status():
    from service.app import _news_source_health
    return _news_source_health
