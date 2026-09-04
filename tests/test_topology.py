"""Tests for Home + Security topology normalization."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


_TOPOLOGY_PATH = Path(__file__).parents[1] / "custom_components" / "home_plus_security" / "topology.py"
_SPEC = importlib.util.spec_from_file_location("home_plus_security_topology", _TOPOLOGY_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_TOPOLOGY = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _TOPOLOGY
_SPEC.loader.exec_module(_TOPOLOGY)


class NormalizeModulesTest(unittest.TestCase):
    """Verify topology/status records merge without dropping child modules."""

    def test_merges_by_id_and_keeps_bridged_placeholders(self) -> None:
        modules, modules_by_id = _TOPOLOGY.normalize_modules(
            [
                {
                    "id": "bridge-1",
                    "type": "BNCX",
                    "modules_bridged": ["external-unit-1", "lock-1"],
                },
                {"id": "lock-1", "type": "BNDL", "name": "Gate"},
            ],
            [
                {"id": "bridge-1", "reachable": True},
                {"id": "external-unit-1", "type": "BNEU", "reachable": False},
            ],
        )

        self.assertEqual([module["id"] for module in modules], ["bridge-1", "lock-1", "external-unit-1"])
        self.assertTrue(modules_by_id["bridge-1"]["reachable"])
        self.assertEqual(modules_by_id["lock-1"]["bridge_id"], "bridge-1")
        self.assertEqual(modules_by_id["external-unit-1"]["bridge_id"], "bridge-1")
        self.assertFalse(modules_by_id["external-unit-1"]["reachable"])

    def test_keeps_a_child_without_full_module_metadata(self) -> None:
        modules, modules_by_id = _TOPOLOGY.normalize_modules(
            [{"id": "bridge-1", "modules_bridged": ["unknown-child"]}],
            [],
        )

        self.assertEqual(len(modules), 2)
        self.assertEqual(modules_by_id["unknown-child"], {"id": "unknown-child", "bridge_id": "bridge-1"})
