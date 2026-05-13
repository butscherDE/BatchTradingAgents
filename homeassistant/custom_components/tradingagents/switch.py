"""Switch platform for TradingAgents — worker pause/resume."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TradingAgentsCoordinator


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"TradingAgents ({host}:{port})",
        manufacturer="BatchTradingAgents",
        model="Service",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TradingAgentsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WorkerSwitch(coordinator, entry),
        ProviderSwitch(coordinator, entry, "local"),
        ProviderSwitch(coordinator, entry, "deepinfra"),
    ])


class WorkerSwitch(CoordinatorEntity[TradingAgentsCoordinator], SwitchEntity):
    """Switch to pause/resume all workers."""

    def __init__(self, coordinator: TradingAgentsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "TradingAgents Worker"
        self._attr_unique_id = f"{entry.entry_id}_worker_switch"
        self._attr_icon = "mdi:play-pause"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        state = self.coordinator.data.get("worker_state")
        return state not in ("paused", "pausing")

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_resume_worker()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_pause_worker()


class ProviderSwitch(CoordinatorEntity[TradingAgentsCoordinator], SwitchEntity):
    """Switch to pause/resume a specific provider."""

    def __init__(self, coordinator: TradingAgentsCoordinator, entry: ConfigEntry, provider: str) -> None:
        super().__init__(coordinator)
        self._provider = provider
        self._attr_name = f"TradingAgents {provider.title()} Provider"
        self._attr_unique_id = f"{entry.entry_id}_provider_{provider}_switch"
        self._attr_icon = "mdi:server" if provider == "local" else "mdi:cloud"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        state = self.coordinator.data.get(f"provider_{self._provider}_state")
        if state is None:
            return None
        return state not in ("paused", "pausing", "offline")

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_resume_worker(self._provider)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_pause_worker(self._provider)
