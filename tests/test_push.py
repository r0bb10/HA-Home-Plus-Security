"""Tests for Home + Security push payload parsing."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


_PUSH_PATH = Path(__file__).parents[1] / "custom_components" / "home_plus_security" / "push.py"
_SPEC = importlib.util.spec_from_file_location("home_plus_security_push", _PUSH_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_PUSH = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _PUSH
_SPEC.loader.exec_module(_PUSH)


class ParsePushEventTest(unittest.TestCase):
    """Verify known Home + Security app_camera event formats."""

    def test_parses_rtc_offer(self) -> None:
        event = _PUSH.parse_push_event(
            {
                "push_type": "BNC1-rtc",
                "extra_params": {
                    "device_id": "bridge-id",
                    "session_id": "session-id",
                    "tag_id": "tag-id",
                    "correlation_id": "correlation-id",
                    "data": {
                        "type": "offer",
                        "session_description": {
                            "module_id": "outdoor-unit-id",
                            "sdp": "offer-sdp",
                            "modules": ["outdoor-unit-id", 3],
                        },
                    },
                },
            }
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_type, "offer")
        self.assertEqual(event.module_id, "outdoor-unit-id")
        self.assertEqual(event.session_id, "session-id")
        self.assertEqual(event.sdp, "offer-sdp")
        self.assertEqual(event.modules, ("outdoor-unit-id",))

    def test_parses_incoming_call_with_images(self) -> None:
        event = _PUSH.parse_push_event(
            {
                "push_type": "BNC1-incoming_call",
                "extra_params": {
                    "event_type": "incoming_call",
                    "device_id": "bridge-id",
                    "session_id": "session-id",
                    "event_id": "event-id",
                    "timestamp": 1_725_000_000,
                    "snapshot_url": "https://example.invalid/snapshot",
                    "vignette_url": "https://example.invalid/vignette",
                },
            }
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.event_type, "incoming_call")
        self.assertEqual(event.snapshot_url, "https://example.invalid/snapshot")
        self.assertEqual(event.vignette_url, "https://example.invalid/vignette")
        self.assertEqual(event.timestamp, 1_725_000_000)

    def test_ignores_unknown_payload(self) -> None:
        self.assertIsNone(_PUSH.parse_push_event({"extra_params": {"event_type": "online"}}))
