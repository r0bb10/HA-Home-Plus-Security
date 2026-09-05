"""Camera platform for Home + Security."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import BytesIO
import logging
from typing import Any

from aiohttp import ClientError
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.camera.webrtc import (
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCClientConfiguration,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from webrtc_models import RTCConfiguration, RTCIceCandidateInit, RTCIceServer

from .const import (
    DATA_CLIENT,
    DATA_COORDINATOR,
    DATA_SIGNALING_CLIENT,
    DOMAIN,
    IMAGE_CACHE_SECONDS,
    OPT_EXPOSE_CAMERAS,
)
from .device import build_device_info
from .entity_options import remove_entity_if_disabled
from .event_images import find_latest_event_media

_LOGGER = logging.getLogger(__name__)
_LIVE_ANSWER_TIMEOUT_SECONDS = 25
_TURN_REFRESH_FALLBACK_SECONDS = 3600


def _resize_jpeg_thumbnail(content: bytes, width: int | None, height: int | None) -> bytes:
    """Resize an event JPEG before HA's TurboJPEG thumbnail path can corrupt it."""
    if width is None or height is None or width <= 0 or height <= 0:
        return content
    try:
        from PIL import Image

        with Image.open(BytesIO(content)) as image:
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=85)
            return output.getvalue()
    except (ImportError, OSError, ValueError, ZeroDivisionError):
        return content


@dataclass
class _LiveSessionState:
    """Per-WebRTC session state (HA session id => upstream session id)."""

    ha_session_id: str
    send_message: WebRTCSendMessage
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    upstream_session_id: str | None = None
    answer_received: bool = False
    answer_event: asyncio.Event = field(default_factory=asyncio.Event)
    pending_candidates: list[RTCIceCandidateInit] = field(default_factory=list)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up event cameras for a config entry."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime[DATA_COORDINATOR]
    client = runtime[DATA_CLIENT]
    signaling = runtime[DATA_SIGNALING_CLIENT]
    cameras = [
        HomePlusSecurityEventCamera(coordinator, entry.entry_id, image_type="snapshot"),
        HomePlusSecurityEventCamera(coordinator, entry.entry_id, image_type="vignette"),
        HomePlusSecurityLiveCamera(
            coordinator=coordinator,
            client=client,
            signaling=signaling,
            entry_id=entry.entry_id,
        ),
    ]
    if not remove_entity_if_disabled(
        hass,
        entry,
        "camera",
        OPT_EXPOSE_CAMERAS,
        cameras[0].unique_id,
        default=True,
    ):
        for camera in cameras[1:]:
            remove_entity_if_disabled(
                hass,
                entry,
                "camera",
                OPT_EXPOSE_CAMERAS,
                camera.unique_id,
                default=True,
            )
        return
    async_add_entities(cameras)


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
        self._history_event_id: str | None = None
        self._image_expires_at: datetime | None = None
        self._event_time: datetime | None = None
        self._cached_image: bytes | None = None
        self._cached_image_time: datetime | None = None
        self._update_state()

    @property
    def available(self) -> bool:
        return super().available and (self._history_event_id is not None or self._image_url is not None)

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
        previous = (self._history_event_id, self._image_url)
        self._update_state()
        if previous != (self._history_event_id, self._image_url):
            self._cached_image = None
            self._cached_image_time = None
        self.async_write_ha_state()

    def _update_state(self) -> None:
        push = self.coordinator.data.get("push", {})
        last_push_event = push.get("last_event") if isinstance(push, dict) else None
        if self._use_history_event(last_push_event):
            return
        if self._use_history_event(self.coordinator.data.get("event_media")):
            return

        self._history_event_id = None
        events = self.coordinator.data.get("events", [])
        if not isinstance(events, list):
            self._image_url = None
            self._image_expires_at = None
            self._event_time = None
            return

        media = find_latest_event_media(events)
        image_url = getattr(media, f"{self._image_type}_url") if media else None
        expires_at = getattr(media, f"{self._image_type}_expires_at") if media else None
        self._image_url = image_url
        self._image_expires_at = (
            datetime.fromtimestamp(expires_at, UTC)
            if isinstance(expires_at, (int, float)) and expires_at > 0
            else None
        )
        self._event_time = (
            datetime.fromtimestamp(media.timestamp, UTC)
            if media and isinstance(media.timestamp, (int, float)) and media.timestamp > 0
            else None
        )

    def _use_history_event(self, event: Any) -> bool:
        """Select a locally persisted image when the event cache has it."""
        if not isinstance(event, dict):
            return False
        history_id = event.get("history_id")
        image_available = event.get(f"{self._image_type}_available")
        if not isinstance(history_id, str) or image_available is not True:
            return False
        self._history_event_id = history_id
        self._image_url = None
        self._image_expires_at = None
        timestamp = event.get("timestamp")
        self._event_time = (
            datetime.fromtimestamp(timestamp, UTC)
            if isinstance(timestamp, (int, float)) and timestamp > 0
            else None
        )
        return True

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        now = datetime.now(UTC)
        if (
            self._cached_image is not None
            and self._cached_image_time is not None
            and (now - self._cached_image_time).total_seconds() < IMAGE_CACHE_SECONDS
        ):
            if self.content_type == "image/jpeg":
                return await self.hass.async_add_executor_job(
                    _resize_jpeg_thumbnail, self._cached_image, width, height
                )
            return self._cached_image

        if self._history_event_id:
            stored = await self.coordinator.history.async_read_image(
                self._history_event_id, self._image_type
            )
            if stored is None:
                return None
            content, content_type = stored
            self.content_type = content_type
            self._cached_image = content
            self._cached_image_time = now
            if content_type == "image/jpeg":
                return await self.hass.async_add_executor_job(
                    _resize_jpeg_thumbnail, content, width, height
                )
            return content

        if not self._image_url:
            return None
        if self._image_expires_at is not None and now >= self._image_expires_at:
            return None

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self._image_url) as response:
                response.raise_for_status()
                content = await response.read()
                content_type = response.content_type
        except ClientError as err:
            _LOGGER.debug("Failed to fetch %s camera image: %s", self._image_type, err)
            return None

        self._cached_image = content
        self._cached_image_time = now
        self.content_type = content_type
        if content_type == "image/jpeg":
            return await self.hass.async_add_executor_job(
                _resize_jpeg_thumbnail, content, width, height
            )
        return content


class HomePlusSecurityLiveCamera(CoordinatorEntity, Camera):
    """Live WebRTC camera using Netatmo/BTicino app signaling."""

    _attr_has_entity_name = True
    _attr_name = "Live"
    _attr_icon = "mdi:video-wireless"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, *, coordinator, client, signaling, entry_id: str) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self._client = client
        self._signaling = signaling
        self._attr_unique_id = f"{entry_id}_live_camera"
        self._session_lock = asyncio.Lock()
        self._sessions: dict[str, _LiveSessionState] = {}
        self._streaming_session_ids: set[str] = set()
        self._ice_servers: list[RTCIceServer] = []
        self._turn_expires_at: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """Initialize TURN cache for WebRTC client configuration."""
        await super().async_added_to_hass()
        await self._async_refresh_turn_configuration()

    async def async_will_remove_from_hass(self) -> None:
        """Terminate active RTC sessions on entity removal."""
        await self._async_close_all_sessions(send_terminate=True)
        await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        bncx_status = self.coordinator.data.get("bncx_status", {})
        bncx_home = self.coordinator.data.get("bncx_home", {})
        bncx_id = bncx_status.get("id") or bncx_home.get("id")
        return super().available and isinstance(bncx_id, str) and bool(bncx_id)

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
        session_count = len(self._sessions)
        attrs = {
            "active_webrtc_sessions": session_count,
            "turn_expires_at": self._turn_expires_at.isoformat() if self._turn_expires_at else None,
        }
        return {key: value for key, value in attrs.items() if value is not None}

    @property
    def is_streaming(self) -> bool:
        """Report streaming after the device accepts a WebRTC answer."""
        return bool(self._streaming_session_ids)

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return the latest snapshot as the live camera thumbnail."""
        push = self.coordinator.data.get("push", {})
        events = [push.get("last_event") if isinstance(push, dict) else None, self.coordinator.data.get("event_media")]
        for event in events:
            if not isinstance(event, dict):
                continue
            history_id = event.get("history_id")
            if isinstance(history_id, str) and event.get("snapshot_available") is True:
                stored = await self.coordinator.history.async_read_image(history_id, "snapshot")
                if stored is not None:
                    content, content_type = stored
                    self.content_type = content_type
                    if content_type == "image/jpeg":
                        return await self.hass.async_add_executor_job(
                            _resize_jpeg_thumbnail, content, width, height
                        )
                    return content

        events_data = self.coordinator.data.get("events", [])
        media = find_latest_event_media(events_data) if isinstance(events_data, list) else None
        if media is None or not media.snapshot_url:
            return None
        if media.snapshot_expires_at and datetime.now(UTC).timestamp() >= media.snapshot_expires_at:
            return None
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(media.snapshot_url) as response:
                response.raise_for_status()
                self.content_type = response.content_type
                content = await response.read()
                if response.content_type == "image/jpeg":
                    return await self.hass.async_add_executor_job(
                        _resize_jpeg_thumbnail, content, width, height
                    )
                return content
        except ClientError:
            _LOGGER.debug("Failed to fetch live camera thumbnail", exc_info=True)
            return None

    @callback
    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        """Provide client-side ICE configuration."""
        return WebRTCClientConfiguration(configuration=RTCConfiguration(ice_servers=list(self._ice_servers)))

    async def async_handle_async_webrtc_offer(
        self,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        """Send offer to Netatmo signaling and relay answer/candidates back to HA frontend."""
        await self._async_refresh_turn_configuration()
        await self._async_close_all_sessions(send_terminate=True)
        async with self._session_lock:
            self._sessions[session_id] = _LiveSessionState(
                ha_session_id=session_id,
                send_message=send_message,
            )

        device_id = self._resolve_device_id()
        if not device_id:
            await self._async_close_session(session_id, send_terminate=False)
            raise HomeAssistantError("Unable to determine BNCX device id for WebRTC offer.")

        module_id = self._resolve_call_module_id()

        def _on_session_message(payload: dict[str, Any]) -> None:
            self.hass.async_create_task(self._async_handle_session_message(session_id, payload))

        try:
            upstream_session_id = await self._signaling.async_send_offer(
                device_id=device_id,
                sdp=offer_sdp,
                module_id=module_id,
                on_session_message=_on_session_message,
            )
        except Exception as err:  # noqa: BLE001 - propagate as HA-friendly error
            await self._async_close_session(session_id, send_terminate=False)
            raise HomeAssistantError(str(err)) from err

        async with self._session_lock:
            current = self._sessions.get(session_id)
            if current is None:
                self._signaling.async_set_session_callback(upstream_session_id, None)
                return
            current.upstream_session_id = upstream_session_id

        async with self._session_lock:
            wait_state = self._sessions.get(session_id)
        if wait_state is None:
            raise HomeAssistantError("WebRTC session closed before answer.")

        try:
            await asyncio.wait_for(wait_state.answer_event.wait(), timeout=_LIVE_ANSWER_TIMEOUT_SECONDS)
        except TimeoutError as err:
            send_message(WebRTCError("webrtc_offer_timeout", "Timeout waiting for remote WebRTC answer"))
            await self._async_close_session(session_id, send_terminate=True)
            raise HomeAssistantError("Timeout waiting for remote WebRTC answer") from err

    async def async_on_webrtc_candidate(
        self,
        session_id: str,
        candidate: RTCIceCandidateInit,
    ) -> None:
        """Queue browser candidates and flush after remote answer (app requirement)."""
        async with self._session_lock:
            state = self._sessions.get(session_id)
            if state is None:
                raise HomeAssistantError("Unknown WebRTC session.")

            if not state.answer_received or not state.upstream_session_id:
                state.pending_candidates.append(candidate)
                return

        await self._async_send_candidate(state, candidate)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Close one HA-side WebRTC session."""
        super().close_webrtc_session(session_id)
        self.hass.async_create_task(self._async_close_session(session_id, send_terminate=True))

    async def _async_handle_session_message(self, ha_session_id: str, payload: dict[str, Any]) -> None:
        """Handle upstream signaling event for one HA session."""
        async with self._session_lock:
            state = self._sessions.get(ha_session_id)
            if state is None:
                return

        data = payload.get("data")
        if not isinstance(data, dict):
            return

        msg_type = data.get("type")
        if msg_type == "answer":
            session_desc = data.get("session_description")
            sdp = session_desc.get("sdp") if isinstance(session_desc, dict) else None
            if isinstance(sdp, str) and sdp:
                state.send_message(WebRTCAnswer(sdp))
                pending: list[RTCIceCandidateInit] = []
                async with self._session_lock:
                    live = self._sessions.get(ha_session_id)
                    if live is None:
                        return
                    live.answer_received = True
                    live.answer_event.set()
                    self._streaming_session_ids.add(ha_session_id)
                    pending = list(live.pending_candidates)
                    live.pending_candidates.clear()
                self.async_write_ha_state()
                for pending_candidate in pending:
                    await self._async_send_candidate(live, pending_candidate)
            return

        if msg_type == "candidate":
            ice = data.get("ice_candidate")
            if not isinstance(ice, dict):
                return
            candidate = ice.get("candidate")
            sdp_m_line_index = ice.get("sdp_m_line_index")
            if not isinstance(candidate, str):
                return
            if not isinstance(sdp_m_line_index, int):
                sdp_m_line_index = 0
            state.send_message(
                WebRTCCandidate(
                    RTCIceCandidateInit(
                        candidate,
                        sdp_m_line_index=sdp_m_line_index,
                    )
                )
            )
            return

        if msg_type == "terminate":
            state.send_message(WebRTCError("webrtc_terminated", "Remote session terminated"))
            await self._async_close_session(ha_session_id, send_terminate=False)

    async def _async_send_candidate(
        self, state: _LiveSessionState, candidate: RTCIceCandidateInit
    ) -> None:
        """Forward one browser ICE candidate to its matching upstream session."""
        if not state.upstream_session_id or self._signaling.session_id != state.upstream_session_id:
            raise HomeAssistantError("WebRTC session is no longer active upstream.")
        sdp_m_line_index = candidate.sdp_m_line_index if candidate.sdp_m_line_index is not None else 0
        await self._signaling.async_send_candidate(
            sdp_m_line_index=sdp_m_line_index,
            candidate=candidate.candidate,
        )

    async def _async_close_session(self, ha_session_id: str, *, send_terminate: bool) -> None:
        """Close one session and optionally terminate upstream."""
        async with self._session_lock:
            state = self._sessions.pop(ha_session_id, None)

        if state is None:
            return

        self._streaming_session_ids.discard(ha_session_id)
        self.async_write_ha_state()
        await self._async_finalize_session(state, send_terminate=send_terminate)

    async def _async_finalize_session(self, state: _LiveSessionState, *, send_terminate: bool) -> None:
        """Finalize one popped session."""

        if state.upstream_session_id:
            self._signaling.async_set_session_callback(state.upstream_session_id, None)

        if send_terminate and state.upstream_session_id and self._signaling.session_id == state.upstream_session_id:
            try:
                await self._signaling.async_send_terminate()
            except Exception:  # noqa: BLE001 - best effort cleanup
                _LOGGER.debug("Failed to terminate upstream session %s", state.upstream_session_id, exc_info=True)

    async def _async_close_all_sessions(self, *, send_terminate: bool) -> None:
        async with self._session_lock:
            states = [self._sessions.pop(session_id) for session_id in list(self._sessions)]
            self._streaming_session_ids.clear()
        self.async_write_ha_state()
        for state in states:
            await self._async_finalize_session(state, send_terminate=send_terminate)

    async def _async_refresh_turn_configuration(self) -> None:
        """Fetch TURN credentials and cache ICE servers for browser-side offer creation."""
        now = datetime.now(UTC)
        if self._turn_expires_at and now < self._turn_expires_at:
            return

        try:
            payload = await self._client.async_get_turn_credentials()
        except Exception:  # noqa: BLE001 - keep previous configuration if fetch fails
            _LOGGER.debug("Failed to refresh TURN credentials", exc_info=True)
            return

        body = payload.get("body", {}) if isinstance(payload, dict) else {}
        servers = body.get("iceServers") if isinstance(body, dict) else None
        if not isinstance(servers, list):
            return

        parsed_servers: list[RTCIceServer] = []
        for item in servers:
            if not isinstance(item, dict):
                continue
            urls = item.get("urls")
            if not isinstance(urls, (str, list)):
                continue
            username = item.get("username")
            credential = item.get("credential")
            parsed_servers.append(
                RTCIceServer(
                    urls=urls,
                    username=username if isinstance(username, str) else None,
                    credential=credential if isinstance(credential, str) else None,
                )
            )

        if not parsed_servers:
            return

        first_server = servers[0] if servers else {}
        lifetime_seconds = first_server.get("lifetimeDuration") if isinstance(first_server, dict) else None
        if isinstance(lifetime_seconds, (int, float)) and lifetime_seconds > 120:
            refresh_seconds = int(lifetime_seconds * 0.8)
        else:
            refresh_seconds = _TURN_REFRESH_FALLBACK_SECONDS

        self._ice_servers = parsed_servers
        self._turn_expires_at = now + timedelta(seconds=refresh_seconds)

    def _resolve_device_id(self) -> str | None:
        """Resolve BNCX device id used by RTC signaling."""
        bncx_status = self.coordinator.data.get("bncx_status", {})
        bncx_home = self.coordinator.data.get("bncx_home", {})
        device_id = bncx_status.get("id") or bncx_home.get("id")
        return device_id if isinstance(device_id, str) and device_id else None

    def _resolve_call_module_id(self) -> str | None:
        """Pick primary module id for call offer targeting."""
        home = self.coordinator.data.get("home", {})
        modules = home.get("modules", []) if isinstance(home, dict) else []
        if not isinstance(modules, list):
            return None

        for module in modules:
            if not isinstance(module, dict) or module.get("type") != "BNCX":
                continue
            bridged = module.get("modules_bridged")
            if not isinstance(bridged, list):
                continue
            for module_id in bridged:
                if isinstance(module_id, str) and module_id:
                    return module_id
        return None
