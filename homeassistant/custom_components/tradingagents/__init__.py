"""TradingAgents integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, VERSION
from .coordinator import TradingAgentsCoordinator

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]
CARD_JS_PATH = Path(__file__).parent / "www" / "tradingagents-proposals-card.js"
CARD_URL = f"/{DOMAIN}/tradingagents-proposals-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.http.register_static_path(CARD_URL, str(CARD_JS_PATH), cache_headers=False)
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"http://{host}:{port}"
    session_cookie = entry.data.get("session_cookie")

    coordinator = TradingAgentsCoordinator(hass, base_url, session_cookie)

    # If we have a password but cookie may have expired, re-login
    password = entry.data.get(CONF_PASSWORD)
    if password and not session_cookie:
        session_cookie = await coordinator.async_login(password)
        coordinator.session_cookie = session_cookie

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
