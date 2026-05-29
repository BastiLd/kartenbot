"""Invarianten-Tests für den AFK-Tracker (Req. 13, Properties 1-4).

``hypothesis`` ist in dieser Umgebung nicht installiert; die Properties werden
- wie ``test_verbessern_invariants.py`` - systematisch über ``itertools.product``
von relevanten Eckwerten abgedeckt.
"""

import itertools
import unittest

from services.afk_tracker import (
    HOUR,
    AfkState,
    evaluate_pings,
    on_action,
    pending_pings,
    state_from_row,
    state_to_row,
)


def _battle_state(round_number=3, started_at=0, mask=0, active=2):
    return AfkState(
        kind="battle",
        battle_id="t1",
        thread_id=999,
        challenger_id=1,
        acceptor_id=2,
        active_player_id=active,
        round_number=round_number,
        round_started_at=started_at,
        last_action_at=started_at,
        pings_sent_mask=mask,
    )


def _challenge_state(last_action_at=0, mask=0):
    return AfkState(
        kind="challenge",
        battle_id="c1",
        thread_id=999,
        challenger_id=1,
        acceptor_id=2,
        active_player_id=None,
        round_number=0,
        round_started_at=last_action_at,
        last_action_at=last_action_at,
        pings_sent_mask=mask,
    )


class ThresholdTests(unittest.TestCase):
    def test_challenge_4h_pings_acceptor_once(self):
        st = _challenge_state(last_action_at=0)
        # Schwelle ist inklusive (>=): genau bei 4h wird gepingt.
        pings = evaluate_pings(st, now=4 * HOUR)
        self.assertEqual(len(pings), 1)
        self.assertEqual(pings[0].recipients, (2,))

    def test_challenge_before_4h_no_ping(self):
        st = _challenge_state(last_action_at=0)
        self.assertEqual(evaluate_pings(st, now=3 * HOUR), [])

    def test_round1_only_4h_active(self):
        st = _battle_state(round_number=1, started_at=0, active=2)
        self.assertEqual(evaluate_pings(st, now=2 * HOUR + 1), [])
        pings = evaluate_pings(st, now=5 * HOUR)
        self.assertEqual(len(pings), 1)
        self.assertEqual(pings[0].recipients, (2,))

    def test_round3_full_cycle_caps_at_4(self):
        st = _battle_state(round_number=3, started_at=0)
        pings = evaluate_pings(st, now=7 * HOUR)
        self.assertEqual(len(pings), 4)
        scopes = [p.scope for p in pings]
        self.assertEqual(scopes, ["active", "both", "active", "both"])


class IdempotencyTests(unittest.TestCase):
    def test_evaluate_is_pure(self):
        # Property 1: gleiche Eingabe -> gleiche Ausgabe (mehrere Aufrufe).
        for rnd, hours in itertools.product((0, 1, 2, 3, 5), range(0, 8)):
            st = (_challenge_state(0) if rnd == 0 else _battle_state(round_number=rnd, started_at=0))
            now = hours * HOUR
            first = evaluate_pings(st, now)
            second = evaluate_pings(st, now)
            self.assertEqual(first, second)

    def test_pending_excludes_already_sent_bits(self):
        # Property 2: Ping-Cap pro Runde - bereits gesetzte Bits liefern keine Pings mehr.
        st = _battle_state(round_number=3, started_at=0, mask=0)
        # alle 4 Bits über die Zeit setzen
        st.pings_sent_mask = 0b1111
        self.assertEqual(pending_pings(st, now=10 * HOUR), [])


class ResetTests(unittest.TestCase):
    def test_on_action_resets_mask_and_round(self):
        # Property 3: nach on_action ist mask == 0 und round_started_at == now.
        st = _battle_state(round_number=3, started_at=0, mask=0b1111, active=2)
        on_action(st, actor_id=2, now=12345)
        self.assertEqual(st.pings_sent_mask, 0)
        self.assertEqual(st.round_started_at, 12345)
        self.assertEqual(st.round_number, 4)
        # nach Zug von Spieler 2 ist Spieler 1 aktiv
        self.assertEqual(st.active_player_id, 1)


class RestartEquivalenceTests(unittest.TestCase):
    def test_serialize_deserialize_same_pings(self):
        # Property 4: serialize -> deserialize liefert dieselbe Ping-Menge.
        for rnd in (0, 1, 2, 3, 5):
            st = (_challenge_state(100) if rnd == 0 else _battle_state(round_number=rnd, started_at=100))
            row = state_to_row(st, created_at=1)
            restored = state_from_row(row)
            now = 100 + 7 * HOUR
            self.assertEqual(evaluate_pings(st, now), evaluate_pings(restored, now))


if __name__ == "__main__":
    unittest.main()
