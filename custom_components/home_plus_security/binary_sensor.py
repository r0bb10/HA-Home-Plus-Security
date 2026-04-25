"""Binary sensor platform for Home + Security."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up binary sensors for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([HomePlusSecurityWebsocketConnectedBinarySensor(coordinator, entry.entry_id)])


class HomePlusSecurityWebsocketConnectedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Websocket connected status."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Websocket Connected"
    _attr_icon = "mdi:web"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_bncx_websocket_connected"

    @property
    def device_info(self) -> DeviceInfo | None:
        bncx_home = self.coordinator.data.get("bncx_home", {})
        bncx_status = self.coordinator.data.get("bncx_status", {})
        bncx_id = bncx_home.get("id")
        if not isinstance(bncx_id, str) or not bncx_id:
            return None

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

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.get("bncx_status", {}).get("websocket_connected")
        if isinstance(value, bool):
            return value
        return None
