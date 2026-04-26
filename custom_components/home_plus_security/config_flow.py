"""Config flow for Home + Security."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    LocalOAuth2Implementation,
    OAuth2TokenRequestError,
)
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .api import (
    HomePlusSecurityApiClient,
    HomePlusSecurityApiError,
    HomePlusSecurityAuthConfig,
    HomePlusSecurityAuthError,
    extract_home_bncx_module,
    extract_homes,
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
    DEFAULT_API_BASE_URL,
    DEFAULT_APP_TYPE,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    DEFAULT_SCOPE,
    DEFAULT_TOKEN_URL,
    DOMAIN,
    NAME,
    REQUIRED_SECURITY_SCOPE,
)

_LOGGER = logging.getLogger(__name__)
APP_AUTHORIZE_URL = "https://app.netatmo.net/oauth2/authorize"
_IMPL_KEY = f"{DOMAIN}_local"
_DATA_LOCAL_IMPL_REGISTERED = f"{DOMAIN}_local_impl_registered"


class HomePlusSecurityOAuth2Implementation(LocalOAuth2Implementation):
    """OAuth2 implementation that enforces app-security authorize params."""

    @property
    def name(self) -> str:
        """Name shown by implementation picker."""
        return NAME

    @property
    def extra_authorize_data(self) -> dict:
        """Append required app-security OAuth authorize params."""
        return {
            "scope": DEFAULT_SCOPE,
            "app_type": DEFAULT_APP_TYPE,
        }


class HomePlusSecurityFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle Home + Security config flow."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        super().__init__()
        self._home_choices: list[dict[str, str]] = []
        self._entry_data: dict[str, Any] | None = None
        self._hps_reauth_entry_id: str | None = None

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user step."""
        self._ensure_local_oauth_impl_registered()
        return await super().async_step_user(user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        self._hps_reauth_entry_id = self.context.get("entry_id")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")

        return await self.async_step_user()

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Create config entry from OAuth token after validation."""
        token = data.get("token")
        if not isinstance(token, dict):
            return self.async_abort(reason="oauth_failed")

        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            return self.async_abort(reason="oauth_failed")

        token_scopes = normalize_scope(token.get("scope"))
        if REQUIRED_SECURITY_SCOPE not in token_scopes:
            return self.async_abort(reason="wrong_scope")

        entry_data = _default_entry_data()
        entry_data[CONF_ACCESS_TOKEN] = access_token
        entry_data[CONF_REFRESH_TOKEN] = refresh_token
        if token_scopes:
            entry_data[CONF_SCOPE] = " ".join(sorted(token_scopes))

        try:
            homes = await self._async_fetch_supported_homes(entry_data)
        except (OAuth2TokenRequestError, HomePlusSecurityAuthError, ClientError):
            return self.async_abort(reason="oauth_failed")
        except HomePlusSecurityApiError:
            return self.async_abort(reason="cannot_connect")

        if not homes:
            return self.async_abort(reason="no_bncx_homes")
        if len(homes) == 1:
            return await self._async_create_or_update_entry(entry_data, homes[0])

        self._entry_data = entry_data
        self._home_choices = homes
        return await self.async_step_select_home()

    async def async_step_select_home(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select home when account has multiple supported homes."""
        if not self._entry_data or not self._home_choices:
            return self.async_abort(reason="no_homes")

        errors: dict[str, str] = {}
        if user_input is not None:
            selected_home_id = str(user_input.get(CONF_HOME_ID, ""))
            selected = next(
                (
                    home
                    for home in self._home_choices
                    if home[CONF_HOME_ID] == selected_home_id
                ),
                None,
            )
            if selected is None:
                errors["base"] = "invalid_home"
            else:
                return await self._async_create_or_update_entry(self._entry_data, selected)

        options = [
            {"value": home[CONF_HOME_ID], "label": home[CONF_HOME_NAME]}
            for home in self._home_choices
        ]
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOME_ID): SelectSelector(
                    SelectSelectorConfig(options=options)
                )
            }
        )
        return self.async_show_form(
            step_id="select_home", data_schema=data_schema, errors=errors
        )

    async def _async_fetch_supported_homes(
        self, entry_data: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Return homes that expose a BNCX module."""
        session = async_get_clientsession(self.hass)
        auth_config = HomePlusSecurityAuthConfig(
            token_url=str(entry_data.get(CONF_TOKEN_URL, DEFAULT_TOKEN_URL)),
            client_id=str(entry_data.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID)),
            client_secret=str(entry_data.get(CONF_CLIENT_SECRET, DEFAULT_CLIENT_SECRET)),
            scope=str(entry_data.get(CONF_SCOPE, DEFAULT_SCOPE)),
        )

        client = HomePlusSecurityApiClient(
            session=session,
            api_base_url=str(entry_data.get(CONF_API_BASE_URL, DEFAULT_API_BASE_URL)),
            auth_config=auth_config,
            access_token=str(entry_data.get(CONF_ACCESS_TOKEN, "")),
            refresh_token=str(entry_data.get(CONF_REFRESH_TOKEN, "")),
        )

        homesdata = await client.async_get_homesdata()
        homes = extract_homes(homesdata)

        bncx_choices: list[dict[str, str]] = []
        for home in homes:
            home_id = str(home.get("id", "")).strip()
            if not home_id:
                continue

            home_name = str(home.get("name", home_id)).strip() or home_id
            if extract_home_bncx_module(home):
                bncx_choices.append({CONF_HOME_ID: home_id, CONF_HOME_NAME: home_name})

        return bncx_choices

    async def _async_create_or_update_entry(
        self,
        entry_data: dict[str, Any],
        selected_home: dict[str, str],
    ) -> ConfigFlowResult:
        """Create config entry (or update existing on reauth)."""
        selected_home_id = selected_home[CONF_HOME_ID]
        selected_home_name = selected_home[CONF_HOME_NAME]
        selected_unique_id = _entry_unique_id(selected_home_id)

        data = dict(entry_data)
        data[CONF_HOME_ID] = selected_home_id
        data[CONF_HOME_NAME] = selected_home_name

        if self._hps_reauth_entry_id:
            reauth_entry = self.hass.config_entries.async_get_entry(self._hps_reauth_entry_id)
            if reauth_entry is None:
                return self.async_abort(reason="oauth_failed")

            duplicate_entry = self._find_entry_by_home_id(selected_home_id)
            if duplicate_entry and duplicate_entry.entry_id != reauth_entry.entry_id:
                return self.async_abort(reason="already_configured_home")

            merged = dict(reauth_entry.data)
            merged.update(data)
            self.hass.config_entries.async_update_entry(
                reauth_entry,
                data=merged,
                title=f"{NAME} ({selected_home_name})",
                unique_id=selected_unique_id,
            )
            await self.hass.config_entries.async_reload(reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        if self._find_entry_by_home_id(selected_home_id):
            return self.async_abort(reason="already_configured_home")

        await self.async_set_unique_id(selected_unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=f"{NAME} ({selected_home_name})", data=data
        )

    def _find_entry_by_home_id(self, home_id: str) -> ConfigEntry | None:
        """Return an existing config entry that already targets the same home."""
        for entry in self._async_current_entries():
            if str(entry.data.get(CONF_HOME_ID, "")) == home_id:
                return entry
        return None

    @callback
    def _ensure_local_oauth_impl_registered(self) -> None:
        """Register the local OAuth implementation once."""
        if self.hass.data.get(_DATA_LOCAL_IMPL_REGISTERED):
            return

        implementation = HomePlusSecurityOAuth2Implementation(
            hass=self.hass,
            domain=_IMPL_KEY,
            client_id=DEFAULT_CLIENT_ID,
            client_secret=DEFAULT_CLIENT_SECRET,
            authorize_url=APP_AUTHORIZE_URL,
            token_url=DEFAULT_TOKEN_URL,
        )
        self.async_register_implementation(self.hass, implementation)
        self.hass.data[_DATA_LOCAL_IMPL_REGISTERED] = True

def _default_entry_data() -> dict[str, Any]:
    """Default entry values used by API client setup."""
    return {
        CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
        CONF_CLIENT_SECRET: DEFAULT_CLIENT_SECRET,
        CONF_SCOPE: DEFAULT_SCOPE,
        CONF_TOKEN_URL: DEFAULT_TOKEN_URL,
        CONF_API_BASE_URL: DEFAULT_API_BASE_URL,
    }


def _entry_unique_id(home_id: str) -> str:
    """Return a stable unique id for one configured home."""
    return f"{DOMAIN}_{home_id}"
