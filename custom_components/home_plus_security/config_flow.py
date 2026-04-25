"""Config flow for Home + Security."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
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
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_BASE_URL,
    CONF_APP_TYPE,
    CONF_APP_VERSION,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_HOME_ID,
    CONF_HOME_NAME,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_SCOPE,
    CONF_TOKEN_URL,
    CONF_TURN_URL,
    CONF_USERNAME,
    CONF_WS_URL,
    DEFAULT_API_BASE_URL,
    DEFAULT_APP_TYPE,
    DEFAULT_APP_VERSION,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    DEFAULT_SCOPE,
    DEFAULT_TOKEN_URL,
    DEFAULT_TURN_URL,
    DEFAULT_WS_URL,
    DOMAIN,
    NAME,
)

APP_AUTHORIZE_URL = "https://app.netatmo.net/oauth2/authorize"


class HomePlusSecurityFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle Home + Security config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow handler state."""
        self._pending_input: dict[str, Any] | None = None
        self._home_choices: list[dict[str, str]] = []
        self._external_data: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get options flow."""
        return HomePlusSecurityOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle user step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            self._pending_input = _default_user_input()
            return await self.async_step_auth()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            errors={},
        )

    async def async_step_auth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start/handle external OAuth callback."""
        if not self._pending_input:
            return self.async_abort(reason="oauth_failed")

        if user_input is not None:
            self._external_data = user_input
            next_step = "authorize_rejected" if "error" in user_input else "finish_auth"
            return self.async_external_step_done(next_step_id=next_step)

        implementation = self._build_oauth_implementation(self._pending_input)
        url = await implementation.async_generate_authorize_url(self.flow_id)
        return self.async_external_step(step_id="auth", url=url)

    async def async_step_finish_auth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Exchange OAuth code for tokens and continue setup."""
        if not self._pending_input or not self._external_data:
            return self.async_abort(reason="oauth_failed")

        try:
            implementation = self._build_oauth_implementation(self._pending_input)
            token = await implementation.async_resolve_external_data(self._external_data)
        except (OAuth2TokenRequestError, ClientError):
            return self.async_abort(reason="oauth_failed")

        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            return self.async_abort(reason="oauth_failed")

        self._pending_input[CONF_ACCESS_TOKEN] = access_token
        self._pending_input[CONF_REFRESH_TOKEN] = refresh_token

        try:
            homes = await self._async_fetch_supported_homes(self._pending_input)
        except HomePlusSecurityAuthError:
            return self.async_abort(reason="oauth_failed")
        except HomePlusSecurityApiError:
            return self.async_abort(reason="cannot_connect")

        if not homes:
            return self.async_abort(reason="no_bncx_homes")
        if len(homes) == 1:
            return await self._async_create_entry_for_home(self._pending_input, homes[0])

        self._home_choices = homes
        return await self.async_step_select_home()

    async def async_step_authorize_rejected(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle external auth rejection."""
        return self.async_abort(reason="user_rejected_authorize")

    async def async_step_select_home(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Select home when account has multiple supported homes."""
        if not self._pending_input or not self._home_choices:
            return self.async_abort(reason="no_homes")

        errors: dict[str, str] = {}

        if user_input is not None:
            selected_home_id = str(user_input.get(CONF_HOME_ID, ""))
            selected = next((home for home in self._home_choices if home[CONF_HOME_ID] == selected_home_id), None)
            if selected is None:
                errors["base"] = "invalid_home"
            else:
                return await self._async_create_entry_for_home(self._pending_input, selected)

        options = [{"value": home[CONF_HOME_ID], "label": home[CONF_HOME_NAME]} for home in self._home_choices]

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOME_ID): SelectSelector(
                    SelectSelectorConfig(options=options)
                )
            }
        )
        return self.async_show_form(step_id="select_home", data_schema=data_schema, errors=errors)

    def _build_oauth_implementation(self, user_input: dict[str, Any]) -> LocalOAuth2Implementation:
        """Build OAuth implementation for app stack."""
        return LocalOAuth2Implementation(
            hass=self.hass,
            domain=DOMAIN,
            client_id=str(user_input.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID)),
            client_secret=str(user_input.get(CONF_CLIENT_SECRET, DEFAULT_CLIENT_SECRET)),
            authorize_url=APP_AUTHORIZE_URL,
            token_url=str(user_input.get(CONF_TOKEN_URL, DEFAULT_TOKEN_URL)),
        )

    async def _async_fetch_supported_homes(self, user_input: dict[str, Any]) -> list[dict[str, str]]:
        """Validate auth and return homes containing BNCX modules."""
        session = async_get_clientsession(self.hass)

        auth_config = HomePlusSecurityAuthConfig(
            token_url=str(user_input.get(CONF_TOKEN_URL, DEFAULT_TOKEN_URL)),
            client_id=str(user_input.get(CONF_CLIENT_ID, DEFAULT_CLIENT_ID)),
            client_secret=str(user_input.get(CONF_CLIENT_SECRET, DEFAULT_CLIENT_SECRET)),
            app_version=str(user_input.get(CONF_APP_VERSION, DEFAULT_APP_VERSION)),
            scope=str(user_input.get(CONF_SCOPE, DEFAULT_SCOPE)),
            username=str(user_input.get(CONF_USERNAME, "")),
            password=str(user_input.get(CONF_PASSWORD, "")),
        )

        client = HomePlusSecurityApiClient(
            session=session,
            api_base_url=str(user_input.get(CONF_API_BASE_URL, DEFAULT_API_BASE_URL)),
            auth_config=auth_config,
            access_token=str(user_input.get(CONF_ACCESS_TOKEN, "")),
            refresh_token=str(user_input.get(CONF_REFRESH_TOKEN, "")),
        )

        homesdata = await client.async_get_homesdata()
        homes = extract_homes(homesdata)

        choices: list[dict[str, str]] = []
        for home in homes:
            home_id = str(home.get("id", "")).strip()
            if not home_id:
                continue

            home_name = str(home.get("name", home_id)).strip() or home_id
            bncx = extract_home_bnc_module(home)

            # Some token contexts may return homes without full module topology in homesdata.
            # Fallback to homestatus lookup before excluding the home.
            if not bncx:
                try:
                    homestatus = await client.async_get_homestatus(home_id)
                except HomePlusSecurityApiError:
                    homestatus = {}
                bncx = extract_bncx_from_homestatus(homestatus)
                if not bncx:
                    continue

            bncx_name = str(bncx.get("name", bncx.get("id", "BNCX"))).strip()
            choices.append({CONF_HOME_ID: home_id, CONF_HOME_NAME: f"{home_name} ({bncx_name})"})

        return choices

    async def _async_create_entry_for_home(
        self,
        user_input: dict[str, Any],
        selected_home: dict[str, str],
    ) -> ConfigFlowResult:
        """Create config entry after choosing a home."""
        data = dict(user_input)
        data[CONF_HOME_ID] = selected_home[CONF_HOME_ID]
        data[CONF_HOME_NAME] = selected_home[CONF_HOME_NAME]

        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=f"{NAME} ({selected_home[CONF_HOME_NAME]})", data=data)


class HomePlusSecurityOptionsFlow(OptionsFlow):
    """Handle Home + Security options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        data = self.config_entry.data

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_APP_VERSION,
                    default=options.get(CONF_APP_VERSION, data.get(CONF_APP_VERSION, DEFAULT_APP_VERSION)),
                ): str,
                vol.Required(
                    CONF_APP_TYPE,
                    default=options.get(CONF_APP_TYPE, data.get(CONF_APP_TYPE, DEFAULT_APP_TYPE)),
                ): str,
                vol.Required(
                    CONF_SCOPE,
                    default=options.get(CONF_SCOPE, data.get(CONF_SCOPE, DEFAULT_SCOPE)),
                ): str,
                vol.Required(
                    CONF_TOKEN_URL,
                    default=options.get(CONF_TOKEN_URL, data.get(CONF_TOKEN_URL, DEFAULT_TOKEN_URL)),
                ): str,
                vol.Required(
                    CONF_API_BASE_URL,
                    default=options.get(CONF_API_BASE_URL, data.get(CONF_API_BASE_URL, DEFAULT_API_BASE_URL)),
                ): str,
                vol.Required(
                    CONF_WS_URL,
                    default=options.get(CONF_WS_URL, data.get(CONF_WS_URL, DEFAULT_WS_URL)),
                ): str,
                vol.Required(
                    CONF_TURN_URL,
                    default=options.get(CONF_TURN_URL, data.get(CONF_TURN_URL, DEFAULT_TURN_URL)),
                ): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)


def _build_user_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Deprecated helper retained for backwards compatibility."""
    return vol.Schema({})


def _default_user_input() -> dict[str, Any]:
    """Return internal defaults used for official-style setup UX."""
    return {
        CONF_CLIENT_ID: DEFAULT_CLIENT_ID,
        CONF_CLIENT_SECRET: DEFAULT_CLIENT_SECRET,
        CONF_APP_VERSION: DEFAULT_APP_VERSION,
        CONF_APP_TYPE: DEFAULT_APP_TYPE,
        CONF_SCOPE: DEFAULT_SCOPE,
        CONF_TOKEN_URL: DEFAULT_TOKEN_URL,
        CONF_API_BASE_URL: DEFAULT_API_BASE_URL,
        CONF_WS_URL: DEFAULT_WS_URL,
        CONF_TURN_URL: DEFAULT_TURN_URL,
    }


def extract_home_bnc_module(home: dict[str, Any]) -> dict[str, Any] | None:
    """Return BNCX module from home metadata."""
    return extract_home_bncx_module(home)


def extract_bncx_from_homestatus(homestatus: dict[str, Any]) -> dict[str, Any] | None:
    """Return BNCX module from homestatus payload."""
    body = homestatus.get("body")
    if not isinstance(body, dict):
        return None

    home = body.get("home")
    if not isinstance(home, dict):
        return None

    modules = home.get("modules")
    if not isinstance(modules, list):
        return None

    for module in modules:
        if isinstance(module, dict) and module.get("type") == "BNCX":
            return module

    return None
