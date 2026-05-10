"""Config flow for TradingAgents integration."""

from __future__ import annotations

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_PASSWORD

from .const import DOMAIN, DEFAULT_PORT


class TradingAgentsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TradingAgents."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            password = user_input.get(CONF_PASSWORD, "")
            base_url = f"http://{host}:{port}"

            try:
                session_cookie = None
                async with aiohttp.ClientSession() as session:
                    # Try auth check first
                    async with session.get(f"{base_url}/api/auth/check") as resp:
                        if resp.status == 401 and password:
                            # Need to login
                            async with session.post(
                                f"{base_url}/api/auth/login",
                                json={"password": password},
                            ) as login_resp:
                                if login_resp.status != 200:
                                    errors["base"] = "invalid_auth"
                                else:
                                    cookie = login_resp.cookies.get("ta_session")
                                    session_cookie = cookie.value if cookie else None
                        elif resp.status == 401:
                            errors["base"] = "invalid_auth"

                    if not errors:
                        # Verify we can reach the stats endpoint
                        cookies = {"ta_session": session_cookie} if session_cookie else {}
                        async with aiohttp.ClientSession(cookies=cookies) as auth_session:
                            async with auth_session.get(f"{base_url}/api/tasks/stats") as resp:
                                if resp.status != 200:
                                    errors["base"] = "cannot_connect"

            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"

            if not errors:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"TradingAgents ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_PASSWORD: password,
                        "session_cookie": session_cookie,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default="10.0.0.217"): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_PASSWORD, default=""): str,
            }),
            errors=errors,
        )
