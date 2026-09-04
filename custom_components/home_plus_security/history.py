"""Persistent raw-image history for Home + Security push events."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    HISTORY_MAX_EVENTS,
    HISTORY_MAX_IMAGE_BYTES,
    HISTORY_RETENTION_DAYS,
    HISTORY_STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)
_IMAGE_KINDS = ("snapshot", "vignette")
_DOWNLOAD_TIMEOUT = ClientTimeout(total=15)
_MIME_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class HomePlusSecurityEventHistory:
    """Persist a bounded set of push event images and metadata."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass, HISTORY_STORAGE_VERSION, f"{DOMAIN}.history.{entry_id}"
        )
        self._root = Path(hass.config.path(DOMAIN, "events", entry_id))
        self._events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._loaded = False

    async def async_load(self) -> None:
        """Load metadata and create the storage root once."""
        if self._loaded:
            return
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            self._events = [event for event in data["events"] if isinstance(event, dict)]
        await self._hass.async_add_executor_job(self._root.mkdir, 0o700, True, True)
        self._loaded = True

    async def async_record_images(
        self,
        *,
        event_id: str,
        module_id: str,
        timestamp: int | float | None,
        snapshot_url: str | None,
        vignette_url: str | None,
    ) -> dict[str, Any]:
        """Store newly received event images before their signed URLs expire."""
        await self.async_load()
        async with self._lock:
            record = next((item for item in self._events if item.get("event_id") == event_id), None)
            if record is None:
                record = {
                    "event_id": event_id,
                    "module_id": module_id,
                    "timestamp": timestamp or datetime.now(UTC).timestamp(),
                    "snapshot_file": None,
                    "snapshot_content_type": None,
                    "vignette_file": None,
                    "vignette_content_type": None,
                }
                self._events.insert(0, record)

            for image_kind, url in (("snapshot", snapshot_url), ("vignette", vignette_url)):
                if not url or record.get(f"{image_kind}_file"):
                    continue
                downloaded = await self._async_download_image(module_id, event_id, image_kind, url)
                if downloaded is None:
                    continue
                filename, content_type = downloaded
                record[f"{image_kind}_file"] = filename
                record[f"{image_kind}_content_type"] = content_type

            await self._async_apply_retention_locked()
            await self._store.async_save({"events": self._events})
            return dict(record)

    async def async_read_image(self, event_id: str, image_kind: str) -> tuple[bytes, str] | None:
        """Read a stored image without exposing its on-disk path."""
        if image_kind not in _IMAGE_KINDS:
            return None
        record = next((item for item in self._events if item.get("event_id") == event_id), None)
        if record is None:
            return None
        filename = record.get(f"{image_kind}_file")
        content_type = record.get(f"{image_kind}_content_type")
        if not isinstance(filename, str) or not isinstance(content_type, str):
            return None
        path = self._safe_path(filename)
        if path is None:
            return None
        try:
            return await self._hass.async_add_executor_job(self._read_image, path, content_type)
        except OSError:
            _LOGGER.debug("Unable to read stored %s image for event %s", image_kind, event_id)
            return None

    async def _async_download_image(
        self, module_id: str, event_id: str, image_kind: str, url: str
    ) -> tuple[str, str] | None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            _LOGGER.warning("Rejected non-HTTPS %s URL for event %s", image_kind, event_id)
            return None

        try:
            async with async_get_clientsession(self._hass).get(url, timeout=_DOWNLOAD_TIMEOUT) as response:
                if response.status != 200:
                    _LOGGER.debug("%s image download returned HTTP %s", image_kind, response.status)
                    return None
                content_type = response.content_type.lower()
                if content_type not in _MIME_SUFFIXES:
                    _LOGGER.warning("Rejected %s image with content type %s", image_kind, content_type)
                    return None
                content = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    content.extend(chunk)
                    if len(content) > HISTORY_MAX_IMAGE_BYTES:
                        _LOGGER.warning("Rejected oversized %s image for event %s", image_kind, event_id)
                        return None
        except asyncio.CancelledError:
            raise
        except (TimeoutError, ClientError) as err:
            _LOGGER.debug("Unable to download %s image for event %s: %s", image_kind, event_id, err)
            return None

        safe_module = _safe_component(module_id)
        safe_event = _safe_component(event_id)
        filename = f"{safe_module}/{safe_event}_{image_kind}{_MIME_SUFFIXES[content_type]}"
        path = self._safe_path(filename)
        if path is None:
            return None
        try:
            await self._hass.async_add_executor_job(self._write_image, path, bytes(content))
        except OSError as err:
            _LOGGER.warning("Unable to store %s image for event %s: %s", image_kind, event_id, err)
            return None
        return filename, content_type

    async def _async_apply_retention_locked(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=HISTORY_RETENTION_DAYS)).timestamp()
        retained: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        for event in sorted(self._events, key=lambda item: item.get("timestamp") or 0, reverse=True):
            if len(retained) < HISTORY_MAX_EVENTS and (event.get("timestamp") or 0) >= cutoff:
                retained.append(event)
            else:
                removed.append(event)
        self._events = retained
        for event in removed:
            for image_kind in _IMAGE_KINDS:
                filename = event.get(f"{image_kind}_file")
                if isinstance(filename, str) and (path := self._safe_path(filename)) is not None:
                    await self._hass.async_add_executor_job(self._unlink, path)

    def _safe_path(self, filename: str) -> Path | None:
        path = self._root / filename
        try:
            path.resolve().relative_to(self._root.resolve())
        except ValueError:
            return None
        return path

    @staticmethod
    def _read_image(path: Path, content_type: str) -> tuple[bytes, str]:
        return path.read_bytes(), content_type

    @staticmethod
    def _write_image(path: Path, content: bytes) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)

    @staticmethod
    def _unlink(path: Path) -> None:
        path.unlink(missing_ok=True)


def _safe_component(value: str) -> str:
    """Convert an external ID into one safe path component."""
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
