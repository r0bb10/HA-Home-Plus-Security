"""Normalize Home + Security push payloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


CALL_EVENT_TYPES = frozenset({"incoming_call", "missed_call", "accepted_call"})
RTC_EVENT_TYPES = frozenset({"offer", "rescind", "terminate"})


@dataclass(frozen=True, slots=True)
class HomePlusSecurityPushEvent:
    """A supported call-related push event."""

    event_type: str
    module_id: str | None
    device_id: str | None
    session_id: str | None
    tag_id: str | None
    correlation_id: str | None
    sdp: str | None
    modules: tuple[str, ...]
    snapshot_url: str | None
    vignette_url: str | None
    timestamp: int | float | None
    event_id: str | None


def _optional_str(value: Any) -> str | None:
    """Return a non-empty string value, otherwise None."""
    return value if isinstance(value, str) and value else None


def parse_push_event(payload: Any) -> HomePlusSecurityPushEvent | None:
    """Parse the known app_camera call and RTC push formats.

    RTC events use ``extra_params.data.type``. Call status events carry their
    type and event media directly in ``extra_params``.
    """
    if not isinstance(payload, dict):
        return None

    extra_params = payload.get("extra_params")
    if not isinstance(extra_params, dict):
        return None

    rtc_data = extra_params.get("data")
    if isinstance(rtc_data, dict):
        event_type = _optional_str(rtc_data.get("type"))
        if event_type in RTC_EVENT_TYPES:
            session_description = rtc_data.get("session_description")
            if not isinstance(session_description, dict):
                session_description = {}
            modules = session_description.get("modules")
            return HomePlusSecurityPushEvent(
                event_type=event_type,
                module_id=_optional_str(session_description.get("module_id")),
                device_id=_optional_str(extra_params.get("device_id")),
                session_id=_optional_str(extra_params.get("session_id")),
                tag_id=_optional_str(extra_params.get("tag_id")),
                correlation_id=_optional_str(extra_params.get("correlation_id")),
                sdp=_optional_str(session_description.get("sdp")),
                modules=tuple(module for module in modules if isinstance(module, str)) if isinstance(modules, list) else (),
                snapshot_url=None,
                vignette_url=None,
                timestamp=None,
                event_id=None,
            )

    event_type = _optional_str(extra_params.get("event_type"))
    if event_type not in CALL_EVENT_TYPES:
        return None

    timestamp = extra_params.get("timestamp")
    return HomePlusSecurityPushEvent(
        event_type=event_type,
        module_id=_optional_str(extra_params.get("module_id")),
        device_id=_optional_str(extra_params.get("device_id")),
        session_id=_optional_str(extra_params.get("session_id")),
        tag_id=None,
        correlation_id=None,
        sdp=None,
        modules=(),
        snapshot_url=_optional_str(extra_params.get("snapshot_url")),
        vignette_url=_optional_str(extra_params.get("vignette_url")),
        timestamp=timestamp if isinstance(timestamp, (int, float)) else None,
        event_id=_optional_str(extra_params.get("event_id")),
    )


def prune_stale_calls(
    active_calls: dict[str, dict[str, Any]],
    *,
    now: datetime,
    threshold_seconds: float,
) -> dict[str, dict[str, Any]]:
    """Remove calls that have not received an update within the threshold."""
    cutoff = now - timedelta(seconds=threshold_seconds)
    stale_calls = {
        module_id: call
        for module_id, call in active_calls.items()
        if not isinstance(call.get("last_seen_at"), datetime) or call["last_seen_at"] <= cutoff
    }
    for module_id in stale_calls:
        active_calls.pop(module_id, None)
    return stale_calls
