"""Home + Security integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    HomePlusSecurityApiClient,
    HomePlusSecurityApiError,
    HomePlusSecurityAuthConfig,
    HomePlusSecurityAuthError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_BASE_URL,
    CONF_APP_VERSION,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_HOME_ID,
    CONF_HOME_NAME,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_SCOPE,
    CONF_TOKEN_URL,
    CONF_USERNAME,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DEFAULT_API_BASE_URL,
    DEFAULT_APP_VERSION,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    DEFAULT_SCOPE,
    DEFAULT_TOKEN_URL,
    DOMAIN,
)
from .coordinator import HomePlusSecurityDataUpdateCoordinator

PLATFORMS: list[str] = ["sensor", "binary_sensor"]
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
        app_version=_entry_value(entry, CONF_APP_VERSION, DEFAULT_APP_VERSION),
        scope=_entry_value(entry, CONF_SCOPE, DEFAULT_SCOPE),
        username=_entry_value(entry, CONF_USERNAME),
        password=_entry_value(entry, CONF_PASSWORD),
    )

    async def _async_token_update(payload: dict[str, Any]) -> None:
        refresh_token = payload.get("refresh_token")
        access_token = payload.get("access_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return

        new_data = dict(entry.data)
        new_data[CONF_REFRESH_TOKEN] = refresh_token
        if isinstance(access_token, str) and access_token:
            new_data[CONF_ACCESS_TOKEN] = access_token

        hass.config_entries.async_update_entry(entry, data=new_data)

    client = HomePlusSecurityApiClient(
        session=session,
        api_base_url=_entry_value(entry, CONF_API_BASE_URL, DEFAULT_API_BASE_URL),
        auth_config=auth_config,
        access_token=_entry_value(entry, CONF_ACCESS_TOKEN),
        refresh_token=_entry_value(entry, CONF_REFRESH_TOKEN),
        token_update_cb=_async_token_update,
    )

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
