"""Helpers for optional Home + Security entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DEFAULT_DIAGNOSTICS, OPT_DIAGNOSTICS, OPT_DIAGNOSTICS_CONFIGURED


def selected_diagnostics(entry: ConfigEntry) -> set[str]:
    """Return the valid diagnostic options selected by the user."""
    default = [] if entry.options.get(OPT_DIAGNOSTICS_CONFIGURED) is True else DEFAULT_DIAGNOSTICS
    values = entry.options.get(OPT_DIAGNOSTICS, default)
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str)}


def remove_unselected_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    domain: str,
    optional_unique_ids: dict[str, str],
) -> set[str]:
    """Remove unselected optional entities so they do not remain visible."""
    selected = selected_diagnostics(entry)
    registry = er.async_get(hass)
    for option, unique_id in optional_unique_ids.items():
        if option in selected:
            continue
        entity_id = registry.async_get_entity_id(domain, "home_plus_security", unique_id)
        if entity_id is not None:
            registry.async_remove(entity_id)
    return selected


def remove_entity_if_disabled(
    hass: HomeAssistant,
    entry: ConfigEntry,
    domain: str,
    option: str,
    unique_id: str,
    *,
    default: bool,
) -> bool:
    """Remove an entity when its exposure option is disabled."""
    enabled = entry.options.get(option, default)
    if enabled is True:
        return True
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(domain, "home_plus_security", unique_id)
    if entity_id is not None:
        registry.async_remove(entity_id)
    return False
