import asyncio
import os
import sys
import unittest
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from core import background_services


class _FakeResponse:
    def __init__(self, payload=None):
        self.status_code = 200
        self._payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return dict(self._payload)


class _FakeAsyncClient:
    def __init__(self, calls, state, error=None):
        self._calls = calls
        self._state = state
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        models = []
        if self._state.get("loaded"):
            models.append({"name": self._state.get("model", "apexbot:latest")})
        return _FakeResponse({"models": models})

    async def post(self, url, json):
        self._calls.append({"url": url, "json": dict(json or {})})
        if self._error is not None:
            raise self._error
        keep_alive = int((json or {}).get("keep_alive", 0) or 0)
        if keep_alive == -1:
            self._state["loaded"] = True
            self._state["model"] = str((json or {}).get("model") or "apexbot:latest")
        elif keep_alive == 0:
            self._state["loaded"] = False
        return _FakeResponse()


class BackgroundServicesOllamaContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await background_services.shutdown_all_services()
        background_services._TASKS.clear()
        background_services._DELAYED_TASKS.clear()
        background_services._SERVICE_ERRORS.clear()

    async def asyncTearDown(self):
        await background_services.shutdown_all_services()
        background_services._TASKS.clear()
        background_services._DELAYED_TASKS.clear()
        background_services._SERVICE_ERRORS.clear()

    async def test_ollama_service_pins_model_on_start_and_unloads_on_stop(self):
        calls = []
        state = {"loaded": False, "model": "apexbot:latest"}

        def _client_factory(timeout=8.0):
            return _FakeAsyncClient(calls, state)

        with patch("core.config_manager.get_config", return_value={"stocks": {"crypto": {"ollama_model": "apexbot:latest"}}}), \
             patch("core.ollama.ollama_url", return_value="http://ollama.local/api/generate"), \
             patch("httpx.AsyncClient", side_effect=_client_factory):
            started = await background_services.ensure_started("ollama")
            self.assertTrue(started)
            await asyncio.sleep(0.05)
            self.assertIn("ollama", background_services.service_status()["running"])
            await background_services.stop_service("ollama")

        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0]["json"]["model"], "apexbot:latest")
        self.assertEqual(calls[0]["json"]["prompt"], "ping")
        self.assertEqual(calls[0]["json"]["keep_alive"], -1)
        self.assertEqual(calls[-1]["json"]["prompt"], "")
        self.assertEqual(calls[-1]["json"]["keep_alive"], 0)

    async def test_ollama_service_reports_keepalive_failures(self):
        state = {"loaded": False, "model": "apexbot:latest"}

        def _client_factory(timeout=8.0):
            return _FakeAsyncClient([], state, error=RuntimeError("ollama down"))

        with patch("core.config_manager.get_config", return_value={"stocks": {"crypto": {"ollama_model": "apexbot:latest"}}}), \
             patch("core.ollama.ollama_url", return_value="http://ollama.local/api/generate"), \
             patch("httpx.AsyncClient", side_effect=_client_factory):
            await background_services.ensure_started("ollama")
            await asyncio.sleep(0.05)
            status = background_services.service_status()
            self.assertIn("ollama", status["running"])
            self.assertIn("ollama", status["errors"])
            self.assertIn("ollama down", status["errors"]["ollama"])
            await background_services.stop_service("ollama")


if __name__ == "__main__":
    unittest.main()
