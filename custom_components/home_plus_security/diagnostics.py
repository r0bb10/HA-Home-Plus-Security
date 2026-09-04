"""Redacted diagnostics for Home + Security."""

from __future__ import annotations

from collections import Counter
import hashlib
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN

_SECRET_KEYS = {
    "access_token",
    "refresh_token",
    "client_secret",
    "client_id",
    "home_id",
    "local_ipv4",
    "local_ip",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return runtime diagnostics with all sensitive identifiers redacted."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = runtime.get(DATA_COORDINATOR) if isinstance(runtime, dict) else None
    data = getattr(coordinator, "data", {}) if coordinator is not None else {}
    data = data if isinstance(data, dict) else {}
    history = getattr(coordinator, "history", None)
    events = history.list_events() if history is not None else []
    modules = data.get("modules", [])
    module_types = Counter(
        module.get("type", "unknown")
        for module in modules
        if isinstance(module, dict)
    )

    return {
        "entry": _redact_mapping(entry.data),
        "options": _redact_mapping(entry.options),
        "runtime": {
            "websocket": data.get("ws", {}),
            "module_counts": dict(module_types),
            "push": _push_summary(data.get("push")),
            "history": [
                {
                    "event_id": _redact_identifier(event.get("event_id")),
                    "module_id": _redact_identifier(event.get("module_id")),
                    "timestamp": event.get("timestamp"),
                    "snapshot": bool(event.get("snapshot_file")),
                    "snapshot_content_type": event.get("snapshot_content_type"),
                    "snapshot_bytes": event.get("snapshot_bytes"),
                    "snapshot_sha256": event.get("snapshot_sha256"),
                    "vignette": bool(event.get("vignette_file")),
                    "vignette_content_type": event.get("vignette_content_type"),
                    "vignette_bytes": event.get("vignette_bytes"),
                    "vignette_sha256": event.get("vignette_sha256"),
                }
                for event in events
            ],
        },
    }


def _redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Redact known secrets and identifiers from config-entry data."""
    return {
        key: "**REDACTED**" if key in _SECRET_KEYS else value
        for key, value in data.items()
    }


def _push_summary(push: Any) -> dict[str, Any]:
    """Return push state without exposing identifiers or signaling material."""
    if not isinstance(push, dict):
        return {}
    last_event = push.get("last_event")
    if not isinstance(last_event, dict):
        return {"active_call_count": len(push.get("active_calls", {}))}
    return {
        "active_call_count": len(push.get("active_calls", {})),
        "last_event": {
            "type": last_event.get("type"),
            "event_id": _redact_identifier(last_event.get("event_id")),
            "module_id": _redact_identifier(last_event.get("module_id")),
            "session_id": _redact_identifier(last_event.get("session_id")),
            "timestamp": last_event.get("timestamp"),
            "snapshot_available": last_event.get("snapshot_available"),
            "vignette_available": last_event.get("vignette_available"),
        },
    }


def _redact_identifier(value: Any) -> str | None:
    """Return a stable diagnostic-safe fingerprint for an external identifier."""
    if not isinstance(value, str) or not value:
        return None
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]}"
