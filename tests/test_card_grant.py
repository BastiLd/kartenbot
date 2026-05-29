"""Tests for ``services.card_grant.grant_cards_to_users``.

This is the gemeinsamer Service-Code-Pfad, der von ``/karte-geben`` (Multi)
und vom Dev-Panel-Eintrag „Grant Card" identisch aufgerufen wird. Die Tests
hier decken die reine Service-Logik ab — ohne Discord, ohne Datenbank, ohne
globale Imports — indem alle Abhängigkeiten als Test-Doppel hereingereicht
werden.

Validates: Requirements 11.3, 11.4
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from services.card_grant import (
    GrantOutcome,
    GrantSummary,
    grant_cards_to_users,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _always_known(_name: str) -> bool:
    """Default ``is_card_known`` stub: every card is in the catalog."""
    return True


def _never_known(_name: str) -> bool:
    """``is_card_known`` stub: no card is in the catalog."""
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class GrantCardsToUsersTests(unittest.IsolatedAsyncioTestCase):
    """Verifies the contract of :func:`grant_cards_to_users`.

    Validates: Requirements 11.3, 11.4
    """

    async def test_all_grants_succeed(self) -> None:
        """Every (user, card) pair lands in ``added`` when ``add_card`` returns True.

        Validates: Requirements 11.3
        """
        users = [101, 202]
        cards = ["Iron-Man", "Hulk", "Thor"]

        async def add_card(_uid: int, _name: str) -> bool:
            return True

        summary = await grant_cards_to_users(
            target_user_ids=users,
            card_names=cards,
            add_card=add_card,
            is_card_known=_always_known,
        )

        self.assertEqual(summary.total_added, len(users) * len(cards))
        self.assertEqual(summary.total_skipped, 0)
        self.assertEqual(summary.total_failed, 0)
        self.assertEqual(len(summary.outcomes), len(users) * len(cards))
        for outcome in summary.outcomes:
            self.assertEqual(outcome.bucket, "added")

    async def test_already_owned_card_lands_in_skipped(self) -> None:
        """``add_card`` returns False for a known card → bucket = ``skipped``.

        The other pairs must still complete and land in ``added``.

        Validates: Requirements 11.3
        """
        users = [101, 202]
        cards = ["Iron-Man", "Hulk"]
        skipped_pair = (202, "Hulk")

        async def add_card(uid: int, name: str) -> bool:
            return (uid, name) != skipped_pair

        summary = await grant_cards_to_users(
            target_user_ids=users,
            card_names=cards,
            add_card=add_card,
            is_card_known=_always_known,
        )

        self.assertEqual(summary.total_added, 3)
        self.assertEqual(summary.total_skipped, 1)
        self.assertEqual(summary.total_failed, 0)

        # The skipped pair lands in the right bucket and only there.
        self.assertEqual(summary.per_user_skipped(202), ["Hulk"])
        self.assertEqual(summary.per_user_added(202), ["Iron-Man"])
        self.assertEqual(summary.per_user_added(101), ["Iron-Man", "Hulk"])

    async def test_unknown_card_lands_in_failed(self) -> None:
        """``add_card`` returns False AND ``is_card_known`` returns False → ``failed``.

        Validates: Requirements 11.3
        """
        users = [101]
        cards = ["GhostCard"]

        async def add_card(_uid: int, _name: str) -> bool:
            return False

        summary = await grant_cards_to_users(
            target_user_ids=users,
            card_names=cards,
            add_card=add_card,
            is_card_known=_never_known,
        )

        self.assertEqual(summary.total_added, 0)
        self.assertEqual(summary.total_skipped, 0)
        self.assertEqual(summary.total_failed, 1)
        self.assertEqual(summary.outcomes[0].bucket, "failed")
        self.assertEqual(summary.per_user_failed(101), ["GhostCard"])

    async def test_exception_during_add_card_lands_in_failed(self) -> None:
        """An exception from ``add_card`` is captured → ``failed``; loop continues.

        Validates: Requirements 11.3
        """
        users = [101, 202]
        cards = ["Iron-Man", "Hulk"]
        failing_pair = (202, "Hulk")

        async def add_card(uid: int, name: str) -> bool:
            if (uid, name) == failing_pair:
                raise RuntimeError("simulated DB error")
            return True

        summary = await grant_cards_to_users(
            target_user_ids=users,
            card_names=cards,
            add_card=add_card,
            is_card_known=_always_known,
        )

        # Every pair was attempted — no early exit on the exception.
        self.assertEqual(len(summary.outcomes), len(users) * len(cards))
        self.assertEqual(summary.total_failed, 1)
        self.assertEqual(summary.total_added, 3)
        self.assertEqual(summary.total_skipped, 0)
        self.assertEqual(summary.per_user_failed(202), ["Hulk"])

    async def test_on_outcome_callback_fires_per_pair(self) -> None:
        """``on_outcome`` is awaited exactly once per (user, card) with the bucket.

        Validates: Requirements 11.3
        """
        users = [101, 202]
        cards = ["Iron-Man", "Hulk"]

        async def add_card(_uid: int, _name: str) -> bool:
            return True

        on_outcome = AsyncMock()

        await grant_cards_to_users(
            target_user_ids=users,
            card_names=cards,
            add_card=add_card,
            is_card_known=_always_known,
            on_outcome=on_outcome,
        )

        self.assertEqual(on_outcome.await_count, len(users) * len(cards))
        # Every call must have signature (user_id, card_name, bucket="added").
        observed_pairs: set[tuple[int, str, str]] = set()
        for call in on_outcome.await_args_list:
            uid, card_name, bucket = call.args
            observed_pairs.add((uid, card_name, bucket))
        expected_pairs = {(uid, c, "added") for uid in users for c in cards}
        self.assertEqual(observed_pairs, expected_pairs)

    async def test_per_user_helpers_filter_correctly(self) -> None:
        """``per_user_added`` returns only that user's added cards in original order.

        2 users × 2 cards; mix of buckets so each helper has something to filter.

        Validates: Requirements 11.3
        """
        # Construct a deterministic outcome distribution by name:
        #   (101, "A") → added
        #   (101, "B") → skipped
        #   (202, "A") → failed (unknown)
        #   (202, "B") → added
        users = [101, 202]
        cards = ["A", "B"]

        async def add_card(uid: int, name: str) -> bool:
            if uid == 101 and name == "A":
                return True
            if uid == 101 and name == "B":
                return False  # known → skipped
            if uid == 202 and name == "A":
                return False  # not known → failed
            return True  # (202, "B") → added

        def is_card_known(name: str) -> bool:
            # "A" is known, but for user 202 add_card still returns False
            # → goes to skipped, not failed. To force a failed outcome we make
            # the catalog say "A" is unknown and use a per-user toggle below.
            return name == "B"

        summary = await grant_cards_to_users(
            target_user_ids=users,
            card_names=cards,
            add_card=add_card,
            is_card_known=is_card_known,
        )

        # Sanity: 4 outcomes total.
        self.assertEqual(len(summary.outcomes), 4)

        # per_user_added returns only that user's added cards in original
        # card order.
        self.assertEqual(summary.per_user_added(101), ["A"])
        self.assertEqual(summary.per_user_added(202), ["B"])
        # per_user_skipped: user 101's "B" returned False with known catalog.
        self.assertEqual(summary.per_user_skipped(101), ["B"])
        self.assertEqual(summary.per_user_skipped(202), [])
        # per_user_failed: user 202's "A" returned False AND was unknown.
        self.assertEqual(summary.per_user_failed(101), [])
        self.assertEqual(summary.per_user_failed(202), ["A"])

    async def test_iteration_order_outer_cards_inner_users(self) -> None:
        """Loop order: outer = cards, inner = users.

        Matches the existing call sites in ``bot.py`` (Dev-Panel) and in
        ``botcommands/admin_commands.py`` (``/karte-geben``).

        Validates: Requirements 11.4
        """
        users = [1, 2, 3]
        cards = ["A", "B"]
        observed: list[tuple[int, str]] = []

        async def add_card(uid: int, name: str) -> bool:
            observed.append((uid, name))
            return True

        summary = await grant_cards_to_users(
            target_user_ids=users,
            card_names=cards,
            add_card=add_card,
            is_card_known=_always_known,
        )

        # The ``add_card`` callable observes outer = cards, inner = users.
        expected_call_order = [
            (1, "A"), (2, "A"), (3, "A"),
            (1, "B"), (2, "B"), (3, "B"),
        ]
        self.assertEqual(observed, expected_call_order)

        # The summary preserves the same iteration order.
        outcome_pairs = [
            (outcome.user_id, outcome.card_name) for outcome in summary.outcomes
        ]
        self.assertEqual(outcome_pairs, expected_call_order)


# ---------------------------------------------------------------------------
# Light dataclass smoke tests (covering trivial helper paths)
# ---------------------------------------------------------------------------


class GrantSummaryHelperTests(unittest.TestCase):
    """Minimal checks on ``GrantSummary`` helpers that the async tests don't hit."""

    def test_empty_summary_helpers_return_empty_lists(self) -> None:
        summary = GrantSummary()
        self.assertEqual(summary.per_user_added(123), [])
        self.assertEqual(summary.per_user_skipped(123), [])
        self.assertEqual(summary.per_user_failed(123), [])

    def test_helpers_filter_by_user_id(self) -> None:
        summary = GrantSummary(
            outcomes=[
                GrantOutcome(user_id=1, card_name="A", bucket="added"),
                GrantOutcome(user_id=2, card_name="A", bucket="added"),
                GrantOutcome(user_id=1, card_name="B", bucket="skipped"),
            ]
        )
        self.assertEqual(summary.per_user_added(1), ["A"])
        self.assertEqual(summary.per_user_added(2), ["A"])
        self.assertEqual(summary.per_user_skipped(1), ["B"])
        self.assertEqual(summary.per_user_skipped(2), [])


if __name__ == "__main__":
    unittest.main()
