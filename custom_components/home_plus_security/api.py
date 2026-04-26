"""App API client for Home + Security."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from aiohttp import ClientConnectionError, ClientConnectorDNSError, ClientError, ClientResponse, ServerTimeoutError
from aiohttp.client import ClientSession

from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    REQUIRED_SECURITY_SCOPE,
    TOKEN_REFRESH_MARGIN,
)

TokenUpdateCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

API_RETRY_ATTEMPTS = 3
API_RETRY_BASE_DELAY_SECONDS = 0.75
AUTH_RETRY_STATUSES = {401, 403}


class HomePlusSecurityApiError(Exception):
    """Raised for API errors."""


class HomePlusSecurityAuthError(HomePlusSecurityApiError):
    """Raised for authentication errors."""


@dataclass(slots=True)
class HomePlusSecurityAuthConfig:
    """Authentication config values."""

    token_url: str
    client_id: str
    client_secret: str
    scope: str


class HomePlusSecurityApiClient:
    """Client handling app OAuth and app API calls."""

    def __init__(
        self,
        session: ClientSession,
        api_base_url: str,
        auth_config: HomePlusSecurityAuthConfig,
        access_token: str,
        refresh_token: str,
        token_update_cb: TokenUpdateCallback | None = None,
    ) -> None:
        self._session = session
        self._api_base_url = api_base_url.rstrip("/")
        self._auth = auth_config
        self._token_update_cb = token_update_cb

        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_expires_at: datetime | None = None
        self._token_scope: set[str] = normalize_scope(self._auth.scope)

    @property
    def refresh_token(self) -> str:
        """Current refresh token."""
        return self._refresh_token

    async def async_ensure_token(self) -> None:
        """Ensure a usable access token is available."""
        if self._access_token and self._token_expires_at:
            if datetime.now(timezone.utc) < (self._token_expires_at - TOKEN_REFRESH_MARGIN):
                return

        if self._refresh_token:
            await self._async_refresh_token()
            return

        raise HomePlusSecurityAuthError("No valid authentication path configured.")

    async def async_get_homesdata(self) -> dict[str, Any]:
        """Fetch homesdata from app API."""
        return await self.async_get_json("homesdata")

    async def async_get_homestatus(self, home_id: str) -> dict[str, Any]:
        """Fetch homestatus from app API."""
        return await self.async_get_json(f"homestatus?home_id={home_id}")

    async def async_get_json(self, endpoint: str) -> dict[str, Any]:
        """GET app API endpoint with bearer auth and one refresh retry."""
        await self.async_ensure_token()
        response = await self._async_api_get(endpoint)

        if response.status not in AUTH_RETRY_STATUSES:
            return await self._async_handle_json_response(response)

        response.release()
        await self._async_refresh_token()

        response = await self._async_api_get(endpoint)
        return await self._async_handle_json_response(response)

    async def _async_api_get(self, endpoint: str) -> ClientResponse:
        url = f"{self._api_base_url}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent": "okhttp/4.12.0",
        }

        for attempt in range(1, API_RETRY_ATTEMPTS + 1):
            try:
                return await self._session.get(url, headers=headers)
            except (ClientConnectorDNSError, ClientConnectionError, ServerTimeoutError, asyncio.TimeoutError, OSError) as err:
                if attempt >= API_RETRY_ATTEMPTS:
                    raise HomePlusSecurityApiError(f"Transient network error while requesting {url}") from err
                await asyncio.sleep(API_RETRY_BASE_DELAY_SECONDS * attempt)
            except ClientError as err:
                raise HomePlusSecurityApiError(f"HTTP error while requesting {url}") from err

    async def _async_refresh_token(self) -> None:
        if not self._refresh_token:
            raise HomePlusSecurityAuthError("Refresh token is missing.")

        payload = {
            "grant_type": "refresh_token",
            CONF_CLIENT_ID: self._auth.client_id,
            CONF_CLIENT_SECRET: self._auth.client_secret,
            "refresh_token": self._refresh_token,
        }
        token_payload = await self._async_post_token(payload)
        await self._async_apply_token_payload(token_payload)

    async def _async_post_token(self, payload: dict[str, str]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": "okhttp/4.12.0",
        }

        try:
            response = await self._session.post(self._auth.token_url, data=payload, headers=headers)
        except ClientError as err:
            raise HomePlusSecurityApiError("Token endpoint request failed") from err

        body = await self._async_handle_json_response(response, auth=True)
        return body

    async def _async_apply_token_payload(self, payload: dict[str, Any]) -> None:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        payload_scope = payload.get("scope")

        if not isinstance(access_token, str) or not access_token:
            raise HomePlusSecurityAuthError("Token payload missing access_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise HomePlusSecurityAuthError("Token payload missing refresh_token")

        self._access_token = access_token
        self._refresh_token = refresh_token

        if payload_scope is not None:
            self._token_scope = normalize_scope(payload_scope)

        if REQUIRED_SECURITY_SCOPE not in self._token_scope:
            scope_text = " ".join(sorted(self._token_scope)) if self._token_scope else "(missing)"
            raise HomePlusSecurityAuthError(
                f"Token scope is not valid for Home + Security. "
                f"Expected '{REQUIRED_SECURITY_SCOPE}', got '{scope_text}'."
            )

        expires_seconds = int(expires_in) if isinstance(expires_in, (int, str)) else 3600
        self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(expires_seconds, 60))

        if self._token_update_cb:
            maybe_awaitable = self._token_update_cb(payload)
            if maybe_awaitable is not None:
                await maybe_awaitable

    async def _async_handle_json_response(self, response: ClientResponse, auth: bool = False) -> dict[str, Any]:
        try:
            body = await response.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - payload comes from external API
            response.release()
            raise HomePlusSecurityApiError("Invalid JSON response from upstream") from err

        if response.status < 400:
            return body

        response.release()

        message = body.get("error") or body.get("message") or body
        if auth or response.status in (401, 403):
            raise HomePlusSecurityAuthError(f"Auth failed ({response.status}): {message}")
        raise HomePlusSecurityApiError(f"Request failed ({response.status}): {message}")


def extract_homes(homesdata: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract homes list from homesdata payload."""
    body = homesdata.get("body")
    if not isinstance(body, dict):
        return []

    homes = body.get("homes")
    if not isinstance(homes, list):
        return []

    return [home for home in homes if isinstance(home, dict)]


def extract_home_bncx_module(home: dict[str, Any]) -> dict[str, Any] | None:
    """Return the BNCX module metadata for a home."""
    modules = home.get("modules")
    if not isinstance(modules, list):
        return None

    for module in modules:
        if isinstance(module, dict) and module.get("type") == "BNCX":
            return module
    return None


def normalize_scope(scope_value: Any) -> set[str]:
    """Normalize scope value (string/list) to a set of scope tokens."""
    if isinstance(scope_value, str):
        return {token for token in scope_value.replace(",", " ").split() if token}
    if isinstance(scope_value, list):
        values: set[str] = set()
        for item in scope_value:
            if isinstance(item, str) and item.strip():
                values.add(item.strip())
        return values
    return set()
