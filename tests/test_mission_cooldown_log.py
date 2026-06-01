"""Regressionstests für v2.3.12 (Mission-Bugs).

1. Bot-/Boss-Cooldowns müssen jede Bot-Runde sinken – auch wenn der Bot nur seine
   Standardattacke einsetzt (vorher: Specials froren auf z. B. 2/2/5 ein, Maestro
   konnte nur noch Standard nutzen).
2. Der Missions-Kampflog muss zwischengespeichert werden, damit "Kampf-Log per DM"
   nach Missions-Ende funktioniert (vorher: "Für diesen Kampf ist kein Log verfügbar").
"""

import unittest
from unittest.mock import AsyncMock

import bot as bot_module
from bot import MissionBattleView


class _DummyResponse:
    def __init__(self) -> None:
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *args, **kwargs) -> None:
        self._done = True

    async def send_message(self, *args, **kwargs) -> None:
        self._done = True


class _DummyFollowup:
    async def send(self, *args, **kwargs):
        return None

    async def edit_message(self, *args, **kwargs):
        return None


class _DummyChannel:
    id = 555

    async def send(self, *args, **kwargs):
        return None


class _DummyUser:
    def __init__(self, uid: int) -> None:
        self.id = uid
        self.display_name = f"User{uid}"
        self.mention = f"<@{uid}>"


class _DummyInteraction:
    def __init__(self, uid: int) -> None:
        self.user = _DummyUser(uid)
        self.guild = None
        self.guild_id = 1
        self.channel = _DummyChannel()
        self.channel_id = 555
        self.message = None  # -> _interaction_message_or_none liefert None (kein Sleep/Persist)
        self.response = _DummyResponse()
        self.followup = _DummyFollowup()


class MissionBotCooldownTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_cooldowns_tick_down_on_standard_attack_turn(self) -> None:
        player_card = {
            "name": "Held",
            "hp": 300,
            "attacks": [{"name": "Tipper", "damage": [1, 1], "is_standard_attack": True}],
        }
        bot_card = {
            "name": "Maestro",
            "hp": 300,
            "attacks": [
                {"name": "Schlag", "damage": [1, 1], "is_standard_attack": True},
                {"name": "Spezial", "damage": [10, 10], "cooldown_turns": 2},
            ],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        # Heavy I/O des Bot-Zugs neutralisieren (DB/Logging) – irrelevant für Cooldowns.
        view._log_mission_attack_event = AsyncMock()
        view.persist_session = AsyncMock()
        original_get_card_buffs = bot_module.get_card_buffs

        async def _no_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _no_buffs
        try:
            # Spezial-Attacke (Index 1) steht auf Cooldown -> Bot MUSS Standard (Index 0) nutzen.
            view.bot_attack_cooldowns[1] = 2
            interaction = _DummyInteraction(1)
            await view.execute_attack(interaction, 0)
            # Nach genau einem Bot-Zug muss der Cooldown um 1 gesunken sein (2 -> 1),
            # obwohl der Bot nur die Standardattacke benutzt hat.
            self.assertEqual(view.bot_attack_cooldowns.get(1, 0), 1)
        finally:
            bot_module.get_card_buffs = original_get_card_buffs
            view.stop()

    async def test_bot_special_recovers_after_enough_turns(self) -> None:
        player_card = {
            "name": "Held",
            "hp": 500,
            "attacks": [{"name": "Tipper", "damage": [1, 1], "is_standard_attack": True}],
        }
        bot_card = {
            "name": "Maestro",
            "hp": 500,
            "attacks": [
                {"name": "Schlag", "damage": [1, 1], "is_standard_attack": True},
                {"name": "Spezial", "damage": [10, 10], "cooldown_turns": 2},
            ],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view._log_mission_attack_event = AsyncMock()
        view.persist_session = AsyncMock()
        original_get_card_buffs = bot_module.get_card_buffs

        async def _no_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _no_buffs
        try:
            view.bot_attack_cooldowns[1] = 2
            # Zwei Spieler-Züge -> zwei Bot-Züge -> Cooldown 2 -> 0 (Eintrag entfernt).
            await view.execute_attack(_DummyInteraction(1), 0)
            await view.execute_attack(_DummyInteraction(1), 0)
            self.assertEqual(view.bot_attack_cooldowns.get(1, 0), 0)
            self.assertFalse(view.is_attack_on_cooldown_bot(1))
        finally:
            bot_module.get_card_buffs = original_get_card_buffs
            view.stop()


class MissionLogCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_edit_battle_log_caches_text_even_without_message(self) -> None:
        player_card = {"name": "Held", "hp": 100, "attacks": [{"name": "Hit", "damage": [1, 1], "is_standard_attack": True}]}
        bot_card = {"name": "Gegner", "hp": 100, "attacks": [{"name": "Hit", "damage": [1, 1], "is_standard_attack": True}]}
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        try:
            view.battle_log_message = None  # Nachricht gelöscht / nie erstellt
            log_text = "Runde 1: Held trifft Gegner für 5 Schaden."
            embed = bot_module.discord.Embed(description=log_text)
            await view._safe_edit_battle_log(embed)
            self.assertEqual(view._battle_log_text_cache, log_text)
            # durable_log_text() fällt auf den Cache zurück, wenn keine Nachricht existiert.
            self.assertEqual(view.durable_log_text(), log_text)
        finally:
            view.stop()


if __name__ == "__main__":
    unittest.main()
