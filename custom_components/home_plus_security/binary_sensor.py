"""Binary sensor platform for Home + Security."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up binary sensors for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            HomePlusSecurityWebsocketConnectedBinarySensor(coordinator, entry.entry_id),
            HomePlusSecurityCloudWebSocketBinarySensor(coordinator, entry.entry_id),
        ]
    )


class HomePlusSecurityWebsocketConnectedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Overall 300EOS online status."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Cloud"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:cloud-check-outline"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_bncx_online"

    @property
    def device_info(self) -> DeviceInfo | None:
        bncx_home = self.coordinator.data.get("bncx_home", {})
        bncx_status = self.coordinator.data.get("bncx_status", {})
        bncx_id = bncx_home.get("id")
        if not isinstance(bncx_id, str) or not bncx_id:
            bncx_id = bncx_status.get("id")
        if not isinstance(bncx_id, str) or not bncx_id:
            bncx_id = getattr(self.coordinator, "home_id", None)
        if not isinstance(bncx_id, str) or not bncx_id:
            return None

        return build_device_info(
            home=self.coordinator.data.get("home", {}),
            bncx_home=bncx_home,
            bncx_status=bncx_status,
            fallback_id=bncx_id,
        )

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.get("bncx_status", {})
        reachable = status.get("reachable")
        if isinstance(reachable, bool):
            return reachable

        return None


class HomePlusSecurityCloudWebSocketBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Cloud websocket connectivity state."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "WebSocket"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:web"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_bncx_websocket_connected"

    @property
    def device_info(self) -> DeviceInfo | None:
        bncx_home = self.coordinator.data.get("bncx_home", {})
        bncx_status = self.coordinator.data.get("bncx_status", {})
        bncx_id = bncx_home.get("id")
        if not isinstance(bncx_id, str) or not bncx_id:
            bncx_id = bncx_status.get("id")
        if not isinstance(bncx_id, str) or not bncx_id:
            bncx_id = getattr(self.coordinator, "home_id", None)
        if not isinstance(bncx_id, str) or not bncx_id:
            return None

        return build_device_info(
            home=self.coordinator.data.get("home", {}),
            bncx_home=bncx_home,
            bncx_status=bncx_status,
            fallback_id=bncx_id,
        )

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.get("bncx_status", {})
        websocket_connected = status.get("websocket_connected")
        if isinstance(websocket_connected, bool):
            return websocket_connected

        return None
