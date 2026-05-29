"""Test für die Schadens-Anzeige im Kampf-Log (Benni-Bug).

Bei aktiver Verstärkung + Schutz-Reduktion muss die Aufschlüsselung den
AUSGANGSSCHADEN (Grundschaden + Verstärkung) zeigen, nicht den bereits durch
Schutz reduzierten Endwert.
"""

import unittest

from services.battle import _damage_breakdown_lines


class DamageBreakdownTests(unittest.TestCase):
    def test_boost_with_shield_reduction_shows_outgoing(self):
        # Grundschaden 30, +15 Verstärkung = 45; durch Schutz auf 23 reduziert.
        events = ["Normal: 30 | durch Verstärkung: 45 (+15)"]
        lines = _damage_breakdown_lines(actual_damage=23, pre_effect_damage=0, effect_events=events)
        self.assertIn("Schaden: 30", lines)
        self.assertIn("Verstärkung Schaden: 15", lines)
        self.assertIn("Zusammen Schaden: 45", lines)
        self.assertNotIn("Schaden: 23", lines)

    def test_boost_without_reduction(self):
        events = ["Normal: 30 | durch Verstärkung: 45 (+15)"]
        lines = _damage_breakdown_lines(actual_damage=45, pre_effect_damage=0, effect_events=events)
        self.assertIn("Schaden: 30", lines)
        self.assertIn("Zusammen Schaden: 45", lines)

    def test_plain_hit_no_boost(self):
        lines = _damage_breakdown_lines(actual_damage=17, pre_effect_damage=0, effect_events=[])
        self.assertEqual(lines, ["Schaden: 17"])


if __name__ == "__main__":
    unittest.main()
