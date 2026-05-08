"""Fixed-window batch debounce for merge/allocation triggers."""

import asyncio
import datetime
from collections import defaultdict
from typing import Callable, Awaitable


class MergeDebouncer:
    def __init__(self, debounce_seconds: int, on_fire: Callable[[str, list[str]], Awaitable[None]]):
        self._debounce_seconds = debounce_seconds
        self._on_fire = on_fire
        self._pending: dict[str, set[str]] = defaultdict(set)
        self._timers: dict[str, asyncio.Task] = {}

    async def ticker_updated(self, account_id: str, ticker: str):
        self._pending[account_id].add(ticker)

        if account_id not in self._timers:
            self._timers[account_id] = asyncio.create_task(
                self._wait_and_fire(account_id)
            )

    async def _wait_and_fire(self, account_id: str):
        await asyncio.sleep(self._debounce_seconds)

        tickers = list(self._pending.pop(account_id, set()))
        self._timers.pop(account_id, None)

        if tickers:
            await self._on_fire(account_id, tickers)

    async def cancel_all(self):
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()
        self._pending.clear()
