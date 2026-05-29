"""Tests for the `/karte-geben` admin command flow (Single + Multi).

These tests verify Requirements 3.1, 3.2, 3.3, 3.4 and 3.5 from the
v2.3.0 spec:
- 3.1: Single mode delivers exactly one card to exactly one user.
- 3.2: Multi mode delivers multiple cards in one operation.
- 3.3: Multi mode produces a single summary message listing every card.
- 3.4: A partial failure (one card fails) does not abort the rest;
       the failed card is still surfaced in the summary.
- 3.5: Both Single and Multi paths are covered by tests.

The flow tested here lives in ``botcommands/admin_commands.py::karte_geben``
(the slash command callback exposed as ``bot.give``). Because the command
talks to many helpers via the ``AdminFacade`` (which delegates attribute
access to ``bot._command_api._items``), we patch the items dict in place
to inject mocks for the views, DB helpers and rendering helpers without
spinning up a Discord client.
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportAssignmentType=false

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot as bot_module


# ---------------------------------------------------------------------------
# Helpers — minimal Discord interaction stand-ins
# ---------------------------------------------------------------------------


class _FakeAdminUserSelectView:
    """Stand-in for ``bot.AdminUserSelectView`` that returns a pre-set value."""

    def __init__(self, value):
        self.value = value

    async def wait(self):
        return None

    def stop(self):
        pass


class _FakeMultiCardSelectView:
    """Stand-in for ``bot.MultiCardSelectView`` that returns a pre-set value."""

    def __init__(self, value):
        self.value = value
        self._message = None

    async def wait(self):
        return None

    def bind_message(self, message):
        self._message = message

    def content_text(self) -> str:
        return "Wähle Karten:"

    def _summary_embed(self):
        return None

    def stop(self):
        pass


def _build_interaction(*, admin_user_id: int = 99, guild_member_map=None):
    """Build a minimal interaction object compatible with the slash callback."""
    if guild_member_map is None:
        guild_member_map = {}

    response = SimpleNamespace(
        defer=AsyncMock(),
        send_message=AsyncMock(),
    )
    # ``followup.send`` returns a Message-like object so that ``bind_message``
    # in the production code receives a value.
    followup = SimpleNamespace(
        send=AsyncMock(return_value=SimpleNamespace(id=4242)),
    )
    user = SimpleNamespace(
        id=admin_user_id,
        mention=f"<@{admin_user_id}>",
        display_name="Admin",
    )
    guild = SimpleNamespace(
        id=12345,
        get_member=lambda uid: guild_member_map.get(uid),
        members=list(guild_member_map.values()),
    )

    return SimpleNamespace(
        user=user,
        guild=guild,
        guild_id=12345,
        channel_id=999,
        channel=SimpleNamespace(id=999),
        response=response,
        followup=followup,
        command=SimpleNamespace(qualified_name="karte-geben"),
    )


class KarteGebenSingleFlowTests(unittest.IsolatedAsyncioTestCase):
    """Verifies the Single-mode happy path and the no-selection abort path."""

    async def _run_single_flow(
        self,
        *,
        target_user_id,
        selected_cards,
        guild_member_map=None,
        add_returns: bool = True,
        get_karte_returns=None,
    ):
        """Drive ``bot.give.callback`` end-to-end with mocked dependencies."""
        if guild_member_map is None:
            guild_member_map = {
                42: SimpleNamespace(id=42, display_name="Tester", mention="<@42>")
            }

        interaction = _build_interaction(
            admin_user_id=99, guild_member_map=guild_member_map
        )

        send_with_visibility_mock = AsyncMock()
        add_exact_mock = AsyncMock(return_value=add_returns)
        get_karte_mock = AsyncMock(return_value=get_karte_returns)
        admin_view_factory = MagicMock(
            return_value=_FakeAdminUserSelectView(target_user_id)
        )
        multi_card_view_factory = MagicMock(
            return_value=_FakeMultiCardSelectView(selected_cards)
        )
        give_card_confirm_factory = MagicMock(
            side_effect=AssertionError(
                "GiveCardConfirmView must NOT be used in Single mode"
            )
        )

        items = bot_module._command_api._items
        with patch.dict(
            items,
            {
                "is_channel_allowed": AsyncMock(return_value=True),
                "is_admin": AsyncMock(return_value=True),
                "command_visibility_key_for_interaction": MagicMock(
                    return_value=None
                ),
                "AdminUserSelectView": admin_view_factory,
                "MultiCardSelectView": multi_card_view_factory,
                "GiveCardConfirmView": give_card_confirm_factory,
                "add_exact_card_variant_once": add_exact_mock,
                "get_karte_by_name": get_karte_mock,
                "_card_rarity_color": MagicMock(return_value=None),
                "_card_by_name_local": MagicMock(return_value=None),
                "_send_with_visibility": send_with_visibility_mock,
            },
            clear=False,
        ):
            await bot_module.give.callback(interaction, "single")

        return SimpleNamespace(
            interaction=interaction,
            send_with_visibility=send_with_visibility_mock,
            add_exact=add_exact_mock,
            admin_view_factory=admin_view_factory,
            multi_card_view_factory=multi_card_view_factory,
            give_card_confirm_factory=give_card_confirm_factory,
        )

    # -- Test 1: happy path ------------------------------------------------

    async def test_single_mode_selects_one_user_one_card(self) -> None:
        """Single mode → exactly one ``add_exact_card_variant_once`` call.

        Validates: Requirements 3.1, 3.5
        """
        result = await self._run_single_flow(
            target_user_id="42",
            selected_cards=["Iron-Man"],
        )

        # add_exact_card_variant_once must be called exactly once with
        # (target_user_id, card_name).
        self.assertEqual(result.add_exact.await_count, 1)
        result.add_exact.assert_awaited_with(42, "Iron-Man")

        # Single mode must NOT pass through the multi-mode confirmation view.
        result.give_card_confirm_factory.assert_not_called()

        # Exactly one final result message is sent through visibility helper.
        self.assertEqual(result.send_with_visibility.await_count, 1)

    # -- Test 2: confirmation contents -------------------------------------

    async def test_single_mode_confirmation_shows_user_and_card(self) -> None:
        """Final embed mentions the target user and contains the card name.

        Validates: Requirements 3.1, 3.5
        """
        member = SimpleNamespace(id=42, display_name="Tester", mention="<@42>")
        result = await self._run_single_flow(
            target_user_id="42",
            selected_cards=["Iron-Man"],
            guild_member_map={42: member},
        )

        self.assertEqual(result.send_with_visibility.await_count, 1)
        call = result.send_with_visibility.await_args
        embed = call.kwargs.get("embed")
        self.assertIsNotNone(embed, "Result message must be sent as an embed")

        text_parts: list[str] = [
            str(embed.title or ""),
            str(embed.description or ""),
        ]
        for field in embed.fields:
            text_parts.append(str(field.name or ""))
            text_parts.append(str(field.value or ""))
        combined = " ".join(text_parts)

        self.assertIn(
            "Iron-Man",
            combined,
            "Confirmation embed must mention the granted card name",
        )
        self.assertIn(
            member.mention,
            combined,
            "Confirmation embed must mention the recipient user",
        )

    # -- Test 3: rejection without DB change -------------------------------

    async def test_single_mode_invalid_target_rejects_without_db_change(
        self,
    ) -> None:
        """No selection → no card grant, no result embed, abort message sent.

        Models the case where the admin's target picker times out or returns
        an invalid value. The flow must short-circuit before any DB write
        and surface a clear abort message.

        Validates: Requirements 3.1, 3.5
        """
        result = await self._run_single_flow(
            target_user_id=None,  # no selection / invalid
            selected_cards=["Iron-Man"],
        )

        # No DB write must happen.
        result.add_exact.assert_not_awaited()

        # No final visibility-aware result must be posted.
        result.send_with_visibility.assert_not_awaited()

        # The flow must inform the admin via followup that the action was
        # aborted because nothing was selected.
        followup_send = result.interaction.followup.send
        self.assertGreaterEqual(followup_send.await_count, 1)

        last_call = followup_send.await_args_list[-1]
        last_content = (
            last_call.args[0]
            if last_call.args
            else last_call.kwargs.get("content", "")
        )
        self.assertIn("Keine Auswahl", str(last_content))


# ---------------------------------------------------------------------------
# Multi-mode helpers
# ---------------------------------------------------------------------------


class _FakeDustMultiUserSelectView:
    """Stand-in for ``bot.DustMultiUserSelectView`` used in Multi mode."""

    def __init__(self, value):
        # ``value`` is a list[int] of selected user ids (or None / empty list
        # when nothing was picked).
        self.value = value
        self._message = None

    async def wait(self):
        return None

    def bind_message(self, message):
        self._message = message

    def _content(self) -> str:
        return "Wähle Nutzer:"

    def _summary_embed(self):
        return None

    def stop(self):
        pass


class _FakeGiveCardConfirmView:
    """Stand-in for ``bot.GiveCardConfirmView`` returning a pre-set value."""

    def __init__(self, value=True):
        # ``True`` = confirmed, ``False`` = cancelled, ``None`` = timed out.
        self.value = value

    async def wait(self):
        return None

    def stop(self):
        pass


class KarteGebenMultiFlowTests(unittest.IsolatedAsyncioTestCase):
    """Verifies Multi-mode behaviour for ``/karte-geben``.

    Validates: Requirements 3.2, 3.3, 3.4, 3.5
    """

    async def _run_multi_flow(
        self,
        *,
        target_user_ids,
        selected_cards,
        guild_member_map=None,
        add_returns=True,
        confirm_value=True,
    ):
        """Drive ``bot.give.callback`` through the Multi-mode branch.

        ``add_returns`` may be a boolean (applies to every call) or a callable
        ``(user_id, card_name) -> bool`` for fine-grained control over which
        ``add_exact_card_variant_once`` calls succeed.
        """
        if guild_member_map is None:
            guild_member_map = {
                uid: SimpleNamespace(
                    id=uid,
                    display_name=f"User{uid}",
                    mention=f"<@{uid}>",
                )
                for uid in (target_user_ids or [])
            }

        interaction = _build_interaction(
            admin_user_id=99, guild_member_map=guild_member_map
        )

        send_with_visibility_mock = AsyncMock()

        if callable(add_returns):
            add_exact_mock = AsyncMock(side_effect=add_returns)
        else:
            add_exact_mock = AsyncMock(return_value=add_returns)

        get_karte_mock = AsyncMock(return_value=None)
        dust_multi_factory = MagicMock(
            return_value=_FakeDustMultiUserSelectView(target_user_ids)
        )
        multi_card_view_factory = MagicMock(
            return_value=_FakeMultiCardSelectView(selected_cards)
        )
        give_card_confirm_factory = MagicMock(
            return_value=_FakeGiveCardConfirmView(confirm_value)
        )
        admin_view_factory = MagicMock(
            side_effect=AssertionError(
                "AdminUserSelectView must NOT be used in Multi mode"
            )
        )

        items = bot_module._command_api._items
        with patch.dict(
            items,
            {
                "is_channel_allowed": AsyncMock(return_value=True),
                "is_admin": AsyncMock(return_value=True),
                "command_visibility_key_for_interaction": MagicMock(
                    return_value=None
                ),
                "AdminUserSelectView": admin_view_factory,
                "DustMultiUserSelectView": dust_multi_factory,
                "MultiCardSelectView": multi_card_view_factory,
                "GiveCardConfirmView": give_card_confirm_factory,
                "add_exact_card_variant_once": add_exact_mock,
                "get_karte_by_name": get_karte_mock,
                "_card_rarity_color": MagicMock(return_value=None),
                "_card_by_name_local": MagicMock(return_value=None),
                "_send_with_visibility": send_with_visibility_mock,
            },
            clear=False,
        ):
            await bot_module.give.callback(interaction, "multi")

        return SimpleNamespace(
            interaction=interaction,
            send_with_visibility=send_with_visibility_mock,
            add_exact=add_exact_mock,
            dust_multi_factory=dust_multi_factory,
            multi_card_view_factory=multi_card_view_factory,
            give_card_confirm_factory=give_card_confirm_factory,
            admin_view_factory=admin_view_factory,
        )

    @staticmethod
    def _embed_text(embed) -> str:
        """Flatten an embed (title, description, fields) into one string."""
        parts: list[str] = [
            str(embed.title or ""),
            str(embed.description or ""),
        ]
        for field in embed.fields:
            parts.append(str(field.name or ""))
            parts.append(str(field.value or ""))
        return " ".join(parts)

    # -- Test 1: every (user, card) pair receives a grant call -------------

    async def test_multi_mode_grants_each_card_to_each_user(self) -> None:
        """2 users × 3 cards → 6 grant calls, one summary embed.

        Validates: Requirements 3.2, 3.3, 3.5
        """
        user_ids = [101, 202]
        cards = ["Iron-Man", "Hulk", "Thor"]

        result = await self._run_multi_flow(
            target_user_ids=user_ids,
            selected_cards=cards,
        )

        # Confirmation view must be used in Multi mode.
        result.give_card_confirm_factory.assert_called_once()

        # add_exact_card_variant_once must be called for every (user, card)
        # combination (2 × 3 = 6 calls).
        self.assertEqual(result.add_exact.await_count, len(user_ids) * len(cards))

        actual_pairs = {
            (call.args[0], call.args[1])
            for call in result.add_exact.await_args_list
        }
        expected_pairs = {(uid, card) for uid in user_ids for card in cards}
        self.assertEqual(actual_pairs, expected_pairs)

        # Exactly one final summary embed is sent through the visibility
        # helper.
        self.assertEqual(result.send_with_visibility.await_count, 1)

    # -- Test 2: summary embed lists every recipient and every card --------

    async def test_multi_mode_summary_lists_cards_per_user(self) -> None:
        """Summary embed mentions every user and every card name.

        Validates: Requirements 3.2, 3.3, 3.5
        """
        user_ids = [101, 202]
        cards = ["Iron-Man", "Hulk", "Thor"]
        members = {
            101: SimpleNamespace(id=101, display_name="Alice", mention="<@101>"),
            202: SimpleNamespace(id=202, display_name="Bob", mention="<@202>"),
        }

        result = await self._run_multi_flow(
            target_user_ids=user_ids,
            selected_cards=cards,
            guild_member_map=members,
        )

        self.assertEqual(result.send_with_visibility.await_count, 1)
        call = result.send_with_visibility.await_args
        embed = call.kwargs.get("embed")
        self.assertIsNotNone(embed, "Summary must be sent as an embed")

        text = self._embed_text(embed)

        for member in members.values():
            self.assertIn(
                member.mention,
                text,
                f"Summary embed must mention recipient {member.mention}",
            )
        for card in cards:
            self.assertIn(
                card,
                text,
                f"Summary embed must list card {card}",
            )

    # -- Test 3: partial failure does not abort remaining grants ----------

    async def test_multi_mode_partial_failure_continues_remaining(self) -> None:
        """One (user, card) pair fails — the rest still complete and the
        summary distinguishes added vs skipped vs failed.

        A true grant FAILURE (e.g. DB error / unknown card) is surfaced via
        a dedicated "fehlgeschlagen" bucket, separate from the
        "bereits vorhanden" warning bucket. To exercise that branch the
        fake ``add_exact_card_variant_once`` raises an exception for the
        failing pair instead of returning ``False``.

        Validates: Requirements 3.2, 3.4, 3.5
        """
        user_ids = [101, 202]
        cards = ["Iron-Man", "Hulk", "Thor"]
        members = {
            101: SimpleNamespace(id=101, display_name="Alice", mention="<@101>"),
            202: SimpleNamespace(id=202, display_name="Bob", mention="<@202>"),
        }

        # User 202 fails specifically on "Hulk"; everything else succeeds.
        failing_pair = (202, "Hulk")

        async def fake_add(user_id, card_name):
            if user_id == failing_pair[0] and card_name == failing_pair[1]:
                raise RuntimeError("simulated DB error")
            return True

        result = await self._run_multi_flow(
            target_user_ids=user_ids,
            selected_cards=cards,
            guild_member_map=members,
            add_returns=fake_add,
        )

        # Every pair was still attempted — no early exit on the failure.
        self.assertEqual(result.add_exact.await_count, len(user_ids) * len(cards))
        attempted_pairs = {
            (call.args[0], call.args[1])
            for call in result.add_exact.await_args_list
        }
        self.assertIn(failing_pair, attempted_pairs)

        # Exactly one summary embed.
        self.assertEqual(result.send_with_visibility.await_count, 1)
        embed = result.send_with_visibility.await_args.kwargs.get("embed")
        self.assertIsNotNone(embed)
        text = self._embed_text(embed)

        # The failing card name must still appear somewhere in the summary
        # (now reported per-user in the dedicated "fehlgeschlagen" bucket).
        self.assertIn("Hulk", text)

        # The summary must distinguish successful vs failed grants — the
        # production code routes exceptions into a dedicated
        # "fehlgeschlagen" bucket alongside "✅ hinzugefügt" and
        # "⚠️ bereits vorhanden".
        self.assertIn("✅", text, "Summary must mark successful grants")
        self.assertIn(
            "fehlgeschlagen",
            text.lower(),
            "Summary must surface true grant failures in a dedicated bucket",
        )

    # -- Test 4: empty user selection aborts before any grant -------------

    async def test_multi_mode_no_users_selected_aborts(self) -> None:
        """No users selected → no grant calls, abort message via followup.

        Validates: Requirements 3.2, 3.5
        """
        result = await self._run_multi_flow(
            target_user_ids=[],  # nothing selected
            selected_cards=["Iron-Man"],
            guild_member_map={},
        )

        # No DB writes.
        result.add_exact.assert_not_awaited()

        # No final summary embed.
        result.send_with_visibility.assert_not_awaited()

        # The card-selection view must never be constructed if no users are
        # available — the flow has to short-circuit on the user step.
        result.multi_card_view_factory.assert_not_called()
        result.give_card_confirm_factory.assert_not_called()

        # The admin must be told the action was aborted.
        followup_send = result.interaction.followup.send
        self.assertGreaterEqual(followup_send.await_count, 1)
        last_call = followup_send.await_args_list[-1]
        last_content = (
            last_call.args[0]
            if last_call.args
            else last_call.kwargs.get("content", "")
        )
        self.assertIn("Keine Nutzer", str(last_content))


if __name__ == "__main__":
    unittest.main()
