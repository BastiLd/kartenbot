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


class AfkDeadChannelCleanupTests(unittest.TestCase):
    """Ein Timer auf einen gelöschten Kanal/Thread (404) muss beim Tick entfernt
    werden, statt bei jedem Durchlauf einen Fehler zu werfen."""

    def test_tick_deletes_timer_when_channel_gone(self) -> None:
        import discord
        from types import SimpleNamespace

        not_found = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Channel")
        battle_id = "battle:test-afk-deadchannel-123"

        class _GoneBot:
            def get_channel(self, _cid):
                return None

            async def fetch_channel(self, _cid):
                raise not_found

        async def _run() -> None:
            await init_db()
            try:
                await afk_tracker.delete_state(battle_id)
                st = await afk_tracker.create_battle_state(
                    battle_id=battle_id,
                    thread_id=999111,
                    challenger_id=1,
                    acceptor_id=2,
                    active_player_id=1,
                )
                # Ping ist fällig (>= 4h Inaktivität in Runde 1).
                due_now = st.round_started_at + 5 * afk_tracker.HOUR
                self.assertTrue(afk_tracker.evaluate_pings(st, now=due_now))

                await afk_tracker.tick(_GoneBot(), st, now=due_now)

                # Toter Kanal -> verwaister Timer wurde entfernt, nicht erneut persistiert.
                self.assertIsNone(await afk_tracker.load_state(battle_id))
            finally:
                await afk_tracker.delete_state(battle_id)
                await close_db()

        asyncio.run(_run())

    def test_send_ping_keeps_timer_on_transient_error(self) -> None:
        from types import SimpleNamespace

        class _FlakyBot:
            def get_channel(self, _cid):
                return None

            async def fetch_channel(self, _cid):
                raise RuntimeError("temporary network blip")

        state = afk_tracker.AfkState(
            kind="battle",
            battle_id="battle:test-afk-transient",
            thread_id=4242,
            challenger_id=1,
            acceptor_id=2,
            active_player_id=1,
            round_number=1,
            round_started_at=0,
            last_action_at=0,
            pings_sent_mask=0,
        )
        ping = SimpleNamespace(bit=0, recipients=[1])

        async def _run() -> None:
            # Vorübergehender Fehler signalisiert NICHT "Kanal weg" (kein Löschen).
            self.assertFalse(await afk_tracker._send_ping(_FlakyBot(), state, ping))

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
