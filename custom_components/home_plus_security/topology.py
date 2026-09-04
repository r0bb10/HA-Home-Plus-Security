"""Normalize Home + Security topology and status module records."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def normalize_modules(
    topology_modules: Iterable[Any], status_modules: Iterable[Any]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Merge all topology and status modules by stable ID.

    The app API can report a bridge's child module IDs separately from the
    child records themselves. Keep those placeholders so no discovered module
    is silently lost while a later refresh fills in its full metadata.
    """
    modules_by_id: dict[str, dict[str, Any]] = {}

    for source, source_name in ((topology_modules, "topology"), (status_modules, "status")):
        for module in source:
            if not isinstance(module, dict):
                continue
            module_id = _module_id(module)
            if module_id is None:
                continue
            record = modules_by_id.setdefault(module_id, {"id": module_id})
            record[source_name] = dict(module)
            record.update(module)
            record["id"] = module_id

    for module_id, record in list(modules_by_id.items()):
        bridged = record.get("modules_bridged")
        if not isinstance(bridged, list):
            continue
        for child in bridged:
            child_id = _module_id(child) if isinstance(child, dict) else child if isinstance(child, str) else None
            if not child_id:
                continue
            child_record = modules_by_id.setdefault(child_id, {"id": child_id})
            child_record.setdefault("bridge_id", module_id)

    modules = list(modules_by_id.values())
    return modules, modules_by_id


def _module_id(module: dict[str, Any]) -> str | None:
    value = module.get("id")
    if isinstance(value, str) and value:
        return value
    return None
