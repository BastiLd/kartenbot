"""Charakterisierungs-Tests für MissionBattleView (PvE) – Sicherheitsnetz für Audit D4.

Treibt einen vollständigen Missions-Zug (Spieler + Bot-Konter) durch `execute_attack`
und fixiert das beobachtbare Verhalten. Muss über die BaseBattleView-Vereinheitlichung
grün bleiben.
"""

import random
import unittest

from tests.view_harness import make_interaction, make_mission_view, run_view_coro


class MissionViewTurnTests(unittest.TestCase):
    def test_player_turn_damages_bot_and_returns_to_player(self) -> None:
        view = make_mission_view(user_id=111)
        it = make_interaction(111)
        before_bot = view.bot_hp

        random.seed(42)
        run_view_coro(lambda: view.execute_attack(it, 0))

        self.assertLess(view.bot_hp, before_bot, "Bot-HP sollte durch Spielerangriff sinken")
        self.assertEqual(view._mission_actor_turn, "player", "Nach Bot-Konter ist wieder der Spieler dran")
        self.assertEqual(view.current_turn, 111, "current_turn bleibt in Missionen der Spieler")
        self.assertGreaterEqual(view.bot_hp, 0)

    def test_wrong_user_is_rejected(self) -> None:
        view = make_mission_view(user_id=111)
        it = make_interaction(999)  # fremder Nutzer
        before_bot = view.bot_hp

        random.seed(3)
        run_view_coro(lambda: view.execute_attack(it, 0))

        self.assertEqual(view.bot_hp, before_bot, "Fremder Nutzer darf keinen Schaden auslösen")


class MissionViewSessionTests(unittest.TestCase):
    def test_serialize_restore_roundtrip(self) -> None:
        view = make_mission_view(user_id=111)
        it = make_interaction(111)
        random.seed(99)
        run_view_coro(lambda: view.execute_attack(it, 0))

        payload = view.serialize_session_payload()

        restored = make_mission_view(user_id=111)
        restored.restore_from_session_payload(payload)

        self.assertEqual(restored._hp_by_player, view._hp_by_player)
        self.assertEqual(restored.player_hp, view.player_hp)
        self.assertEqual(restored.bot_hp, view.bot_hp)


if __name__ == "__main__":
    unittest.main()
