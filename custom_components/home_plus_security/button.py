"""Button platform for Home + Security."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_CLIENT, DATA_COORDINATOR, DOMAIN
from .device import build_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data[DATA_COORDINATOR]
    client = data[DATA_CLIENT]
    async_add_entities([HomePlusSecurityUnlockButton(coordinator, client, entry.entry_id)])


class HomePlusSecurityUnlockButton(CoordinatorEntity, ButtonEntity):
    """Simple unlock trigger button (first BNDL module)."""

    _attr_has_entity_name = True
    _attr_name = "Unlock"
    _attr_icon = "mdi:gate-open"

    def __init__(self, coordinator, client, entry_id: str) -> None:
        super().__init__(coordinator)
        self._client = client
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_unlock"

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
    def available(self) -> bool:
        return super().available and self._find_unlock_module() is not None

    async def async_press(self) -> None:
        """Trigger unlock on the first discovered BNDL module."""
        home_id = getattr(self.coordinator, "home_id", None)
        if not isinstance(home_id, str) or not home_id:
            raise HomeAssistantError("Home ID is unavailable.")

        module = self._find_unlock_module()
        if module is None:
            raise HomeAssistantError("No BNDL lock module found in this home.")

        module_id = module.get("id")
        if not isinstance(module_id, str) or not module_id:
            raise HomeAssistantError("Lock module ID is unavailable.")

        bridge_id = module.get("bridge")
        if not isinstance(bridge_id, str) or not bridge_id:
            bridge_id = self.coordinator.data.get("bncx_home", {}).get("id")
            if not isinstance(bridge_id, str):
                bridge_id = None

        async def _run_unlock() -> None:
            await self._client.async_unlock_module(
                home_id=home_id,
                module_id=module_id,
                bridge_id=bridge_id,
                timezone_name=getattr(self.hass.config, "time_zone", None),
            )

        try:
            await self.coordinator.async_run_guarded_command(
                label="Unlock",
                command_coro_factory=_run_unlock,
            )
        except Exception as err:  # noqa: BLE001 - show friendly message in HA UI
            raise HomeAssistantError(str(err)) from err

        await self.coordinator.async_request_refresh()

    def _find_unlock_module(self) -> dict | None:
        home = self.coordinator.data.get("home", {})
        modules = home.get("modules", []) if isinstance(home, dict) else []
        if not isinstance(modules, list):
            return None
        for module in modules:
            if isinstance(module, dict) and module.get("type") == "BNDL":
                return module
        return None
