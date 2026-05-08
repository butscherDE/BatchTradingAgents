"""Stream lifecycle manager — starts/stops all data streams."""

import logging
from typing import Callable, Awaitable, Optional

from service.config import ServiceConfig
from service.streams.alpaca_news import AlpacaNewsStream
from service.streams.alpaca_prices import AlpacaPriceStream


logger = logging.getLogger(__name__)


class StreamManager:
    def __init__(
        self,
        config: ServiceConfig,
        on_news: Callable[[dict], Awaitable[None]],
        on_price_bar: Optional[Callable[[dict], Awaitable[None]]] = None,
    ):
        self._config = config
        self._on_news = on_news
        self._on_price_bar = on_price_bar
        self._news_streams: list[AlpacaNewsStream] = []
        self._price_streams: list[AlpacaPriceStream] = []

    async def start(self):
        for name, account in self._config.accounts.items():
            if not account.api_key or not account.api_secret:
                logger.warning(f"Account {name}: missing credentials, skipping streams")
                continue

            # News stream
            news_stream = AlpacaNewsStream(
                api_key=account.api_key,
                api_secret=account.api_secret,
                symbols=self._config.news_symbols,
                on_news=self._on_news,
            )
            await news_stream.start()
            self._news_streams.append(news_stream)
            logger.info(f"Started news stream for account: {name}")

            # Price stream (if handler provided and we have symbols to watch)
            if self._on_price_bar:
                price_symbols = self._get_price_symbols(account)
                if price_symbols:
                    price_stream = AlpacaPriceStream(
                        api_key=account.api_key,
                        api_secret=account.api_secret,
                        symbols=price_symbols,
                        on_bar=self._on_price_bar,
                    )
                    await price_stream.start()
                    self._price_streams.append(price_stream)
                    logger.info(f"Started price stream for account {name}: {price_symbols}")

    def _get_price_symbols(self, account) -> list[str]:
        """Get symbols to monitor prices for (held positions)."""
        try:
            from alpaca.trading.client import TradingClient
            client = TradingClient(account.api_key, account.api_secret, paper=account.is_paper)
            positions = client.get_all_positions()
            return [p.symbol for p in positions]
        except Exception as e:
            logger.warning(f"Could not fetch positions for price stream: {e}")
            return []

    async def stop(self):
        for stream in self._news_streams:
            await stream.stop()
        for stream in self._price_streams:
            await stream.stop()
        self._news_streams.clear()
        self._price_streams.clear()
        logger.info("All streams stopped")
