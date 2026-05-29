"""Unit-Tests für den `namenconfig`-Loader in ``botcore.feature_config``.

Validates: Requirements 2.5, 2.6, 2.7
"""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import patch


def _make_fake_namenconfig(**attrs: object) -> types.ModuleType:
    """Erzeugt ein Fake-Modul mit den angegebenen Attributen."""
    module = types.ModuleType("namenconfig")
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


class NamenconfigLoaderTests(unittest.TestCase):
    """Verhalten des defensiven Loaders in ``botcore.feature_config``."""

    LOGGER_NAME = "botcore.feature_config"

    def _reload_feature_config(self):
        """Lädt ``botcore.feature_config`` neu, sodass ``NAMENCONFIG`` neu berechnet wird."""
        import botcore.feature_config as feature_config

        return importlib.reload(feature_config)

    def tearDown(self) -> None:
        """Stellt nach jedem Test den echten Modul-Zustand wieder her."""
        try:
            import botcore.feature_config as feature_config

            importlib.reload(feature_config)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Fall 1: gültige Werte, beide Toggles auf False
    # ------------------------------------------------------------------
    def test_valid_values_both_false_are_honored(self) -> None:
        fake = _make_fake_namenconfig(
            boss_switch_enabled=False,
            name_normalization_enabled=False,
        )
        with patch.dict(sys.modules, {"namenconfig": fake}):
            module = self._reload_feature_config()

            self.assertEqual(
                module.NAMENCONFIG,
                {
                    "boss_switch_enabled": False,
                    "name_normalization_enabled": False,
                },
            )
            self.assertFalse(module.boss_switch_enabled())
            self.assertFalse(module.name_normalization_enabled())

    # ------------------------------------------------------------------
    # Fall 2: Modul fehlt komplett -> Defaults + Warning
    # ------------------------------------------------------------------
    def test_missing_module_returns_defaults_and_logs_warning(self) -> None:
        # ``sys.modules[name] = None`` erzwingt einen ImportError beim Import.
        with patch.dict(sys.modules, {"namenconfig": None}):
            with self.assertLogs(self.LOGGER_NAME, level="WARNING") as log:
                module = self._reload_feature_config()

            self.assertEqual(
                module.NAMENCONFIG,
                {
                    "boss_switch_enabled": True,
                    "name_normalization_enabled": True,
                },
            )
            self.assertTrue(module.boss_switch_enabled())
            self.assertTrue(module.name_normalization_enabled())

            joined = "\n".join(log.output)
            self.assertIn("namenconfig", joined)

    # ------------------------------------------------------------------
    # Fall 3: einzelner Eintrag fehlt -> dieser Key fällt auf Default zurück
    # ------------------------------------------------------------------
    def test_missing_single_entry_falls_back_to_default(self) -> None:
        # ``boss_switch_enabled`` ist gesetzt (False), ``name_normalization_enabled`` fehlt.
        fake = _make_fake_namenconfig(boss_switch_enabled=False)

        with patch.dict(sys.modules, {"namenconfig": fake}):
            with self.assertLogs(self.LOGGER_NAME, level="WARNING") as log:
                module = self._reload_feature_config()

            self.assertEqual(
                module.NAMENCONFIG,
                {
                    "boss_switch_enabled": False,
                    "name_normalization_enabled": True,
                },
            )
            self.assertFalse(module.boss_switch_enabled())
            self.assertTrue(module.name_normalization_enabled())

            joined = "\n".join(log.output)
            self.assertIn("name_normalization_enabled", joined)

    # ------------------------------------------------------------------
    # Fall 4: ungültiger Typ (String statt Bool) -> Default + Warning
    # ------------------------------------------------------------------
    def test_invalid_type_falls_back_to_default(self) -> None:
        fake = _make_fake_namenconfig(
            boss_switch_enabled="yes",  # String, kein Bool
            name_normalization_enabled=False,
        )

        with patch.dict(sys.modules, {"namenconfig": fake}):
            with self.assertLogs(self.LOGGER_NAME, level="WARNING") as log:
                module = self._reload_feature_config()

            self.assertEqual(
                module.NAMENCONFIG,
                {
                    "boss_switch_enabled": True,  # Default, weil String verworfen
                    "name_normalization_enabled": False,  # gültig, wird übernommen
                },
            )
            self.assertTrue(module.boss_switch_enabled())
            self.assertFalse(module.name_normalization_enabled())

            joined = "\n".join(log.output)
            self.assertIn("boss_switch_enabled", joined)
            self.assertIn("yes", joined)

    # ------------------------------------------------------------------
    # Fall 5: Helper-Funktionen liefern Werte aus NAMENCONFIG
    # ------------------------------------------------------------------
    def test_helper_functions_return_namenconfig_values(self) -> None:
        fake = _make_fake_namenconfig(
            boss_switch_enabled=True,
            name_normalization_enabled=False,
        )
        with patch.dict(sys.modules, {"namenconfig": fake}):
            module = self._reload_feature_config()

            self.assertEqual(
                module.boss_switch_enabled(),
                module.NAMENCONFIG["boss_switch_enabled"],
            )
            self.assertEqual(
                module.name_normalization_enabled(),
                module.NAMENCONFIG["name_normalization_enabled"],
            )
            self.assertTrue(module.boss_switch_enabled())
            self.assertFalse(module.name_normalization_enabled())


if __name__ == "__main__":
    unittest.main()
