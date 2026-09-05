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

from .const import (
    DATA_COORDINATOR,
    DIAGNOSTIC_DEVICE_REACHABLE,
    DIAGNOSTIC_DEVICE_WEBSOCKET,
    DIAGNOSTIC_PUSH_WEBSOCKET,
    DIAGNOSTIC_WEBSOCKET_STALE,
    DOMAIN,
)
from .device import build_device_info
from .entity_options import remove_unselected_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    optional_entities = {
        DIAGNOSTIC_DEVICE_REACHABLE: HomePlusSecurityWebsocketConnectedBinarySensor(
            coordinator, entry.entry_id
        ),
        DIAGNOSTIC_PUSH_WEBSOCKET: HomePlusSecurityCloudWebSocketBinarySensor(
            coordinator, entry.entry_id
        ),
        DIAGNOSTIC_WEBSOCKET_STALE: HomePlusSecurityCloudWebSocketStaleBinarySensor(
            coordinator, entry.entry_id
        ),
        DIAGNOSTIC_DEVICE_WEBSOCKET: HomePlusSecurityDeviceWebSocketBinarySensor(
            coordinator, entry.entry_id
        ),
    }
    selected = remove_unselected_entities(
        hass,
        entry,
        "binary_sensor",
        {option: entity.unique_id for option, entity in optional_entities.items()},
    )
    entities = [entity for option, entity in optional_entities.items() if option in selected]
    async_add_entities(entities)


class HomePlusSecurityWebsocketConnectedBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Overall 300EOS online status."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Device Reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:lan-connect"

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
    _attr_name = "Push WebSocket"
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
        runtime_connected = getattr(self.coordinator, "ws_connected", None)
        if isinstance(runtime_connected, bool):
            return runtime_connected

        status = self.coordinator.data.get("bncx_status", {})
        websocket_connected = status.get("websocket_connected")
        if isinstance(websocket_connected, bool):
            return websocket_connected

        return None


class HomePlusSecurityCloudWebSocketStaleBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Cloud websocket stale state."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "WebSocket Stale"
    _attr_icon = "mdi:web-clock"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_bncx_websocket_stale"

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
        ws_stale = getattr(self.coordinator, "ws_stale", None)
        if isinstance(ws_stale, bool):
            return ws_stale
        return None


class HomePlusSecurityDeviceWebSocketBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Vendor-reported BNCX websocket connection state."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Device WebSocket"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:access-point-network"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_bncx_device_websocket_connected"

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
