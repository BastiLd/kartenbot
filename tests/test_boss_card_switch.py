"""Tests für den Boss-Karten-Wechsel (Req. 1): Toggle-Gate + (aktuell)-Marker."""

import unittest
from unittest import mock

import bot as bot_module
from karten import karten


def _two_card_names():
    names = [str(c.get("name")) for c in karten if c.get("name")]
    return names[:2]


class BossSwitchToggleTests(unittest.TestCase):
    def _boss_preview(self):
        mission_state = {"mission_data": {}, "next_wave": 4, "total_waves": 4, "preview_index": 0}
        return bot_module.MissionEncounterPreviewView(123, mission_state, "boss")

    def test_toggle_on_shows_hero_button(self):
        with mock.patch.object(bot_module, "boss_switch_enabled", return_value=True):
            view = self._boss_preview()
        ids = {getattr(item, "custom_id", None) for item in view.children}
        self.assertIn("mission_enc_prv:hero", ids)

    def test_toggle_off_hides_hero_button(self):
        with mock.patch.object(bot_module, "boss_switch_enabled", return_value=False):
            view = self._boss_preview()
        ids = {getattr(item, "custom_id", None) for item in view.children}
        self.assertNotIn("mission_enc_prv:hero", ids)
        # Boss-Start bleibt verfügbar (direkter Kampf ohne Menü).
        self.assertIn("mission_enc_prv:start_b", ids)


class BossSwitchMarkerTests(unittest.TestCase):
    def test_current_card_marked_aktuell_and_first(self):
        names = _two_card_names()
        if len(names) < 2:
            self.skipTest("Nicht genug Karten für den Test")
        current, other = names[0], names[1]
        user_karten = [(current, 1), (other, 1)]
        mission_state = {"selected_card_name": current, "mission_data": {}}
        view = bot_module.MissionNewCardSelectView(7, user_karten, mission_state=mission_state)
        labels = [opt.label for opt in view.select.options]
        self.assertTrue(any("(aktuell)" in lbl for lbl in labels), labels)
        # Markierte Karte steht an erster Stelle.
        self.assertIn("(aktuell)", labels[0])


if __name__ == "__main__":
    unittest.main()
