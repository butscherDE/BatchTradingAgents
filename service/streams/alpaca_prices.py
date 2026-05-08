"""Alpaca Price WebSocket stream consumer."""

import asyncio
import logging
from typing import Callable, Awaitable

from alpaca.data.live import StockDataStream


logger = logging.getLogger(__name__)


class AlpacaPriceStream:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: list[str],
        on_bar: Callable[[dict], Awaitable[None]],
        on_quote: Callable[[dict], Awaitable[None]] | None = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = symbols
        self._on_bar = on_bar
        self._on_quote = on_quote
        self._stream: StockDataStream | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._stream = StockDataStream(self._api_key, self._api_secret)

        on_bar = self._on_bar

        async def _bar_handler(bar):
            data = {
                "symbol": bar.symbol,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume),
                "timestamp": bar.timestamp.isoformat() if bar.timestamp else None,
            }
            try:
                await on_bar(data)
            except Exception:
                logger.exception(f"Error handling bar for {bar.symbol}")

        self._stream.subscribe_bars(_bar_handler, *self._symbols)

        if self._on_quote:
            on_quote = self._on_quote

            async def _quote_handler(quote):
                data = {
                    "symbol": quote.symbol,
                    "bid_price": float(quote.bid_price) if quote.bid_price else None,
                    "ask_price": float(quote.ask_price) if quote.ask_price else None,
                    "bid_size": int(quote.bid_size) if quote.bid_size else None,
                    "ask_size": int(quote.ask_size) if quote.ask_size else None,
                    "timestamp": quote.timestamp.isoformat() if quote.timestamp else None,
                }
                try:
                    await on_quote(data)
                except Exception:
                    logger.exception(f"Error handling quote for {quote.symbol}")

            self._stream.subscribe_quotes(_quote_handler, *self._symbols)

        self._task = asyncio.create_task(self._run_stream())
        logger.info(f"Price stream started for: {self._symbols}")

    async def _run_stream(self):
        try:
            await asyncio.to_thread(self._stream._run_forever)
        except Exception:
            logger.exception("Price stream connection error")

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
        logger.info("Price stream stopped")

    def update_symbols(self, symbols: list[str]):
        """Update the symbol subscription list (requires restart)."""
        self._symbols = symbols
