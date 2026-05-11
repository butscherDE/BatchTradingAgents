"""Sensor platform for TradingAgents."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TradingAgentsCoordinator

SENSORS = [
    {
        "key": "worker_state",
        "name": "Worker State",
        "icon": "mdi:robot",
    },
    {
        "key": "queue_depth",
        "name": "Task Queue",
        "icon": "mdi:tray-full",
        "unit": "tasks",
    },
    {
        "key": "alpaca_status",
        "name": "Alpaca Stream",
        "icon": "mdi:access-point",
    },
    {
        "key": "yfinance_status",
        "name": "yfinance Poller",
        "icon": "mdi:newspaper-variant",
    },
    {
        "key": "pending_proposals",
        "name": "Pending Proposals",
        "icon": "mdi:hand-coin",
        "unit": "proposals",
    },
]


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
    async_add_entities(
        TradingAgentsSensor(coordinator, entry, sensor) for sensor in SENSORS
    )


class TradingAgentsSensor(CoordinatorEntity[TradingAgentsCoordinator], SensorEntity):
    """A sensor for a TradingAgents data point."""

    def __init__(
        self,
        coordinator: TradingAgentsCoordinator,
        entry: ConfigEntry,
        sensor_def: dict,
    ) -> None:
        super().__init__(coordinator)
        self._key = sensor_def["key"]
        self._attr_name = f"TradingAgents {sensor_def['name']}"
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_icon = sensor_def.get("icon")
        self._attr_native_unit_of_measurement = sensor_def.get("unit")
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._key)
