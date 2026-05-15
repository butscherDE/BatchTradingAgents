"""Stream lifecycle manager — starts/stops all data streams."""

import logging
from typing import Callable, Awaitable, Optional

from service.config import ServiceConfig
from service.streams.alpaca_news import AlpacaNewsStream


logger = logging.getLogger(__name__)


class StreamManager:
    def __init__(
        self,
        config: ServiceConfig,
        on_news: Callable[[dict], Awaitable[None]],
        on_price_bar: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_stream_status: Optional[Callable[[str, str | None], None]] = None,
    ):
        self._config = config
        self._on_news = on_news
        self._on_price_bar = on_price_bar
        self._on_stream_status = on_stream_status
        self._news_stream: AlpacaNewsStream | None = None

    async def start(self):
        # Alpaca allows only 1 WebSocket connection per feed per API key.
        # Use the first account's credentials for a single shared news stream.
        for name, account in self._config.accounts.items():
            if not account.api_key or not account.api_secret:
                logger.warning(f"Account {name}: missing credentials, skipping streams")
                continue

            self._news_stream = AlpacaNewsStream(
                api_key=account.api_key,
                api_secret=account.api_secret,
                symbols=["*"],
                on_news=self._on_news,
                on_status=self._on_stream_status,
            )
            await self._news_stream.start()
            logger.info(f"Started shared news stream using account: {name}")
            break  # Only one stream — Alpaca connection limit

    async def stop(self):
        if self._news_stream:
            await self._news_stream.stop()
            self._news_stream = None
        logger.info("All streams stopped")
