"""Home + Security integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr

from .api import (
    HomePlusSecurityApiClient,
    HomePlusSecurityApiError,
    HomePlusSecurityAuthConfig,
    HomePlusSecurityAuthError,
    normalize_scope,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_APP_API_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_HOME_ID,
    CONF_HOME_NAME,
    CONF_REFRESH_TOKEN,
    CONF_SCOPE,
    CONF_SYNC_API_BASE_URL,
    CONF_TOKEN_URL,
    CONF_TURN_API_BASE_URL,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DATA_SIGNALING_CLIENT,
    DATA_WS_MANAGER,
    DEFAULT_APP_API_BASE_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    DEFAULT_SCOPE,
    DEFAULT_SYNC_API_BASE_URL,
    DEFAULT_TOKEN_URL,
    DEFAULT_TURN_API_BASE_URL,
    DOMAIN,
    HISTORY_MAX_EVENTS,
    HISTORY_RETENTION_DAYS,
    OPT_HISTORY_ENABLED,
    OPT_HISTORY_MAX_EVENTS,
    OPT_HISTORY_RETENTION_DAYS,
)
from .coordinator import HomePlusSecurityDataUpdateCoordinator
from .device import build_device_info
from .history import HomePlusSecurityEventHistory
from .signaling import HomePlusSecuritySignalingClient, HomePlusSecuritySignalingError
from .ws_manager import HomePlusSecurityWsManager
from .media_source import HomePlusSecurityHistoryImageView

PLATFORMS: list[str] = ["sensor", "binary_sensor", "button", "camera", "event"]
HomePlusSecurityConfigEntry = ConfigEntry

SERVICE_RTC_OFFER = "rtc_offer"
SERVICE_RTC_NEXT_MODULE = "rtc_next_module"
SERVICE_RTC_TERMINATE = "rtc_terminate"
_DATA_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"
_DATA_HISTORY_VIEW_REGISTERED = f"{DOMAIN}_history_view_registered"


def _entry_value(entry: ConfigEntry, key: str, default: str = "") -> str:
    value = entry.options.get(key, entry.data.get(key, default))
    return value if isinstance(value, str) else default


def _entry_bool_option(entry: ConfigEntry, key: str, default: bool) -> bool:
    value = entry.options.get(key, default)
    return value if isinstance(value, bool) else default


def _entry_int_option(entry: ConfigEntry, key: str, default: int, maximum: int) -> int:
    value = entry.options.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return min(max(value, 1), maximum)
    return default


async def async_setup_entry(hass: HomeAssistant, entry: HomePlusSecurityConfigEntry) -> bool:
    """Set up Home + Security from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)

    auth_config = HomePlusSecurityAuthConfig(
        token_url=_entry_value(entry, CONF_TOKEN_URL, DEFAULT_TOKEN_URL),
        client_id=_entry_value(entry, CONF_CLIENT_ID, DEFAULT_CLIENT_ID),
        client_secret=_entry_value(entry, CONF_CLIENT_SECRET, DEFAULT_CLIENT_SECRET),
        scope=_entry_value(entry, CONF_SCOPE, DEFAULT_SCOPE),
    )

    async def _async_token_update(payload: dict[str, Any]) -> None:
        refresh_token = payload.get("refresh_token")
        access_token = payload.get("access_token")
        scope = payload.get("scope")
        if not isinstance(refresh_token, str) or not refresh_token:
            return

        new_data = dict(entry.data)
        new_data[CONF_REFRESH_TOKEN] = refresh_token
        if isinstance(access_token, str) and access_token:
            new_data[CONF_ACCESS_TOKEN] = access_token
        if scope is not None:
            scope_tokens = normalize_scope(scope)
            if scope_tokens:
                new_data[CONF_SCOPE] = " ".join(sorted(scope_tokens))

        hass.config_entries.async_update_entry(entry, data=new_data)

    app_api_base_url = _entry_value(
        entry,
        CONF_APP_API_BASE_URL,
        DEFAULT_APP_API_BASE_URL,
    )

    client = HomePlusSecurityApiClient(
        session=session,
        app_api_base_url=app_api_base_url,
        sync_api_base_url=_entry_value(entry, CONF_SYNC_API_BASE_URL, DEFAULT_SYNC_API_BASE_URL),
        turn_api_base_url=_entry_value(entry, CONF_TURN_API_BASE_URL, DEFAULT_TURN_API_BASE_URL),
        auth_config=auth_config,
        access_token=_entry_value(entry, CONF_ACCESS_TOKEN),
        refresh_token=_entry_value(entry, CONF_REFRESH_TOKEN),
        token_update_cb=_async_token_update,
    )

    try:
        await client.async_ensure_token()
    except HomePlusSecurityAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err

    home_id = _entry_value(entry, CONF_HOME_ID)
    if not home_id:
        raise ConfigEntryNotReady("Missing selected home_id in configuration.")

    history = HomePlusSecurityEventHistory(
        hass,
        entry.entry_id,
        enabled=_entry_bool_option(entry, OPT_HISTORY_ENABLED, True),
        retention_days=_entry_int_option(
            entry, OPT_HISTORY_RETENTION_DAYS, HISTORY_RETENTION_DAYS, 365
        ),
        max_events=_entry_int_option(entry, OPT_HISTORY_MAX_EVENTS, HISTORY_MAX_EVENTS, 500),
    )
    coordinator = HomePlusSecurityDataUpdateCoordinator(
        hass, client, home_id, entry.entry_id, history=history
    )
    ws_manager = HomePlusSecurityWsManager(session=session, client=client, coordinator=coordinator)
    signaling_client = HomePlusSecuritySignalingClient(session=session, client=client)

    try:
        await coordinator.async_load_history()
        await coordinator.async_config_entry_first_refresh()
    except HomePlusSecurityAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except HomePlusSecurityApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    bncx_home = coordinator.data.get("bncx_home", {})
    bncx_status = coordinator.data.get("bncx_status", {})
    selected_home = coordinator.data.get("home", {})

    fallback_id = str(
        bncx_home.get("id")
        or bncx_status.get("id")
        or home_id
    )
    device_registry = dr.async_get(hass)
    device_info = build_device_info(
        home=selected_home if isinstance(selected_home, dict) else {},
        bncx_home=bncx_home if isinstance(bncx_home, dict) else {},
        bncx_status=bncx_status if isinstance(bncx_status, dict) else {},
        fallback_id=fallback_id,
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **device_info,
    )

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
        DATA_WS_MANAGER: ws_manager,
        DATA_SIGNALING_CLIENT: signaling_client,
        CONF_HOME_ID: home_id,
        CONF_HOME_NAME: _entry_value(entry, CONF_HOME_NAME),
    }

    if not hass.data.get(_DATA_HISTORY_VIEW_REGISTERED):
        hass.http.register_view(HomePlusSecurityHistoryImageView())
        hass.data[_DATA_HISTORY_VIEW_REGISTERED] = True

    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await ws_manager.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomePlusSecurityConfigEntry) -> bool:
    """Unload a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    ws_manager: HomePlusSecurityWsManager | None = entry_data.get(DATA_WS_MANAGER)
    signaling_client: HomePlusSecuritySignalingClient | None = entry_data.get(DATA_SIGNALING_CLIENT)
    if ws_manager:
        await ws_manager.async_stop()
    if signaling_client:
        await signaling_client.async_disconnect()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            await _async_unregister_services(hass)
    return unload_ok


async def _async_register_services(hass: HomeAssistant) -> None:
    if hass.data.get(_DATA_SERVICES_REGISTERED):
        return

    async def _resolve_runtime(service_call: ServiceCall) -> dict[str, Any]:
        entry_id = service_call.data.get("entry_id")
        entries = hass.data.get(DOMAIN, {})
        if not isinstance(entries, dict) or not entries:
            raise HomeAssistantError("No Home + Security entries are loaded.")

        if isinstance(entry_id, str) and entry_id:
            runtime = entries.get(entry_id)
            if runtime is None:
                raise HomeAssistantError(f"Entry '{entry_id}' is not loaded.")
            return runtime

        _, runtime = next(iter(entries.items()))
        return runtime

    async def _handle_rtc_offer(service_call: ServiceCall) -> None:
        runtime = await _resolve_runtime(service_call)
        coordinator: HomePlusSecurityDataUpdateCoordinator = runtime[DATA_COORDINATOR]
        signaling: HomePlusSecuritySignalingClient = runtime[DATA_SIGNALING_CLIENT]

        sdp = service_call.data.get("sdp")
        if not isinstance(sdp, str) or not sdp.strip():
            raise HomeAssistantError("Service 'rtc_offer' requires non-empty 'sdp'.")

        device_id = service_call.data.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            bncx_status = coordinator.data.get("bncx_status", {})
            bncx_home = coordinator.data.get("bncx_home", {})
            device_id = bncx_status.get("id") or bncx_home.get("id")
        if not isinstance(device_id, str) or not device_id:
            raise HomeAssistantError("Unable to determine device_id for rtc_offer.")

        module_id = service_call.data.get("module_id")
        if not isinstance(module_id, str):
            module_id = None

        try:
            await signaling.async_send_offer(device_id=device_id, sdp=sdp, module_id=module_id)
        except HomePlusSecuritySignalingError as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_rtc_next_module(service_call: ServiceCall) -> None:
        runtime = await _resolve_runtime(service_call)
        signaling: HomePlusSecuritySignalingClient = runtime[DATA_SIGNALING_CLIENT]
        try:
            await signaling.async_send_next_module()
        except HomePlusSecuritySignalingError as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_rtc_terminate(service_call: ServiceCall) -> None:
        runtime = await _resolve_runtime(service_call)
        signaling: HomePlusSecuritySignalingClient = runtime[DATA_SIGNALING_CLIENT]
        try:
            await signaling.async_send_terminate()
        except HomePlusSecuritySignalingError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_RTC_OFFER, _handle_rtc_offer)
    hass.services.async_register(DOMAIN, SERVICE_RTC_NEXT_MODULE, _handle_rtc_next_module)
    hass.services.async_register(DOMAIN, SERVICE_RTC_TERMINATE, _handle_rtc_terminate)
    hass.data[_DATA_SERVICES_REGISTERED] = True


async def _async_unregister_services(hass: HomeAssistant) -> None:
    if not hass.data.get(_DATA_SERVICES_REGISTERED):
        return
    for service in (SERVICE_RTC_OFFER, SERVICE_RTC_NEXT_MODULE, SERVICE_RTC_TERMINATE):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
    hass.data.pop(_DATA_SERVICES_REGISTERED, None)
