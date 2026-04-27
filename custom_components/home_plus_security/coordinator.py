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
    COORDINATOR_UPDATE_INTERVAL,
    DOMAIN,
    WS_STALE_THRESHOLD_SECONDS,
)

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


class HomePlusSecurityDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for app API data."""

    def __init__(self, hass, client: HomePlusSecurityApiClient, home_id: str) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=COORDINATOR_UPDATE_INTERVAL,
        )
        self.client = client
        self.home_id = home_id

        self._command_lock = asyncio.Lock()
        self._last_command_at_monotonic = 0.0
        self._last_command_error: str | None = None

        self._ws_connected = False
        self._ws_last_message_at: datetime | None = None
        self._ws_stale = False

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
        self._ws_stale = False
        self._publish_runtime_state()

    def mark_ws_disconnected(self) -> None:
        """Mark websocket as disconnected."""
        self._ws_connected = False
        self._publish_runtime_state()

    def note_ws_message(self) -> None:
        """Update websocket activity heartbeat."""
        self._ws_last_message_at = datetime.now(UTC)
        self._ws_stale = False
        self._publish_runtime_state()

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
        try:
            homesdata = await self.client.async_get_homesdata()
            homestatus = await self.client.async_get_homestatus(self.home_id)
        except Exception as err:  # noqa: BLE001 - preserve last-good payload on transient failure
            if isinstance(self.data, dict) and self.data:
                _LOGGER.debug("Transient update failure, keeping last coordinator data: %s", err)
                return self.data
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
        status_modules = status_home.get("modules", [])
        bncx_home = next(
            (
                module
                for module in selected_home.get("modules", [])
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

        if self._ws_last_message_at:
            silence_seconds = (datetime.now(UTC) - self._ws_last_message_at).total_seconds()
            if silence_seconds > WS_STALE_THRESHOLD_SECONDS:
                self._ws_stale = True

        events = (
            events_payload.get("body", {})
            .get("home", {})
            .get("events", [])
        )
        if not isinstance(events, list):
            events = []

        return {
            "home": selected_home,
            "status_home": status_home if isinstance(status_home, dict) else {},
            "bncx_home": bncx_home if isinstance(bncx_home, dict) else {},
            "bncx_status": bncx_status if isinstance(bncx_status, dict) else {},
            "events": events,
            "ws": self._build_ws_state(),
        }

    def _build_ws_state(self) -> dict[str, Any]:
        return {
            "connected": self._ws_connected,
            "stale": self._ws_stale,
            "last_message_at": self._ws_last_message_at.isoformat() if self._ws_last_message_at else None,
            "last_command_error": self._last_command_error,
        }

    def _publish_runtime_state(self) -> None:
        if not isinstance(self.data, dict) or not self.data:
            return
        new_data = dict(self.data)
        new_data["ws"] = self._build_ws_state()
        self.async_set_updated_data(new_data)
