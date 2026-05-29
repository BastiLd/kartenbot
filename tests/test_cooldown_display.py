"""Tests für die Cooldown-Anzeige und Boss-Spezial-Hervorhebung (Req. 14, 15)."""

import logging
import unittest

from services.battle import _format_attack_label, render_boss_special_activation


class CooldownLabelTests(unittest.TestCase):
    def test_no_cooldown_no_suffix(self):
        atk = {"name": "Schlag", "cooldown_turns": 0}
        self.assertEqual(_format_attack_label(atk, is_on_cooldown=False), "Schlag")

    def test_missing_cooldown_no_suffix(self):
        self.assertEqual(_format_attack_label({"name": "Schlag"}, is_on_cooldown=False), "Schlag")

    def test_available_with_cooldown_shows_suffix(self):
        atk = {"name": "Gamma-Eruption", "cooldown_turns": 3}
        self.assertEqual(_format_attack_label(atk, is_on_cooldown=False), "Gamma-Eruption (3CD)")

    def test_on_cooldown_no_suffix(self):
        atk = {"name": "Gamma-Eruption", "cooldown_turns": 3}
        self.assertEqual(_format_attack_label(atk, is_on_cooldown=True), "Gamma-Eruption")


class BossSpecialRenderTests(unittest.TestCase):
    def test_render_format(self):
        out = render_boss_special_activation("Maestro", "Maestros Hohn",
                                             "der nächste Spielerangriff verursacht 0 Schaden")
        self.assertEqual(out, "⚡ **Maestros Hohn** — der nächste Spielerangriff verursacht 0 Schaden")

    def test_missing_ability_returns_none_and_warns(self):
        with self.assertLogs(level=logging.WARNING):
            self.assertIsNone(render_boss_special_activation("Maestro", "", "Effekt"))

    def test_missing_effect_returns_none_and_warns(self):
        with self.assertLogs(level=logging.WARNING):
            self.assertIsNone(render_boss_special_activation("Maestro", "Hohn", ""))


if __name__ == "__main__":
    unittest.main()
