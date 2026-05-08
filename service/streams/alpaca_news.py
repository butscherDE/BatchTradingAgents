"""Alpaca News WebSocket stream consumer."""

import asyncio
import logging
from typing import Callable, Awaitable

from alpaca.data.live import NewsDataStream


logger = logging.getLogger(__name__)


class AlpacaNewsStream:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: list[str],
        on_news: Callable[[dict], Awaitable[None]],
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = symbols
        self._on_news = on_news
        self._stream: NewsDataStream | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self):
        self._loop = asyncio.get_event_loop()
        self._stream = NewsDataStream(self._api_key, self._api_secret)

        on_news = self._on_news
        loop = self._loop

        async def _news_handler(news):
            article = {
                "alpaca_id": str(news.id) if hasattr(news, "id") else None,
                "headline": news.headline,
                "summary": getattr(news, "summary", None) or "",
                "source": getattr(news, "source", None) or "",
                "symbols": list(news.symbols) if hasattr(news, "symbols") else [],
                "published_at": news.created_at.isoformat() if hasattr(news, "created_at") and news.created_at else None,
            }
            try:
                await on_news(article)
            except Exception:
                logger.exception("Error handling news article")

        self._stream.subscribe_news(_news_handler, *self._symbols)
        self._task = asyncio.create_task(self._run_stream())
        logger.info(f"News stream started for symbols: {self._symbols}")

    async def _run_stream(self):
        try:
            await self._stream._run_forever()
        except Exception:
            logger.exception("News stream connection error")

    async def stop(self):
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("News stream stopped")
