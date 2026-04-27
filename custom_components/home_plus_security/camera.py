"""Camera platform for Home + Security."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN, IMAGE_CACHE_SECONDS
from .device import build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up event cameras for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities(
        [
            HomePlusSecurityEventCamera(coordinator, entry.entry_id, image_type="snapshot"),
            HomePlusSecurityEventCamera(coordinator, entry.entry_id, image_type="vignette"),
        ]
    )


class HomePlusSecurityEventCamera(CoordinatorEntity, Camera):
    """Last event image camera."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature(0)

    def __init__(self, coordinator, entry_id: str, *, image_type: str) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self._image_type = image_type
        self._attr_name = "Last Snapshot" if image_type == "snapshot" else "Last Vignette"
        self._attr_unique_id = f"{entry_id}_{image_type}_camera"
        self._image_url: str | None = None
        self._image_expires_at: datetime | None = None
        self._event_time: datetime | None = None
        self._cached_image: bytes | None = None
        self._cached_image_time: datetime | None = None
        self._update_state()

    @property
    def available(self) -> bool:
        return super().available and self._image_url is not None

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
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = {
            "image_url": self._image_url,
            "expires_at": self._image_expires_at.isoformat() if self._image_expires_at else None,
            "event_time": self._event_time.isoformat() if self._event_time else None,
        }
        return {key: value for key, value in attrs.items() if value is not None}

    @callback
    def _handle_coordinator_update(self) -> None:
        previous = self._image_url
        self._update_state()
        if previous != self._image_url:
            self._cached_image = None
            self._cached_image_time = None
        self.async_write_ha_state()

    def _update_state(self) -> None:
        events = self.coordinator.data.get("events", [])
        if not isinstance(events, list):
            self._image_url = None
            self._image_expires_at = None
            self._event_time = None
            return

        image_url: str | None = None
        expires_at: datetime | None = None
        event_time: datetime | None = None

        for event in events:
            if not isinstance(event, dict):
                continue
            subevents = event.get("subevents")
            if not isinstance(subevents, list) or not subevents:
                continue
            first_subevent = subevents[0]
            if not isinstance(first_subevent, dict):
                continue
            image_data = first_subevent.get(self._image_type)
            if not isinstance(image_data, dict):
                continue
            url = image_data.get("url")
            if not isinstance(url, str) or not url:
                continue

            image_url = url
            expires_raw = image_data.get("expires_at")
            if isinstance(expires_raw, (int, float)) and expires_raw > 0:
                expires_at = datetime.fromtimestamp(expires_raw, UTC)

            event_time_raw = first_subevent.get("time") or event.get("time")
            if isinstance(event_time_raw, (int, float)) and event_time_raw > 0:
                event_time = datetime.fromtimestamp(event_time_raw, UTC)
            break

        self._image_url = image_url
        self._image_expires_at = expires_at
        self._event_time = event_time

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        now = datetime.now(UTC)
        if (
            self._cached_image is not None
            and self._cached_image_time is not None
            and (now - self._cached_image_time).total_seconds() < IMAGE_CACHE_SECONDS
        ):
            return self._cached_image

        if not self._image_url:
            return None
        if self._image_expires_at is not None and now >= self._image_expires_at:
            return None

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self._image_url) as response:
                response.raise_for_status()
                content = await response.read()
        except ClientError as err:
            _LOGGER.debug("Failed to fetch %s camera image: %s", self._image_type, err)
            return None

        self._cached_image = content
        self._cached_image_time = now
        return content
