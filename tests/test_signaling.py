"""Tests for signaling connection recovery."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest


_COMPONENT_PATH = Path(__file__).parents[1] / "custom_components" / "home_plus_security"
_PACKAGE_NAME = "home_plus_security_signaling_test"
_PACKAGE = types.ModuleType(_PACKAGE_NAME)
_PACKAGE.__path__ = [str(_COMPONENT_PATH)]
sys.modules[_PACKAGE_NAME] = _PACKAGE


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(f"{_PACKAGE_NAME}.{name}", _COMPONENT_PATH / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_load_module("const")
_API = types.ModuleType(f"{_PACKAGE_NAME}.api")
setattr(_API, "HomePlusSecurityApiClient", object)
sys.modules[_API.__name__] = _API
_SIGNALING = _load_module("signaling")


class SignalingRecoveryTest(unittest.IsolatedAsyncioTestCase):
    """Verify listener failures leave the client ready to reconnect."""

    async def test_listener_exit_clears_stale_session_and_fails_offer(self) -> None:
        client = _SIGNALING.HomePlusSecuritySignalingClient(session=None, client=None)
        ws = object()
        pending_offer = asyncio.get_running_loop().create_future()
        client._ws = ws
        client._session_id = "session-id"
        client._tag_id = "tag-id"
        client._device_id = "device-id"
        client._correlation_id = "correlation-id"
        client._session_callbacks["session-id"] = lambda _: None
        client._pending_offer_ack = pending_offer

        client._handle_listener_exit(ws)

        self.assertIsNone(client._ws)
        self.assertIsNone(client._session_id)
        self.assertIsNone(client._tag_id)
        self.assertIsNone(client._device_id)
        self.assertIsNone(client._correlation_id)
        self.assertEqual(client._session_callbacks, {})
        with self.assertRaises(_SIGNALING.HomePlusSecuritySignalingError):
            await pending_offer

    async def test_old_listener_cannot_clear_a_replacement_connection(self) -> None:
        client = _SIGNALING.HomePlusSecuritySignalingClient(session=None, client=None)
        old_ws = object()
        replacement_ws = object()
        client._ws = replacement_ws

        client._handle_listener_exit(old_ws)

        self.assertIs(client._ws, replacement_ws)
