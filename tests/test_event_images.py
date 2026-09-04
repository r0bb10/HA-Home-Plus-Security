"""Tests for event image extraction."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


_PATH = Path(__file__).parents[1] / "custom_components" / "home_plus_security" / "event_images.py"
_SPEC = importlib.util.spec_from_file_location("home_plus_security_event_images", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


class EventImagesTest(unittest.TestCase):
    """Verify media is discovered without relying on a first subevent."""

    def test_selects_media_from_later_subevent(self) -> None:
        media = _MODULE.find_latest_event_media(
            [
                {
                    "id": "event-1",
                    "time": 1_725_000_000,
                    "subevents": [
                        {"type": "noise"},
                        {
                            "module_id": "module-1",
                            "snapshot": {"url": "https://example.invalid/snapshot", "expires_at": 1_725_000_100},
                            "vignette": {"url": "https://example.invalid/vignette"},
                        },
                    ],
                }
            ]
        )

        self.assertIsNotNone(media)
        assert media is not None
        self.assertEqual(media.event_id, "event-1")
        self.assertEqual(media.module_id, "module-1")
        self.assertEqual(media.snapshot_url, "https://example.invalid/snapshot")
        self.assertEqual(media.vignette_url, "https://example.invalid/vignette")

    def test_selects_top_level_media(self) -> None:
        media = _MODULE.find_latest_event_media(
            [{"id": "event-1", "module_id": "module-1", "snapshot_url": "https://example.invalid/snapshot"}]
        )

        self.assertIsNotNone(media)
        assert media is not None
        self.assertEqual(media.snapshot_url, "https://example.invalid/snapshot")
