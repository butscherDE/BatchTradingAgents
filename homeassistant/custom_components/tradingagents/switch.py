"""Switch platform for TradingAgents — worker pause/resume."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TradingAgentsCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TradingAgentsCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([WorkerSwitch(coordinator, entry)])


class WorkerSwitch(CoordinatorEntity[TradingAgentsCoordinator], SwitchEntity):
    """Switch to pause/resume the GPU worker."""

    def __init__(self, coordinator: TradingAgentsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "TradingAgents Worker"
        self._attr_unique_id = f"{entry.entry_id}_worker_switch"
        self._attr_icon = "mdi:play-pause"

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
