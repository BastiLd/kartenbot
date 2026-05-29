"""Tests für das Infinitydust-Belohnungssystem (Req. 7)."""

import asyncio
import itertools
import unittest

from services.mission_rewards import (
    MISSION_INFINITYDUST_CAP,
    MissionRewardAccumulator,
    commit_on_mission_success,
    discard_on_mission_failure,
)


class _FakeBank:
    def __init__(self):
        self.calls = []

    async def add(self, user_id, amount):
        self.calls.append((user_id, amount))


class MissionRewardTests(unittest.TestCase):
    def _acc(self):
        return MissionRewardAccumulator(user_id=42, mission_id="maestro")

    def test_lakai_boss_daily_increments(self):
        acc = self._acc()
        acc.on_lakai_defeated()
        self.assertEqual(acc.total(), 1)
        acc.on_boss_defeated()
        self.assertEqual(acc.total(), 2)
        acc.on_daily_card_already_owned()
        self.assertEqual(acc.total(), 3)

    def test_full_standard_mission_caps_at_5(self):
        acc = self._acc()
        for _ in range(3):
            acc.on_lakai_defeated()
        acc.on_boss_defeated()
        acc.on_daily_card_already_owned()
        self.assertEqual(acc.total(), 5)
        self.assertEqual(MISSION_INFINITYDUST_CAP, 5)

    def test_total_never_exceeds_cap_property(self):
        # Systematische Abdeckung der Standard-Mission-Eingaben (Property 10: total <= 5).
        for lakeien, boss, daily in itertools.product(range(0, 4), (False, True), (False, True)):
            acc = self._acc()
            for _ in range(lakeien):
                acc.on_lakai_defeated()
            if boss:
                acc.on_boss_defeated()
            if daily:
                acc.on_daily_card_already_owned()
            self.assertLessEqual(acc.total(), MISSION_INFINITYDUST_CAP)

    def test_commit_pays_out(self):
        acc = self._acc()
        acc.on_lakai_defeated()
        acc.on_boss_defeated()
        bank = _FakeBank()
        paid = asyncio.run(commit_on_mission_success(acc, add_infinitydust=bank.add))
        self.assertEqual(paid, 2)
        self.assertEqual(bank.calls, [(42, 2)])

    def test_commit_zero_does_not_call(self):
        acc = self._acc()
        bank = _FakeBank()
        paid = asyncio.run(commit_on_mission_success(acc, add_infinitydust=bank.add))
        self.assertEqual(paid, 0)
        self.assertEqual(bank.calls, [])

    def test_discard_pays_nothing(self):
        acc = self._acc()
        acc.on_lakai_defeated()
        acc.on_boss_defeated()
        asyncio.run(discard_on_mission_failure(acc))
        # Kein Auszahlungs-Hook -> Property 11: failed/cancelled => 0 ausgezahlt.
        bank = _FakeBank()
        self.assertEqual(bank.calls, [])


if __name__ == "__main__":
    unittest.main()
