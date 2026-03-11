import asyncio
import random
import unittest

from botcore.bootstrap import BOT_START_TIME, build_bot_intents
from services.battle import calculate_damage
from db import close_db, init_db


class SmokeTests(unittest.TestCase):
    def test_build_bot_intents_enable_required_flags(self) -> None:
        intents = build_bot_intents()
        self.assertTrue(bool(intents.message_content))
        self.assertTrue(bool(intents.members))
        self.assertTrue(bool(intents.presences))

    def test_bot_start_time_initialized(self) -> None:
        self.assertGreater(BOT_START_TIME, 0)

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


if __name__ == "__main__":
    unittest.main()
