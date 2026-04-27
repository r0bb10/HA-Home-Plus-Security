"""Sensor platform for Home + Security."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN
from .device import build_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            HomePlusSecurityConnectionTypeSensor(coordinator, entry.entry_id),
            HomePlusSecurityWifiStrengthSensor(coordinator, entry.entry_id),
            HomePlusSecurityUptimeSensor(coordinator, entry.entry_id),
            HomePlusSecurityLocalIpSensor(coordinator, entry.entry_id),
        ]
    )


class HomePlusSecurityBaseEntity(CoordinatorEntity):
    """Base Home + Security entity with device binding."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)

    @property
    def _bncx_id(self) -> str | None:
        bncx_home = self.coordinator.data.get("bncx_home", {})
        value = bncx_home.get("id")
        if isinstance(value, str) and value:
            return value
        bncx_status = self.coordinator.data.get("bncx_status", {})
        status_id = bncx_status.get("id")
        if isinstance(status_id, str) and status_id:
            return status_id
        home_id = getattr(self.coordinator, "home_id", None)
        if isinstance(home_id, str) and home_id:
            return home_id
        return None

    @property
    def device_info(self) -> DeviceInfo | None:
        bncx_id = self._bncx_id
        if not bncx_id:
            return None

        return build_device_info(
            home=self.coordinator.data.get("home", {}),
            bncx_home=self.coordinator.data.get("bncx_home", {}),
            bncx_status=self.coordinator.data.get("bncx_status", {}),
            fallback_id=bncx_id,
        )


class HomePlusSecurityConnectionTypeSensor(HomePlusSecurityBaseEntity, SensorEntity):
    """Current device connection type."""

    _attr_name = "Connection Type"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_bncx_connection_type"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get("bncx_status", {}).get("connection")
        return str(value) if value is not None else None


class HomePlusSecurityWifiStrengthSensor(HomePlusSecurityBaseEntity, SensorEntity):
    """Current wifi strength."""

    _attr_name = "WiFi Strength"
    _attr_icon = "mdi:wifi"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_bncx_wifi_strength"

    @property
    def native_value(self) -> int | None:
        value = self.coordinator.data.get("bncx_status", {}).get("wifi_strength")
        if isinstance(value, int):
            return value
        return None


class HomePlusSecurityUptimeSensor(HomePlusSecurityBaseEntity, SensorEntity):
    """Current uptime in seconds."""

    _attr_name = "Uptime"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_bncx_uptime"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get("bncx_status", {}).get("uptime")
        if isinstance(value, int):
            hours = value // 3600
            minutes = (value % 3600) // 60
            return f"{hours}h {minutes}min"
        return None


class HomePlusSecurityLocalIpSensor(HomePlusSecurityBaseEntity, SensorEntity):
    """Current local IPv4."""

    _attr_name = "Local IP Address"
    _attr_icon = "mdi:ip-network-outline"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_bncx_local_ipv4"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get("bncx_status", {}).get("local_ipv4")
        return str(value) if value is not None else None


class HomePlusSecurityWebSocketLastMessageSensor(HomePlusSecurityBaseEntity, SensorEntity):
    """Last websocket message timestamp."""

    _attr_name = "WebSocket Last Message"
    _attr_icon = "mdi:web-clock"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_bncx_ws_last_message"

    @property
    def native_value(self) -> str | None:
        ws_data = self.coordinator.data.get("ws", {})
        value = ws_data.get("last_message_at") if isinstance(ws_data, dict) else None
        return str(value) if value is not None else None


class HomePlusSecurityLastCommandErrorSensor(HomePlusSecurityBaseEntity, SensorEntity):
    """Latest command failure message."""

    _attr_name = "Last Command Error"
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_bncx_last_command_error"

    @property
    def native_value(self) -> str | None:
        ws_data = self.coordinator.data.get("ws", {})
        value = ws_data.get("last_command_error") if isinstance(ws_data, dict) else None
        return str(value) if value is not None else None
