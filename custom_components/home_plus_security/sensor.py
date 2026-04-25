"""Sensor platform for Home + Security."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN


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
        self._entry_id = entry_id

    @property
    def _bncx_id(self) -> str | None:
        bncx_home = self.coordinator.data.get("bncx_home", {})
        value = bncx_home.get("id")
        if isinstance(value, str) and value:
            return value
        return None

    @property
    def device_info(self) -> DeviceInfo | None:
        bncx_id = self._bncx_id
        if not bncx_id:
            return None

        bncx_home = self.coordinator.data.get("bncx_home", {})
        bncx_status = self.coordinator.data.get("bncx_status", {})

        name = str(bncx_home.get("name", "Classe 300EOS"))
        sw_version = bncx_status.get("firmware_name")
        hw_version = bncx_status.get("hardware_version")

        return DeviceInfo(
            identifiers={(DOMAIN, bncx_id)},
            manufacturer="BTicino / Netatmo",
            model="Classe 300EOS (BNCX)",
            name=name,
            sw_version=str(sw_version) if sw_version is not None else None,
            hw_version=str(hw_version) if hw_version is not None else None,
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
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_bncx_uptime"

    @property
    def native_value(self) -> int | None:
        value = self.coordinator.data.get("bncx_status", {}).get("uptime")
        if isinstance(value, int):
            return value
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
