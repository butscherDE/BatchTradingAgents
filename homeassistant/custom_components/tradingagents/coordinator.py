"""DataUpdateCoordinator for TradingAgents."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class TradingAgentsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch data from TradingAgents API."""

    def __init__(self, hass: HomeAssistant, base_url: str, session_cookie: str | None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="TradingAgents",
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.base_url = base_url.rstrip("/")
        self.session_cookie = session_cookie

    def _cookies(self) -> dict[str, str]:
        if self.session_cookie:
            return {"ta_session": self.session_cookie}
        return {}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(cookies=self._cookies()) as session:
                stats = await self._get(session, "/api/tasks/stats")
                news_sources = await self._get(session, "/api/status/news-sources")
                proposals = await self._get(session, "/api/proposals?status=pending")

            return {
                "worker_state": stats.get("worker_state"),
                "queue_depth": stats.get("queue_depth", 0),
                "alpaca_status": news_sources.get("alpaca", {}).get("status", "unknown"),
                "yfinance_status": news_sources.get("yfinance", {}).get("status", "unknown"),
                "yfinance_failures": news_sources.get("yfinance", {}).get("consecutive_failures", 0),
                "pending_proposals": len(proposals) if isinstance(proposals, list) else 0,
            }
        except Exception as err:
            raise UpdateFailed(f"Error fetching TradingAgents data: {err}") from err

    async def _get(self, session: aiohttp.ClientSession, path: str) -> Any:
        async with session.get(f"{self.base_url}{path}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def async_pause_worker(self) -> None:
        async with aiohttp.ClientSession(cookies=self._cookies()) as session:
            async with session.post(f"{self.base_url}/api/tasks/pause") as resp:
                resp.raise_for_status()
        await self.async_request_refresh()

    async def async_resume_worker(self) -> None:
        async with aiohttp.ClientSession(cookies=self._cookies()) as session:
            async with session.post(f"{self.base_url}/api/tasks/resume") as resp:
                resp.raise_for_status()
        await self.async_request_refresh()

    async def async_login(self, password: str) -> str:
        """Login and return session cookie value."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/auth/login",
                json={"password": password},
            ) as resp:
                resp.raise_for_status()
                cookie = resp.cookies.get("ta_session")
                if cookie:
                    return cookie.value
                raise UpdateFailed("Login succeeded but no session cookie returned")
