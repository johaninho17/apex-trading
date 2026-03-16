import os
import sys
import types
import unittest
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import main


class _DummyApp:
    def __init__(self):
        self.calls = []

    def include_router(self, router, prefix=None, tags=None):
        self.calls.append({
            "router": router,
            "prefix": prefix,
            "tags": tags,
        })


class RoutePackProfileContracts(unittest.TestCase):
    def test_route_pack_flags_default_to_crypto_first_profile(self):
        with patch("core.config_manager.get_config", return_value={}):
            flags = main._route_pack_flags()

        self.assertTrue(flags["crypto"])
        self.assertTrue(flags["crypto_compat"])
        self.assertTrue(flags["system"])
        self.assertFalse(flags["kalshi"])
        self.assertFalse(flags["dfs"])
        self.assertFalse(flags["polymarket"])
        self.assertFalse(flags["legacy_alpaca"])
        self.assertFalse(flags["stocks_ml"])

    def test_route_pack_env_can_enable_optional_domains_without_changing_defaults(self):
        with patch("core.config_manager.get_config", return_value={}), patch.dict(
            os.environ,
            {"APEX_ENABLE_ROUTE_PACKS": "dfs,kalshi"},
            clear=False,
        ):
            flags = main._route_pack_flags()

        self.assertTrue(flags["crypto"])
        self.assertTrue(flags["dfs"])
        self.assertTrue(flags["kalshi"])
        self.assertFalse(flags["polymarket"])

    def test_mount_enabled_route_packs_only_imports_enabled_modules(self):
        flags = {key: False for key in main._DEFAULT_ROUTE_PACKS}
        flags["crypto"] = True
        flags["system"] = True
        app = _DummyApp()
        imported = []

        def _fake_import(name):
            imported.append(name)
            return types.SimpleNamespace(router=f"{name}:router", compat_router=f"{name}:compat")

        with patch.object(main, "_route_pack_flags", return_value=flags), patch.object(main.importlib, "import_module", side_effect=_fake_import):
            mounted = main._mount_enabled_route_packs(app)

        self.assertEqual(imported, ["routers.crypto", "routers.system"])
        self.assertTrue(mounted["crypto"])
        self.assertTrue(mounted["system"])
        self.assertFalse(mounted["kalshi"])
        self.assertEqual(
            [call["prefix"] for call in app.calls],
            ["/api/v1/crypto", "/api/v1/system"],
        )


if __name__ == "__main__":
    unittest.main()
