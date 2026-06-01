"""Unit-Tests für die aus bot.py ausgelagerten Effekt-Helfer (Audit D5).

Sichert das Verhalten des neuen, puren Moduls services/effect_handler.py ab,
damit weitere Auslagerungen ein Sicherheitsnetz haben.
"""

import unittest

from services import effect_handler as eh


class DotHelperTests(unittest.TestCase):
    def test_resolve_dot_damage_caps_at_configured_max(self) -> None:
        # bleeding ist auf max_damage 60 gedeckelt (karten.py DOT_TYPE_DEFAULTS).
        self.assertEqual(eh._resolve_dot_damage("bleeding", 200), 60)
        self.assertEqual(eh._resolve_dot_damage("bleeding", 25), 25)
        self.assertEqual(eh._resolve_dot_damage("kein_dot", 25), 25)

    def test_append_and_tick_dot(self) -> None:
        active: dict[int, list[dict[str, object]]] = {2: []}
        duration, dmg = eh._append_dot_effect(
            active, target_id=2, attacker_id=1, effect_type="poison", duration=[2, 2], damage=6
        )
        self.assertEqual((duration, dmg), (2, 6))

        taken: list[int] = []
        total, events = eh._apply_dot_ticks_for_applier(
            active, target_id=2, applier_id=1, damage_callback=taken.append
        )
        self.assertEqual(total, 6)
        self.assertEqual(taken, [6])
        self.assertEqual(len(events), 1)
        # Dauer wurde von 2 auf 1 reduziert, Effekt bleibt aktiv.
        self.assertEqual(active[2][0]["duration"], 1)

        # Zweiter Tick reduziert auf 0 -> Effekt wird entfernt.
        eh._apply_dot_ticks_for_applier(active, target_id=2, applier_id=1, damage_callback=taken.append)
        self.assertEqual(active[2], [])

    def test_dot_ticks_ignore_other_applier(self) -> None:
        active: dict[int, list[dict[str, object]]] = {2: []}
        eh._append_dot_effect(active, target_id=2, attacker_id=99, effect_type="burning", duration=3, damage=5)
        total, events = eh._apply_dot_ticks_for_applier(
            active, target_id=2, applier_id=1, damage_callback=lambda _d: None
        )
        self.assertEqual((total, events), (0, []))


class ActiveEffectHelperTests(unittest.TestCase):
    def test_append_find_remove(self) -> None:
        active: dict[int, list[dict[str, object]]] = {}
        entry = eh._append_active_effect(active, 5, "Stun", 1, turns=2)
        # Typ wird normalisiert (lowercase/strip).
        self.assertEqual(entry["type"], "stun")
        self.assertIs(eh._find_active_effect(active, 5, "stun"), entry)
        self.assertEqual(len(eh._active_effect_entries(active, 5, "STUN ")), 1)

        eh._remove_active_effect(active, 5, entry)
        self.assertIsNone(eh._find_active_effect(active, 5, "stun"))
        # Doppeltes Entfernen ist ein No-Op.
        eh._remove_active_effect(active, 5, entry)

    def test_label_and_dot_type_helpers(self) -> None:
        self.assertEqual(eh._label_key("  HeLLo "), "hello")
        self.assertTrue(eh._is_dot_effect_type("Poison"))
        self.assertFalse(eh._is_dot_effect_type("stun"))
        self.assertEqual(eh._effect_amount_label([3, 7]), "3-7")
        self.assertEqual(eh._effect_amount_label(4), "4")


if __name__ == "__main__":
    unittest.main()
