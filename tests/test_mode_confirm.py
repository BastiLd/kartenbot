"""Tests für den Mode-Confirmation-Dialog mit Statusanzeige (Req. 10)."""

import unittest

import game_ui_texts


class ModeConfirmTests(unittest.TestCase):
    def test_active_shows_deactivation(self):
        text = game_ui_texts.render_mode_confirm("Maintenance", is_active=True)
        self.assertIn("AKTIV", text)
        self.assertIn("DEAKTIVIERT", text)
        self.assertIn("Maintenance ist aktuell **AKTIV**", text)
        self.assertIn("wird **DEAKTIVIERT**", text)

    def test_inactive_shows_activation(self):
        text = game_ui_texts.render_mode_confirm("Beta", is_active=False)
        self.assertIn("NICHT AKTIV", text)
        self.assertIn("AKTIVIERT", text)
        self.assertIn("Beta ist aktuell **NICHT AKTIV**", text)
        self.assertIn("wird **AKTIVIERT**", text)

    def test_template_available(self):
        self.assertIn("{mode_name}", game_ui_texts.MODE_CONFIRM_TEMPLATE)
        self.assertIn("{current}", game_ui_texts.MODE_CONFIRM_TEMPLATE)
        self.assertIn("{transition}", game_ui_texts.MODE_CONFIRM_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
