"""DB-Roundtrip-Tests für die PvP-Kampf-AFK-Verdrahtung (Req. 13.6).

Prüft, dass ``create_battle_state`` einen Timer anlegt und ``touch_battle_turn``
ihn pro Spielerzug korrekt weiterschaltet (Runde hoch, Marker zurück, aktiver
Spieler wechselt) – inkl. der Eskalation ab Runde 3.
"""

import asyncio
import unittest

from db import close_db, init_db
from services import afk_tracker

BATTLE_ID = "battle:test-afk-wiring-9988776655"


class BattleAfkWiringTests(unittest.TestCase):
    def test_create_touch_delete_roundtrip(self) -> None:
        async def _run() -> None:
            await init_db()
            try:
                await afk_tracker.delete_state(BATTLE_ID)  # sauberer Start

                await afk_tracker.create_battle_state(
                    battle_id=BATTLE_ID,
                    thread_id=4242,
                    challenger_id=1,
                    acceptor_id=2,
                    active_player_id=1,
                )
                st = await afk_tracker.load_state(BATTLE_ID)
                self.assertIsNotNone(st)
                self.assertEqual(st.kind, "battle")
                self.assertEqual(st.round_number, 1)
                self.assertEqual(st.active_player_id, 1)

                # Herausforderer (1) handelt -> Runde 2, Akzeptor (2) aktiv.
                await afk_tracker.touch_battle_turn(BATTLE_ID, actor_id=1)
                st = await afk_tracker.load_state(BATTLE_ID)
                self.assertEqual(st.round_number, 2)
                self.assertEqual(st.active_player_id, 2)

                # Akzeptor (2) handelt -> Runde 3, Herausforderer (1) aktiv.
                await afk_tracker.touch_battle_turn(BATTLE_ID, actor_id=2)
                st = await afk_tracker.load_state(BATTLE_ID)
                self.assertEqual(st.round_number, 3)
                self.assertEqual(st.active_player_id, 1)

                # Ab Runde 3 gelten die eskalierten Schwellen (4 Pings statt 1).
                pings = afk_tracker.evaluate_pings(st, now=st.round_started_at + 7 * afk_tracker.HOUR)
                self.assertEqual(len(pings), 4)

                # Mask wird bei jedem Zug zurückgesetzt -> direkt nach touch keine offenen Bits.
                self.assertEqual(st.pings_sent_mask, 0)

                await afk_tracker.delete_state(BATTLE_ID)
                self.assertIsNone(await afk_tracker.load_state(BATTLE_ID))

                # touch auf gelöschten/fehlenden Timer ist ein No-Op (z. B. Bot-Kampf).
                self.assertIsNone(await afk_tracker.touch_battle_turn(BATTLE_ID, actor_id=1))
            finally:
                await afk_tracker.delete_state(BATTLE_ID)
                await close_db()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
