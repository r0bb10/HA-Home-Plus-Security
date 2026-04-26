"""Home + Security integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
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
    CONF_API_BASE_URL,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_HOME_ID,
    CONF_HOME_NAME,
    CONF_REFRESH_TOKEN,
    CONF_SCOPE,
    CONF_TOKEN_URL,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DEFAULT_API_BASE_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    DEFAULT_SCOPE,
    DEFAULT_TOKEN_URL,
    DOMAIN,
)
from .coordinator import HomePlusSecurityDataUpdateCoordinator
from .device import build_device_info

PLATFORMS: list[str] = ["sensor", "binary_sensor", "button"]
HomePlusSecurityConfigEntry = ConfigEntry


def _entry_value(entry: ConfigEntry, key: str, default: str = "") -> str:
    value = entry.options.get(key, entry.data.get(key, default))
    return value if isinstance(value, str) else default


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

    client = HomePlusSecurityApiClient(
        session=session,
        api_base_url=_entry_value(entry, CONF_API_BASE_URL, DEFAULT_API_BASE_URL),
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

    coordinator = HomePlusSecurityDataUpdateCoordinator(hass, client, home_id)

    try:
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
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        **device_info,
    )
    # Normalize legacy fields from previous versions (model_id/suggested_area).
    device_registry.async_update_device(
        device_id=device_entry.id,
        area_id=None,
        manufacturer=device_info.get("manufacturer"),
        model=device_info.get("model"),
        model_id=None,
        name=device_info.get("name"),
        serial_number=device_info.get("serial_number"),
        sw_version=device_info.get("sw_version"),
        hw_version=device_info.get("hw_version"),
    )

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
        CONF_HOME_ID: home_id,
        CONF_HOME_NAME: _entry_value(entry, CONF_HOME_NAME),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HomePlusSecurityConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
