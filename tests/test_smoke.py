import asyncio
import random
import time
import unittest

import bot
from botcore.bootstrap import BOT_START_TIME, build_bot_intents
from db import close_db, init_db
from services.battle import calculate_damage
from services.guild_settings import get_message_visibility, set_message_visibility
from services.user_data import add_infinitydust, delete_user_data, get_infinitydust


class SmokeTests(unittest.TestCase):
    def test_build_bot_intents_enable_required_flags(self) -> None:
        intents = build_bot_intents()
        self.assertTrue(bool(intents.message_content))
        self.assertTrue(bool(intents.members))
        self.assertTrue(bool(intents.presences))

    def test_bot_start_time_initialized(self) -> None:
        self.assertGreater(BOT_START_TIME, 0)

    def test_command_registration_stays_complete_after_split(self) -> None:
        def flatten(commands, prefix="") -> set[str]:
            names: set[str] = set()
            for command in commands:
                if getattr(command, "commands", None):
                    names.update(flatten(command.commands, prefix=f"{prefix}{command.name} "))
                else:
                    names.add(f"{prefix}{command.name}")
            return names

        command_names = flatten(bot.bot.tree.get_commands())
        expected = {
            "anfang",
            "bot-status",
            "eingeladen",
            "entwicklerpanel",
            "geschichte",
            "intro-zuruecksetzen",
            "kampf",
            "kanal-freigeben",
            "karte-geben",
            "konfigurieren entfernen",
            "konfigurieren hinzufuegen",
            "konfigurieren liste",
            "mission",
            "op-verwaltung",
            "sammlung",
            "sammlung-ansehen",
            "statistik balance",
            "täglich",
            "test-bericht",
            "verbessern",
        }
        self.assertTrue(expected.issubset(command_names))

    def test_calculate_damage_bounds(self) -> None:
        random.seed(42)
        damage, _is_crit, min_damage, max_damage = calculate_damage([10, 20], buff_amount=5)
        self.assertGreaterEqual(min_damage, 15)
        self.assertGreaterEqual(max_damage, min_damage)
        self.assertGreaterEqual(damage, min_damage)
        self.assertLessEqual(damage, max_damage)

    def test_db_init(self) -> None:
        asyncio.run(init_db())
        asyncio.run(close_db())

    def test_user_data_services_roundtrip(self) -> None:
        asyncio.run(init_db())
        user_id = 9876543210123
        try:
            asyncio.run(add_infinitydust(user_id, 3))
            self.assertGreaterEqual(asyncio.run(get_infinitydust(user_id)), 3)
        finally:
            asyncio.run(delete_user_data(user_id))
            asyncio.run(close_db())

    def test_visibility_services_roundtrip(self) -> None:
        asyncio.run(init_db())
        guild_id = time.time_ns()
        message_key = f"cmd:test.visibility.{guild_id}"

        self.assertEqual(
            asyncio.run(
                get_message_visibility(
                    guild_id,
                    message_key,
                    default_visibility="private",
                    legacy_visibility_keys={},
                )
            ),
            "private",
        )

        asyncio.run(set_message_visibility(guild_id, message_key, "public"))

        self.assertEqual(
            asyncio.run(
                get_message_visibility(
                    guild_id,
                    message_key,
                    default_visibility="private",
                    legacy_visibility_keys={},
                )
            ),
            "public",
        )
        asyncio.run(close_db())


if __name__ == "__main__":
    unittest.main()
