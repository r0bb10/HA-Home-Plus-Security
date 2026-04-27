"""RTC signaling websocket client for Home + Security."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import logging
import ssl
import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientWebSocketResponse, WSMsgType

from .api import HomePlusSecurityApiClient
from .const import SIGNALING_WS_URL

_LOGGER = logging.getLogger(__name__)


class HomePlusSecuritySignalingClient:
    """Manage signaling websocket and RTC session identifiers."""

    def __init__(self, *, session: ClientSession, client: HomePlusSecurityApiClient) -> None:
        self._session = session
        self._client = client
        self._ws: ClientWebSocketResponse | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._pending_offer_ack: asyncio.Future[dict[str, Any]] | None = None
        self._lock = asyncio.Lock()
        self._ssl_lock = asyncio.Lock()

        self._session_id: str | None = None
        self._tag_id: str | None = None
        self._device_id: str | None = None
        self._correlation_id: str | None = None
        self._session_callbacks: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._ssl_context: ssl.SSLContext | None = None

    @property
    def session_id(self) -> str | None:
        """Current RTC session id, when available."""
        return self._session_id

    @property
    def tag_id(self) -> str | None:
        """Current RTC tag id, when available."""
        return self._tag_id

    async def async_ensure_connected(self) -> None:
        """Connect and subscribe signaling websocket if needed."""
        async with self._lock:
            if self._ws and not self._ws.closed:
                return
            await self._async_connect_locked()

    async def async_disconnect(self) -> None:
        """Close signaling websocket and clear in-memory session."""
        async with self._lock:
            await self._async_disconnect_locked()

    async def async_send_offer(
        self,
        *,
        device_id: str,
        sdp: str,
        module_id: str | None = None,
        on_session_message: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """Send offer and wait for ack that includes session metadata."""
        await self.async_ensure_connected()
        if not self._ws:
            raise HomePlusSecuritySignalingError("Signaling websocket is not connected.")

        correlation_id = str(int(time.time() * 1000))
        self._device_id = device_id
        self._correlation_id = correlation_id

        session_description: dict[str, Any] = {"type": "call", "sdp": sdp}
        if module_id:
            session_description["module_id"] = module_id

        offer_payload = {
            "action": "rtc",
            "data": {
                "type": "offer",
                "session_description": session_description,
            },
            "device_id": device_id,
            "correlation_id": correlation_id,
        }

        loop = asyncio.get_running_loop()
        self._pending_offer_ack = loop.create_future()
        await self._ws.send_json(offer_payload)

        try:
            ack = await asyncio.wait_for(self._pending_offer_ack, timeout=30)
        except TimeoutError as err:
            raise HomePlusSecuritySignalingError("Timeout waiting for offer ack.") from err
        finally:
            self._pending_offer_ack = None

        if ack.get("status") == "error":
            error = ack.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message:
                    raise HomePlusSecuritySignalingError(message)
            raise HomePlusSecuritySignalingError(f"Offer rejected: {ack}")

        session_id = ack.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise HomePlusSecuritySignalingError("Offer ack did not include session_id.")
        if on_session_message is not None:
            self._session_callbacks[session_id] = on_session_message
        return session_id

    async def async_send_candidate(self, *, sdp_m_line_index: int, candidate: str) -> None:
        """Send ICE candidate in current session."""
        await self._async_send_rtc_action(
            action_type="candidate",
            data={
                "ice_candidate": {
                    "sdp_m_line_index": sdp_m_line_index,
                    "candidate": candidate,
                }
            },
        )

    async def async_send_next_module(self) -> None:
        """Switch to next module in active call."""
        await self._async_send_rtc_action(
            action_type="player",
            data={"value": "next_module"},
        )

    async def async_send_terminate(self) -> None:
        """Terminate active call session."""
        old_session_id = self._session_id
        await self._async_send_rtc_action(action_type="terminate", data={})
        self._session_id = None
        self._tag_id = None
        if old_session_id:
            self._session_callbacks.pop(old_session_id, None)

    def async_set_session_callback(
        self,
        session_id: str,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        """Set or clear callback for one signaling session."""
        if callback is None:
            self._session_callbacks.pop(session_id, None)
            return
        self._session_callbacks[session_id] = callback

    async def _async_send_rtc_action(self, *, action_type: str, data: dict[str, Any]) -> None:
        await self.async_ensure_connected()
        if not self._session_id or not self._tag_id or not self._device_id:
            raise HomePlusSecuritySignalingError("No active signaling session.")
        if not self._ws:
            raise HomePlusSecuritySignalingError("Signaling websocket is not connected.")

        payload: dict[str, Any] = {
            "action": "rtc",
            "data": {"type": action_type, **data},
            "session_id": self._session_id,
            "tag_id": self._tag_id,
            "device_id": self._device_id,
            "correlation_id": str(int(time.time() * 1000)),
        }
        await self._ws.send_json(payload)

    async def _async_connect_locked(self) -> None:
        ssl_context = await self._async_get_ssl_context()
        self._ws = await self._session.ws_connect(
            SIGNALING_WS_URL,
            ssl=ssl_context,
            heartbeat=None,
            autoping=True,
        )
        await self._async_subscribe_locked()
        self._listener_task = asyncio.create_task(self._async_listener(), name="home_plus_security_signaling_listener")

    async def _async_disconnect_locked(self) -> None:
        listener = self._listener_task
        self._listener_task = None
        if listener and not listener.done():
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener

        ws = self._ws
        self._ws = None
        if ws and not ws.closed:
            with suppress(ClientError):
                await ws.close()

        self._session_id = None
        self._tag_id = None
        self._device_id = None
        self._correlation_id = None
        self._session_callbacks.clear()
        if self._pending_offer_ack and not self._pending_offer_ack.done():
            self._pending_offer_ack.cancel()
        self._pending_offer_ack = None

    async def _async_subscribe_locked(self) -> None:
        if not self._ws:
            raise HomePlusSecuritySignalingError("Signaling websocket is not connected.")
        access_token = await self._client.async_get_access_token()
        subscribe_payload = {
            "action": "subscribe",
            "access_token": access_token,
            "app_type": "app_security",
            "version": "1.0",
            "platform": "android",
        }
        await self._ws.send_json(subscribe_payload)
        ack = await self._ws.receive(timeout=30)
        if ack.type != WSMsgType.TEXT:
            raise HomePlusSecuritySignalingError("Signaling subscribe failed: no text ack.")
        payload = ack.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise HomePlusSecuritySignalingError(f"Signaling subscribe rejected: {payload}")

    async def _async_listener(self) -> None:
        if not self._ws:
            return
        while self._ws and not self._ws.closed:
            msg = await self._ws.receive()
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                return
            if msg.type == WSMsgType.ERROR:
                return
            if msg.type != WSMsgType.TEXT:
                continue
            payload = msg.json()
            if not isinstance(payload, dict):
                continue
            await self._async_handle_message(payload)

    async def _async_handle_message(self, payload: dict[str, Any]) -> None:
        top_type = payload.get("type")
        if top_type == "ack":
            session_id = payload.get("session_id")
            tag_id = payload.get("tag_id")
            if isinstance(session_id, str) and session_id:
                self._session_id = session_id
            if isinstance(tag_id, str) and tag_id:
                self._tag_id = tag_id
            if self._pending_offer_ack and not self._pending_offer_ack.done():
                self._pending_offer_ack.set_result(payload)
            return

        data = payload.get("data")
        if not isinstance(data, dict):
            return

        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            callback = self._session_callbacks.get(session_id)
            if callback:
                try:
                    callback(payload)
                except Exception:  # noqa: BLE001 - do not break listener on callback errors
                    _LOGGER.debug("Signaling session callback failed", exc_info=True)

        if data.get("type") == "terminate":
            if isinstance(session_id, str) and session_id:
                self._session_callbacks.pop(session_id, None)
                if self._session_id == session_id:
                    self._session_id = None
                    self._tag_id = None

    async def _async_get_ssl_context(self) -> ssl.SSLContext:
        """Create SSL context outside the event loop and reuse it."""
        if self._ssl_context is not None:
            return self._ssl_context

        async with self._ssl_lock:
            if self._ssl_context is None:
                self._ssl_context = await asyncio.to_thread(ssl.create_default_context)

        return self._ssl_context


class HomePlusSecuritySignalingError(Exception):
    """Raised for signaling connection/session issues."""
