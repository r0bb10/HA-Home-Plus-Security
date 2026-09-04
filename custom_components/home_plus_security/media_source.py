"""Media Source provider for cached Home + Security event images."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN
from .history import HomePlusSecurityEventHistory

_SOURCE_NAME = "Home + Security Events"
_IMAGE_KINDS = ("snapshot", "vignette")


async def async_get_media_source(hass: HomeAssistant) -> HomePlusSecurityMediaSource:
    """Return the integration's cached-event media source."""
    return HomePlusSecurityMediaSource(hass)


class HomePlusSecurityMediaSource(MediaSource):
    """Browse locally cached doorbell event images."""

    name = _SOURCE_NAME

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve an image to the authenticated local history view."""
        entry_id, _module_id, event_id, image_kind = self._parse_image_identifier(item.identifier)
        history = _get_history(self.hass, entry_id)
        resolved = history.resolve_image_path(event_id, image_kind) if history else None
        if resolved is None:
            raise Unresolvable(f"History image not found: {item.identifier!r}")
        path, content_type = resolved
        if not await self.hass.async_add_executor_job(path.is_file):
            raise Unresolvable(f"History image not found: {item.identifier!r}")
        return PlayMedia(
            url=HomePlusSecurityHistoryImageView.build_url(entry_id, event_id, image_kind),
            mime_type=content_type,
            path=path,
        )

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse entries, modules, events, and their cached images."""
        parts = [part for part in (item.identifier or "").split("/") if part]
        if not parts:
            return self._browse_root()
        if len(parts) == 1:
            return self._browse_entry(parts[0])
        if len(parts) == 2:
            return self._browse_module(parts[0], parts[1])
        if len(parts) == 3:
            return self._browse_event(parts[0], parts[1], parts[2])
        raise BrowseError(f"Invalid Home + Security media identifier: {item.identifier!r}")

    def _browse_root(self) -> BrowseMediaSource:
        children = [
            _directory(entry_id, _entry_title(self.hass, entry_id))
            for entry_id in self.hass.data.get(DOMAIN, {})
            if _get_history(self.hass, entry_id) is not None
        ]
        return _directory(None, _SOURCE_NAME, children=children)

    def _browse_entry(self, entry_id: str) -> BrowseMediaSource:
        history = _require_history(self.hass, entry_id)
        children = [
            _directory(f"{entry_id}/{module_id}", module_id)
            for module_id in history.list_modules()
        ]
        return _directory(entry_id, _entry_title(self.hass, entry_id), children=children)

    def _browse_module(self, entry_id: str, module_id: str) -> BrowseMediaSource:
        history = _require_history(self.hass, entry_id)
        events = history.list_events(module_id)
        children = [
            _directory(
                f"{entry_id}/{module_id}/{event['event_id']}",
                _event_title(event),
                children_media_class=MediaClass.IMAGE,
            )
            for event in events
            if isinstance(event.get("event_id"), str)
        ]
        return _directory(f"{entry_id}/{module_id}", module_id, children=children)

    def _browse_event(self, entry_id: str, module_id: str, event_id: str) -> BrowseMediaSource:
        history = _require_history(self.hass, entry_id)
        event = history.get_event(event_id)
        if event is None or event.get("module_id") != module_id:
            raise BrowseError(f"Event not found: {event_id!r}")
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/{module_id}/{event_id}/{image_kind}",
                media_class=MediaClass.IMAGE,
                media_content_type=event[f"{image_kind}_content_type"],
                title=image_kind.capitalize(),
                can_play=True,
                can_expand=False,
                thumbnail=HomePlusSecurityHistoryImageView.build_url(entry_id, event_id, image_kind),
            )
            for image_kind in _IMAGE_KINDS
            if event.get(f"{image_kind}_file") and isinstance(event.get(f"{image_kind}_content_type"), str)
        ]
        return _directory(f"{entry_id}/{module_id}/{event_id}", _event_title(event), children=children)

    @staticmethod
    def _parse_image_identifier(identifier: str | None) -> tuple[str, str, str, str]:
        parts = (identifier or "").split("/")
        if len(parts) != 4 or parts[3] not in _IMAGE_KINDS or not all(parts):
            raise Unresolvable(f"Invalid Home + Security media identifier: {identifier!r}")
        return parts[0], parts[1], parts[2], parts[3]


class HomePlusSecurityHistoryImageView(HomeAssistantView):
    """Serve local history images behind Home Assistant authentication."""

    url = "/api/home_plus_security/image/{entry_id}/{event_id}/{image_kind}"
    name = "api:home_plus_security:history_image"
    requires_auth = True

    @staticmethod
    def build_url(entry_id: str, event_id: str, image_kind: str) -> str:
        """Return the authenticated history-image URL."""
        return f"/api/home_plus_security/image/{entry_id}/{quote(event_id, safe='')}/{image_kind}"

    async def get(
        self, request: web.Request, entry_id: str, event_id: str, image_kind: str
    ) -> web.StreamResponse:
        """Return a locally cached image or a 404 response."""
        history = _get_history(request.app["hass"], entry_id)
        resolved = history.resolve_image_path(event_id, image_kind) if history else None
        if resolved is None:
            raise web.HTTPNotFound
        path, content_type = resolved
        if not await request.app["hass"].async_add_executor_job(path.is_file):
            raise web.HTTPNotFound
        return web.FileResponse(path, headers={"Content-Type": content_type})


def _get_history(hass: HomeAssistant, entry_id: str) -> HomePlusSecurityEventHistory | None:
    """Return a loaded entry's history store."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    coordinator = entry_data.get(DATA_COORDINATOR) if isinstance(entry_data, dict) else None
    history = getattr(coordinator, "history", None)
    return history if isinstance(history, HomePlusSecurityEventHistory) else None


def _require_history(hass: HomeAssistant, entry_id: str) -> HomePlusSecurityEventHistory:
    history = _get_history(hass, entry_id)
    if history is None:
        raise BrowseError(f"Home + Security entry is not loaded: {entry_id!r}")
    return history


def _directory(
    identifier: str | None,
    title: str,
    *,
    children: list[BrowseMediaSource] | None = None,
    children_media_class: MediaClass = MediaClass.DIRECTORY,
) -> BrowseMediaSource:
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=identifier,
        media_class=MediaClass.DIRECTORY,
        media_content_type=MediaType.IMAGE,
        title=title,
        can_play=False,
        can_expand=True,
        children=children,
        children_media_class=children_media_class,
    )


def _entry_title(hass: HomeAssistant, entry_id: str) -> str:
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry.title if entry and entry.title else entry_id


def _event_title(event: dict[str, Any]) -> str:
    timestamp = event.get("timestamp")
    if isinstance(timestamp, (int, float)):
        timestamp_text = datetime.fromtimestamp(timestamp, UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    else:
        timestamp_text = "Unknown time"
    return f"{timestamp_text} ({event.get('module_id', 'module')})"
