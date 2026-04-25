"""Data coordinator for Home + Security."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import HomePlusSecurityApiClient
from .const import COORDINATOR_UPDATE_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the API."""
        homesdata = await self.client.async_get_homesdata()
        homestatus = await self.client.async_get_homestatus(self.home_id)

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
        bncx_id = str(bncx_home.get("id")) if isinstance(bncx_home, dict) and bncx_home.get("id") else None
        bncx_status = next(
            (
                module
                for module in status_modules
                if isinstance(module, dict) and bncx_id and str(module.get("id")) == bncx_id
            ),
            None,
        )

        return {
            "home": selected_home,
            "status_home": status_home if isinstance(status_home, dict) else {},
            "bncx_home": bncx_home if isinstance(bncx_home, dict) else {},
            "bncx_status": bncx_status if isinstance(bncx_status, dict) else {},
        }
