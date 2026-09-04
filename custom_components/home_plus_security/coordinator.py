"""Data coordinator for Home + Security."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import time
from typing import Any, Awaitable, Callable, TypeVar

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import HomePlusSecurityApiClient, HomePlusSecurityApiError
from .const import (
    COMMAND_COOLDOWN_SECONDS,
    COMMAND_TIMEOUT_SECONDS,
    CALL_STALE_THRESHOLD_SECONDS,
    COORDINATOR_UPDATE_INTERVAL,
    DOMAIN,
    WS_STALE_THRESHOLD_SECONDS,
)
from .history import HomePlusSecurityEventHistory
from .event_images import find_latest_event_media
from .push import HomePlusSecurityPushEvent, parse_push_event, prune_stale_calls
from .topology import normalize_modules

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


class HomePlusSecurityDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for app API data."""

    def __init__(self, hass, client: HomePlusSecurityApiClient, home_id: str, entry_id: str) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=COORDINATOR_UPDATE_INTERVAL,
        )
        self.client = client
        self.home_id = home_id
        self.history = HomePlusSecurityEventHistory(hass, entry_id)

        self._command_lock = asyncio.Lock()
        self._last_command_at_monotonic = 0.0
        self._last_command_error: str | None = None

        self._ws_connected = False
        self._ws_connected_at: datetime | None = None
        self._ws_last_message_at: datetime | None = None
        self._ws_stale = False
        self._active_calls: dict[str, dict[str, Any]] = {}
        self._closed_sessions: dict[str, datetime] = {}
        self._last_push_event: dict[str, Any] | None = None

    @property
    def last_command_error(self) -> str | None:
        """Last command failure message."""
        return self._last_command_error

    @property
    def ws_connected(self) -> bool:
        """Current websocket manager connectivity state."""
        return self._ws_connected

    @property
    def ws_stale(self) -> bool:
        """True when websocket appears silent/stale."""
        return self._ws_stale

    def mark_ws_connected(self) -> None:
        """Mark websocket as connected."""
        self._ws_connected = True
        self._ws_connected_at = datetime.now(UTC)
        self._ws_stale = False
        self._publish_runtime_state()

    def mark_ws_disconnected(self) -> None:
        """Mark websocket as disconnected."""
        self._ws_connected = False
        self._publish_runtime_state()

    def note_ws_application_message(self) -> None:
        """Update the heartbeat after a recognized application push."""
        self._ws_last_message_at = datetime.now(UTC)
        self._ws_stale = False
        self._publish_runtime_state()

    async def async_load_history(self) -> None:
        """Load persisted push-event metadata before the websocket starts."""
        await self.history.async_load()

    async def async_process_push_message(self, payload: Any) -> bool:
        """Apply a push message and preserve its images before publishing state."""
        if not self.process_push_message(payload, publish=False):
            return False
        await self._async_store_push_images()
        self._publish_push_state()
        return True

    def process_push_message(self, payload: Any, *, publish: bool = True) -> bool:
        """Apply a supported push event and notify coordinator listeners.

        SDP and other signaling material remain private to the active-call
        record. Coordinator data only exposes the state needed by entities.
        """
        stale_calls_pruned = self._prune_stale_calls()
        event = parse_push_event(payload)
        if event is None:
            if stale_calls_pruned and publish:
                self._publish_push_state()
            return False

        if event.event_type == "offer":
            return self._process_rtc_offer(event, publish=publish)
        if event.event_type in {"rescind", "terminate"}:
            return self._process_rtc_close(event, publish=publish)
        return self._process_call_status(event, publish=publish)

    def _process_rtc_offer(self, event: HomePlusSecurityPushEvent, *, publish: bool) -> bool:
        module_id = event.module_id or event.device_id
        if module_id is None:
            _LOGGER.debug("Ignoring RTC offer without a module or device ID.")
            return False

        existing = self._active_calls.get(module_id)
        if existing and existing.get("session_id") == event.session_id:
            existing["last_seen_at"] = datetime.now(UTC)
            return False

        now = datetime.now(UTC)
        ring_id = event.session_id or f"{module_id}:{now.timestamp()}"
        self._active_calls[module_id] = {
            "module_id": module_id,
            "device_id": event.device_id,
            "session_id": event.session_id,
            "tag_id": event.tag_id,
            "correlation_id": event.correlation_id,
            "sdp": event.sdp,
            "modules": event.modules,
            "ring_id": ring_id,
            "started_at": now,
            "last_seen_at": now,
            "state": "incoming_call",
        }
        self._last_push_event = self._event_data(event, module_id, "incoming_call", ring_id)
        if publish:
            self._publish_push_state()
        return True

    def _process_rtc_close(self, event: HomePlusSecurityPushEvent, *, publish: bool) -> bool:
        module_id = self._find_call_module(event)
        if module_id is None:
            return False

        call = self._active_calls.get(module_id)
        session_id = event.session_id or (call.get("session_id") if call else None)
        if session_id and session_id in self._closed_sessions:
            return False

        self._active_calls.pop(module_id, None)
        if session_id:
            self._closed_sessions[session_id] = datetime.now(UTC)
            while len(self._closed_sessions) > 32:
                self._closed_sessions.pop(next(iter(self._closed_sessions)))
        self._last_push_event = self._event_data(event, module_id, event.event_type)
        if publish:
            self._publish_push_state()
        return True

    def _process_call_status(self, event: HomePlusSecurityPushEvent, *, publish: bool) -> bool:
        module_id = self._find_call_module(event) or event.module_id or event.device_id
        if module_id is None:
            _LOGGER.debug("Ignoring call status event without a module or device ID.")
            return False

        call = self._active_calls.get(module_id)
        if event.event_type == "incoming_call" and call is None:
            now = datetime.now(UTC)
            call = {
                "module_id": module_id,
                "device_id": event.device_id,
                "session_id": event.session_id,
                "ring_id": event.session_id or f"{module_id}:{now.timestamp()}",
                "started_at": now,
                "last_seen_at": now,
                "state": "incoming_call",
            }
            self._active_calls[module_id] = call
        if call:
            call["last_seen_at"] = datetime.now(UTC)
            if event.event_type == "incoming_call":
                call["snapshot_url"] = event.snapshot_url
                call["vignette_url"] = event.vignette_url
            elif event.event_type == "accepted_call":
                call["state"] = "accepted_call"
        if event.event_type == "missed_call":
            self._active_calls.pop(module_id, None)

        self._last_push_event = self._event_data(
            event,
            module_id,
            event.event_type,
            call.get("ring_id") if call and event.event_type == "incoming_call" else None,
        )
        if publish:
            self._publish_push_state()
        return True

    async def _async_store_push_images(self) -> None:
        """Persist incoming-call media and discard expiring URLs from public state."""
        event = self._last_push_event
        if not isinstance(event, dict) or event.get("type") != "incoming_call":
            return
        event_id = event.get("ring_id") or event.get("event_id") or event.get("session_id")
        module_id = event.get("module_id")
        if not isinstance(event_id, str) or not isinstance(module_id, str):
            return
        record = await self.history.async_record_images(
            event_id=event_id,
            module_id=module_id,
            timestamp=event.get("timestamp"),
            snapshot_url=event.get("snapshot_url"),
            vignette_url=event.get("vignette_url"),
        )
        event["history_id"] = event_id
        event["snapshot_available"] = bool(record.get("snapshot_file"))
        event["vignette_available"] = bool(record.get("vignette_file"))
        event["snapshot_url"] = None
        event["vignette_url"] = None

    def _find_call_module(self, event: HomePlusSecurityPushEvent) -> str | None:
        if event.session_id:
            for module_id, call in self._active_calls.items():
                if call.get("session_id") == event.session_id:
                    return module_id
        if event.module_id in self._active_calls:
            return event.module_id
        if event.device_id in self._active_calls:
            return event.device_id
        if len(self._active_calls) == 1:
            return next(iter(self._active_calls))
        return None

    @staticmethod
    def _event_data(
        event: HomePlusSecurityPushEvent,
        module_id: str,
        event_type: str,
        ring_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "type": event_type,
            "module_id": module_id,
            "device_id": event.device_id,
            "session_id": event.session_id,
            "event_id": event.event_id,
            "ring_id": ring_id,
            "timestamp": event.timestamp,
            "snapshot_url": event.snapshot_url,
            "vignette_url": event.vignette_url,
            "received_at": datetime.now(UTC).isoformat(),
        }

    async def async_run_guarded_command(
        self,
        *,
        label: str,
        command_coro_factory: Callable[[], Awaitable[_T]],
        timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
        cooldown_seconds: float = COMMAND_COOLDOWN_SECONDS,
    ) -> _T:
        """Run one command at a time with cooldown and timeout guardrails."""
        async with self._command_lock:
            now = time.monotonic()
            elapsed = now - self._last_command_at_monotonic
            if elapsed < cooldown_seconds:
                wait_left = cooldown_seconds - elapsed
                raise HomePlusSecurityApiError(
                    f"{label} blocked by cooldown ({wait_left:.1f}s remaining)."
                )

            self._last_command_error = None

            try:
                async with asyncio.timeout(timeout_seconds):
                    result = await command_coro_factory()
            except TimeoutError as err:
                self._last_command_error = f"{label} timed out after {timeout_seconds:.1f}s."
                self._publish_runtime_state()
                raise HomePlusSecurityApiError(self._last_command_error) from err
            except Exception as err:  # noqa: BLE001 - bubble up detailed backend error
                self._last_command_error = f"{label} failed: {err}"
                self._publish_runtime_state()
                raise

            self._last_command_at_monotonic = time.monotonic()
            self._last_command_error = None
            self._publish_runtime_state()
            return result

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the API."""
        stale_calls_pruned = self._prune_stale_calls()
        try:
            homesdata = await self.client.async_get_homesdata()
            homestatus = await self.client.async_get_homestatus(self.home_id)
        except Exception as err:  # noqa: BLE001 - preserve last-good payload on transient failure
            if isinstance(self.data, dict) and self.data:
                _LOGGER.debug("Transient update failure, keeping last coordinator data: %s", err)
                if not stale_calls_pruned:
                    return self.data
                data = dict(self.data)
                data["push"] = self._build_push_state()
                data["ws"] = self._build_ws_state()
                return data
            raise

        events_payload: dict[str, Any] = {}
        try:
            events_payload = await self.client.async_get_events(self.home_id, size=20)
        except Exception as err:  # noqa: BLE001 - events are optional; keep core entities live
            _LOGGER.debug("Events fetch failed during update: %s", err)

        homes = homesdata.get("body", {}).get("homes", [])
        selected_home = next(
            (
                home
                for home in homes
                if isinstance(home, dict) and str(home.get("id")) == self.home_id
            ),
            {},
        )

        status_home = homestatus.get("body", {}).get("home", {})
        home_modules = selected_home.get("modules", [])
        if not isinstance(home_modules, list):
            home_modules = []
        status_modules = status_home.get("modules", [])
        if not isinstance(status_modules, list):
            status_modules = []
        modules, modules_by_id = normalize_modules(home_modules, status_modules)
        bncx_home = next(
            (
                module
                for module in home_modules
                if isinstance(module, dict) and module.get("type") == "BNCX"
            ),
            None,
        )
        bncx_status_by_type = next(
            (
                module
                for module in status_modules
                if isinstance(module, dict) and module.get("type") == "BNCX"
            ),
            None,
        )

        bncx_id = (
            str(bncx_home.get("id"))
            if isinstance(bncx_home, dict) and bncx_home.get("id")
            else (
                str(bncx_status_by_type.get("id"))
                if isinstance(bncx_status_by_type, dict) and bncx_status_by_type.get("id")
                else None
            )
        )
        bncx_status = next(
            (
                module
                for module in status_modules
                if isinstance(module, dict) and bncx_id and str(module.get("id")) == bncx_id
            ),
            bncx_status_by_type if isinstance(bncx_status_by_type, dict) else None,
        )

        if not isinstance(bncx_home, dict):
            bncx_home = {}

        if isinstance(bncx_status, dict):
            bncx_home = {
                "id": bncx_home.get("id") or bncx_status.get("id"),
                "name": bncx_home.get("name") or selected_home.get("name", "Classe 300EOS"),
                "type": "BNCX",
            }

        if not bncx_home.get("id"):
            bncx_home = {
                "id": self.home_id,
                "name": selected_home.get("name", "Classe 300EOS"),
                "type": "BNCX",
            }

        heartbeat_at = self._ws_last_message_at or self._ws_connected_at
        if heartbeat_at:
            silence_seconds = (datetime.now(UTC) - heartbeat_at).total_seconds()
            if silence_seconds > WS_STALE_THRESHOLD_SECONDS:
                self._ws_stale = True

        events = (
            events_payload.get("body", {})
            .get("home", {})
            .get("events", [])
        )
        if not isinstance(events, list):
            events = []
        polled_event_media = await self._async_store_polled_event_images(events)

        return {
            "home": selected_home,
            "status_home": status_home if isinstance(status_home, dict) else {},
            "bncx_home": bncx_home if isinstance(bncx_home, dict) else {},
            "bncx_status": bncx_status if isinstance(bncx_status, dict) else {},
            "events": events,
            "event_media": polled_event_media,
            "modules": modules,
            "modules_by_id": modules_by_id,
            "push": self._build_push_state(),
            "ws": self._build_ws_state(),
        }

    def _prune_stale_calls(self) -> bool:
        """Clear orphaned calls when their final push message was lost."""
        stale_calls = prune_stale_calls(
            self._active_calls,
            now=datetime.now(UTC),
            threshold_seconds=CALL_STALE_THRESHOLD_SECONDS,
        )
        for call in stale_calls.values():
            session_id = call.get("session_id")
            if isinstance(session_id, str) and session_id:
                self._closed_sessions[session_id] = datetime.now(UTC)
        while len(self._closed_sessions) > 32:
            self._closed_sessions.pop(next(iter(self._closed_sessions)))
        return bool(stale_calls)

    async def _async_store_polled_event_images(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Persist the latest API event media when no live push was received."""
        media = find_latest_event_media(events)
        if media is None or not media.event_id or not media.module_id:
            return {}
        record = await self.history.async_record_images(
            event_id=media.event_id,
            module_id=media.module_id,
            timestamp=media.timestamp,
            snapshot_url=media.snapshot_url,
            vignette_url=media.vignette_url,
        )
        return {
            "history_id": media.event_id,
            "timestamp": media.timestamp,
            "snapshot_available": bool(record.get("snapshot_file")),
            "vignette_available": bool(record.get("vignette_file")),
        }

    def _build_ws_state(self) -> dict[str, Any]:
        return {
            "connected": self._ws_connected,
            "stale": self._ws_stale,
            "last_message_at": self._ws_last_message_at.isoformat() if self._ws_last_message_at else None,
            "last_command_error": self._last_command_error,
        }

    def _build_push_state(self) -> dict[str, Any]:
        active_calls = {
            module_id: {
                "module_id": call["module_id"],
                "device_id": call["device_id"],
                "session_id": call["session_id"],
                "ring_id": call["ring_id"],
                "state": call["state"],
                "started_at": call["started_at"].isoformat(),
                "last_seen_at": call["last_seen_at"].isoformat(),
            }
            for module_id, call in self._active_calls.items()
        }
        return {"active_calls": active_calls, "last_event": self._last_push_event}

    def _publish_runtime_state(self) -> None:
        if not isinstance(self.data, dict) or not self.data:
            return
        new_data = dict(self.data)
        new_data["ws"] = self._build_ws_state()
        self.async_set_updated_data(new_data)

    def _publish_push_state(self) -> None:
        if not isinstance(self.data, dict) or not self.data:
            return
        new_data = dict(self.data)
        new_data["push"] = self._build_push_state()
        new_data["ws"] = self._build_ws_state()
        self.async_set_updated_data(new_data)
