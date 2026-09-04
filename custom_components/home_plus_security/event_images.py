"""Extract event-media URLs from Home + Security API payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EventMedia:
    """The latest media attached to one API event."""

    event_id: str | None
    module_id: str | None
    timestamp: int | float | None
    snapshot_url: str | None
    snapshot_expires_at: int | float | None
    vignette_url: str | None
    vignette_expires_at: int | float | None


def find_latest_event_media(events: Any) -> EventMedia | None:
    """Return the first event containing a usable snapshot or vignette URL.

    Netatmo event payloads may place media either on the event itself or on any
    of its subevents. Do not assume the first subevent is the relevant one.
    """
    if not isinstance(events, list):
        return None
    for event in events:
        if not isinstance(event, dict):
            continue
        sources = [event]
        subevents = event.get("subevents")
        if isinstance(subevents, list):
            sources.extend(subevent for subevent in subevents if isinstance(subevent, dict))

        snapshot_url, snapshot_expires_at = _media_url(sources, "snapshot")
        vignette_url, vignette_expires_at = _media_url(sources, "vignette")
        if not snapshot_url and not vignette_url:
            continue

        source = next(
            (
                item
                for item in sources
                if _optional_str(item.get("module_id"))
            ),
            next((item for item in sources if _optional_str(item.get("id"))), event),
        )
        return EventMedia(
            event_id=_optional_str(event.get("id")) or _optional_str(source.get("id")),
            module_id=_optional_str(source.get("module_id")) or _optional_str(event.get("module_id")),
            timestamp=_timestamp(source.get("time")) or _timestamp(event.get("time")),
            snapshot_url=snapshot_url,
            snapshot_expires_at=snapshot_expires_at,
            vignette_url=vignette_url,
            vignette_expires_at=vignette_expires_at,
        )
    return None


def _media_url(sources: list[dict[str, Any]], image_type: str) -> tuple[str | None, int | float | None]:
    for source in sources:
        direct_url = _optional_str(source.get(f"{image_type}_url"))
        if direct_url:
            return direct_url, _timestamp(source.get(f"{image_type}_expires_at"))
        image = source.get(image_type)
        if not isinstance(image, dict):
            continue
        url = _optional_str(image.get("url"))
        if url:
            return url, _timestamp(image.get("expires_at"))
    return None, None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and value > 0 else None
