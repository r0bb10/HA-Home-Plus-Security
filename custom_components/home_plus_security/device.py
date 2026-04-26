"""Device metadata helpers for Home + Security."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def _pick_first(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty string-ish value found for keys."""
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def build_device_info(
    *,
    home: dict[str, Any],
    bncx_home: dict[str, Any],
    bncx_status: dict[str, Any],
    fallback_id: str,
) -> DeviceInfo:
    """Build a consistent DeviceInfo payload from available metadata."""
    bncx_id = _pick_first(bncx_home, ("id",)) or _pick_first(bncx_status, ("id",)) or fallback_id
    home_name = _pick_first(bncx_home, ("name",)) or _pick_first(home, ("name",)) or "Classe 300EOS"
    model = home_name
    manufacturer = _pick_first(bncx_home, ("brand", "manufacturer")) or "BTicino / Netatmo"

    sw_version = (
        _pick_first(bncx_status, ("firmware_name", "firmware_version"))
        or _pick_first(bncx_status, ("firmware_revision",))
    )
    hw_version = (
        _pick_first(bncx_status, ("hardware_version",))
        or _pick_first(bncx_home, ("hardware_version",))
    )
    serial_number = _pick_first(bncx_home, ("serial", "serial_number")) or bncx_id
    return DeviceInfo(
        identifiers={(DOMAIN, bncx_id)},
        manufacturer=manufacturer,
        model=model,
        name=home_name,
        serial_number=serial_number,
        sw_version=sw_version,
        hw_version=hw_version,
    )
