"""Charakterisierungs-Tests für BattleView (PvP) – Sicherheitsnetz für Audit D4.

Treibt echte Züge durch `execute_attack` und fixiert das beobachtbare Verhalten
(Schaden, Zugwechsel, Cooldown, Log, Serialisierungs-Roundtrip). Diese Tests müssen
über die gesamte BaseBattleView-Vereinheitlichung grün bleiben.
"""

import random
import unittest

from tests.view_harness import make_battle_view, make_interaction, run_view_coro


class BattleViewTurnTests(unittest.TestCase):
    def test_standard_attack_damages_opponent_and_switches_turn(self) -> None:
        view = make_battle_view(p1_id=111, p2_id=222)
        it = make_interaction(111)
        before = view.player2_hp

        random.seed(42)
        run_view_coro(lambda: view.execute_attack(it, 0))  # Index 0 = Standardangriff

        self.assertLess(view.player2_hp, before, "Gegner-HP sollte sinken")
        self.assertEqual(view.current_turn, 222, "Zug sollte zum Gegner wechseln")
        self.assertGreaterEqual(len(view._all_battle_log_entries), 1, "Log-Eintrag sollte entstehen")
        self.assertGreaterEqual(view.player2_hp, 0)

    def test_special_attack_sets_cooldown(self) -> None:
        view = make_battle_view(p1_id=111, p2_id=222)
        it = make_interaction(111)

        random.seed(7)
        # Index 1 bei Black Widow = "Taser" (cooldown_turns: 3)
        run_view_coro(lambda: view.execute_attack(it, 1))

        self.assertTrue(view.is_attack_on_cooldown(111, 1), "Spezialangriff sollte auf Cooldown sein")

    def test_not_your_turn_is_rejected(self) -> None:
        view = make_battle_view(p1_id=111, p2_id=222)
        it = make_interaction(222)  # Spieler 2 ist nicht dran
        before = view.player2_hp

        random.seed(1)
        run_view_coro(lambda: view.execute_attack(it, 0))

        self.assertEqual(view.player2_hp, before, "Kein Schaden, wenn nicht am Zug")
        self.assertEqual(view.current_turn, 111, "Zug bleibt bei Spieler 1")


class BattleViewSessionTests(unittest.TestCase):
    def test_serialize_restore_roundtrip(self) -> None:
        view = make_battle_view(p1_id=111, p2_id=222)
        it = make_interaction(111)
        random.seed(99)
        run_view_coro(lambda: view.execute_attack(it, 1))

        payload = view.serialize_session_payload()

        restored = make_battle_view(p1_id=111, p2_id=222)
        restored.restore_from_session_payload(payload)

        self.assertEqual(restored._hp_by_player, view._hp_by_player)
        self.assertEqual(restored.current_turn, view.current_turn)
        self.assertEqual(restored.attack_cooldowns, view.attack_cooldowns)


if __name__ == "__main__":
    unittest.main()
