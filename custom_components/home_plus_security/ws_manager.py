"""Push websocket manager for Home + Security."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
import ssl
import time
from typing import Any

from aiohttp import (
    ClientError,
    ClientSession,
    ClientWebSocketResponse,
    WSMsgType,
)

from .api import HomePlusSecurityApiClient
from .const import (
    DEFAULT_APP_VERSION,
    PUSH_WS_URL,
    WS_BOOT_RETRY_DELAYS,
    WS_RESUBSCRIBE_INTERVAL_SECONDS,
    WS_RUNTIME_RETRY_DELAYS,
    WS_STALE_THRESHOLD_SECONDS,
)
from .coordinator import HomePlusSecurityDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class HomePlusSecurityWsManager:
    """Manage push websocket connect/reconnect/subscription lifecycle."""

    def __init__(
        self,
        *,
        session: ClientSession,
        client: HomePlusSecurityApiClient,
        coordinator: HomePlusSecurityDataUpdateCoordinator,
    ) -> None:
        self._session = session
        self._client = client
        self._coordinator = coordinator
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._ws: ClientWebSocketResponse | None = None
        self._ws_last_message_monotonic = 0.0
        self._ssl_context: ssl.SSLContext | None = None
        self._ssl_lock = asyncio.Lock()

    async def async_start(self) -> None:
        """Start background connection manager."""
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._async_run_loop(), name="home_plus_security_ws_manager")

    async def async_stop(self) -> None:
        """Stop background manager and close websocket."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        await self._async_disconnect()

    async def _async_run_loop(self) -> None:
        boot_phase = True
        retry_index = 0
        while self._running:
            try:
                await self._async_connect_and_listen()
                retry_index = 0
                boot_phase = False
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep reconnecting until stop
                _LOGGER.debug("Push websocket manager loop error: %s", err)
            finally:
                await self._async_disconnect()

            if not self._running:
                break

            delays = WS_BOOT_RETRY_DELAYS if boot_phase else WS_RUNTIME_RETRY_DELAYS
            delay = delays[min(retry_index, len(delays) - 1)]
            retry_index += 1
            await asyncio.sleep(delay)

    async def _async_connect_and_listen(self) -> None:
        """Open websocket, subscribe, then keep listener loop alive."""
        ssl_context = await self._async_get_ssl_context()
        self._ws = await self._session.ws_connect(
            PUSH_WS_URL,
            ssl=ssl_context,
            heartbeat=None,
            autoping=True,
        )

        await self._async_subscribe()
        self._coordinator.mark_ws_connected()
        self._ws_last_message_monotonic = time.monotonic()

        last_resubscribe = time.monotonic()
        while self._running and self._ws and not self._ws.closed:
            try:
                msg = await self._ws.receive(timeout=60)
            except TimeoutError:
                now = time.monotonic()
                silence = now - self._ws_last_message_monotonic
                if silence > WS_STALE_THRESHOLD_SECONDS:
                    raise HomePlusSecurityWsError(f"Push websocket stale for {int(silence)}s")
                if now - last_resubscribe >= WS_RESUBSCRIBE_INTERVAL_SECONDS:
                    await self._async_subscribe()
                    last_resubscribe = now
                continue

            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                raise HomePlusSecurityWsError("Push websocket closed by remote.")
            if msg.type == WSMsgType.ERROR:
                raise HomePlusSecurityWsError(f"Push websocket error: {self._ws.exception()}")
            if msg.type == WSMsgType.PING:
                continue
            if msg.type == WSMsgType.PONG:
                continue
            if msg.type != WSMsgType.TEXT:
                continue

            try:
                payload = msg.json()
            except ValueError:
                _LOGGER.debug("Push ws non-JSON message: %s", msg.data)
                continue
            if await self._handle_message(payload):
                self._ws_last_message_monotonic = time.monotonic()

    async def _async_subscribe(self) -> None:
        if not self._ws:
            raise HomePlusSecurityWsError("Websocket is not connected.")
        access_token = await self._client.async_get_access_token()
        subscribe_payload = {
            "action": "Subscribe",
            "access_token": access_token,
            "app_type": "app_camera",
            "platform": "Android",
            "version": DEFAULT_APP_VERSION,
        }
        await self._ws.send_json(subscribe_payload)
        ack = await self._ws.receive(timeout=30)
        if ack.type != WSMsgType.TEXT:
            raise HomePlusSecurityWsError("Push subscribe failed: no text response.")
        try:
            payload = json.loads(ack.data)
        except (TypeError, json.JSONDecodeError) as err:
            raise HomePlusSecurityWsError("Push subscribe failed: invalid JSON response.") from err
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise HomePlusSecurityWsError(f"Push subscribe rejected: {payload}")

    async def _handle_message(self, payload: Any) -> bool:
        """Process a push message without logging sensitive payload contents."""
        if not isinstance(payload, dict) or not isinstance(payload.get("extra_params"), dict):
            return False
        await self._coordinator.async_process_push_message(payload)
        # Push messages can accompany device state changes but carry no telemetry.
        self._coordinator.async_request_refresh()
        self._coordinator.note_ws_application_message()
        return True

    async def _async_disconnect(self) -> None:
        ws = self._ws
        self._ws = None
        self._coordinator.mark_ws_disconnected()
        if ws and not ws.closed:
            with suppress(ClientError):
                await ws.close()

    async def _async_get_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context outside the event loop and reuse it."""
        if self._ssl_context is not None:
            return self._ssl_context

        async with self._ssl_lock:
            if self._ssl_context is None:
                self._ssl_context = await asyncio.to_thread(ssl.create_default_context)

        return self._ssl_context


class HomePlusSecurityWsError(Exception):
    """Raised for websocket manager errors."""
