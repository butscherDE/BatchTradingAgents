"""Stream lifecycle manager — starts/stops all data streams."""

import logging
from typing import Callable, Awaitable

from service.config import ServiceConfig
from service.streams.alpaca_news import AlpacaNewsStream


logger = logging.getLogger(__name__)


class StreamManager:
    def __init__(self, config: ServiceConfig, on_news: Callable[[dict], Awaitable[None]]):
        self._config = config
        self._on_news = on_news
        self._news_streams: list[AlpacaNewsStream] = []

    async def start(self):
        for name, account in self._config.accounts.items():
            if not account.api_key or not account.api_secret:
                logger.warning(f"Account {name}: missing credentials, skipping news stream")
                continue

            stream = AlpacaNewsStream(
                api_key=account.api_key,
                api_secret=account.api_secret,
                symbols=self._config.news_symbols,
                on_news=self._on_news,
            )
            await stream.start()
            self._news_streams.append(stream)
            logger.info(f"Started news stream for account: {name}")

    async def stop(self):
        for stream in self._news_streams:
            await stream.stop()
        self._news_streams.clear()
        logger.info("All streams stopped")
