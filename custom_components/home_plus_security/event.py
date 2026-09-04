"""Event platform for Home + Security."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import DoorbellEventType, EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    """Set up the doorbell event entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([HomePlusSecurityDoorbellEvent(coordinator, entry.entry_id)])


class HomePlusSecurityDoorbellEvent(CoordinatorEntity, EventEntity):
    """Expose a normalized incoming call as a standard doorbell ring."""

    _attr_has_entity_name = True
    _attr_name = "Doorbell"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [DoorbellEventType.RING]

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_doorbell"
        self._last_ring_id = self._current_ring_id()

    @property
    def device_info(self) -> DeviceInfo | None:
        bncx_home = self.coordinator.data.get("bncx_home", {})
        bncx_status = self.coordinator.data.get("bncx_status", {})
        bncx_id = bncx_home.get("id") or bncx_status.get("id")
        if not isinstance(bncx_id, str) or not bncx_id:
            return None
        return build_device_info(
            home=self.coordinator.data.get("home", {}),
            bncx_home=bncx_home,
            bncx_status=bncx_status,
            fallback_id=bncx_id,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        ring_id = self._current_ring_id()
        if ring_id and ring_id != self._last_ring_id:
            self._last_ring_id = ring_id
            last_event = self._last_push_event()
            self._trigger_event(
                DoorbellEventType.RING,
                {
                    "module_id": last_event.get("module_id"),
                    "session_id": last_event.get("session_id"),
                },
            )
        self.async_write_ha_state()

    def _current_ring_id(self) -> str | None:
        last_event = self._last_push_event()
        ring_id = last_event.get("ring_id")
        return ring_id if last_event.get("type") == "incoming_call" and isinstance(ring_id, str) else None

    def _last_push_event(self) -> dict[str, Any]:
        push = self.coordinator.data.get("push", {})
        last_event = push.get("last_event") if isinstance(push, dict) else None
        return last_event if isinstance(last_event, dict) else {}
