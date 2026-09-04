"""Tests for diagnostics redaction."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


_COMPONENT_PATH = Path(__file__).parents[1] / "custom_components" / "home_plus_security"
_PACKAGE_NAME = "home_plus_security_diagnostics_test"
_PACKAGE = types.ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [str(_COMPONENT_PATH)]
sys.modules[_PACKAGE_NAME] = _PACKAGE

_HOMEASSISTANT = types.ModuleType("homeassistant")
_HOMEASSISTANT.__path__ = []
sys.modules["homeassistant"] = _HOMEASSISTANT
_CONFIG_ENTRIES = types.ModuleType("homeassistant.config_entries")
setattr(_CONFIG_ENTRIES, "ConfigEntry", object)
sys.modules["homeassistant.config_entries"] = _CONFIG_ENTRIES
_CORE = types.ModuleType("homeassistant.core")
setattr(_CORE, "HomeAssistant", object)
sys.modules["homeassistant.core"] = _CORE


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{_PACKAGE_NAME}.{name}", _COMPONENT_PATH / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_load_module("const")
_DIAGNOSTICS = _load_module("diagnostics")


class DiagnosticsRedactionTest(unittest.TestCase):
    """Verify diagnostics never include raw credentials or identifiers."""

    def test_redacts_credentials_and_home_identifier(self) -> None:
        redacted = _DIAGNOSTICS._redact_mapping(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "client_secret": "secret",
                "home_id": "home",
                "safe": "value",
            }
        )

        self.assertEqual(redacted["access_token"], "**REDACTED**")
        self.assertEqual(redacted["refresh_token"], "**REDACTED**")
        self.assertEqual(redacted["client_secret"], "**REDACTED**")
        self.assertEqual(redacted["home_id"], "**REDACTED**")
        self.assertEqual(redacted["safe"], "value")

    def test_push_summary_omits_signed_urls_and_fingerprints_ids(self) -> None:
        summary = _DIAGNOSTICS._push_summary(
            {
                "active_calls": {"module-id": {}},
                "last_event": {
                    "type": "incoming_call",
                    "event_id": "event-id",
                    "module_id": "module-id",
                    "session_id": "session-id",
                    "snapshot_url": "https://example.invalid/?secret=1",
                    "vignette_url": "https://example.invalid/?secret=2",
                },
            }
        )

        self.assertEqual(summary["active_call_count"], 1)
        self.assertEqual(summary["last_event"]["type"], "incoming_call")
        self.assertTrue(summary["last_event"]["event_id"].startswith("sha256:"))
        self.assertNotIn("snapshot_url", summary["last_event"])
        self.assertNotIn("vignette_url", summary["last_event"])
