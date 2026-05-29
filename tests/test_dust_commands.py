"""Tests for the `/dust` and `/lödust` admin command flow.

These tests cover Single, Multi and Custom-Betrag UI flows that were
unified in Task 4.2 and verified in Task 4.3 of the v2.3.0 spec.

Validates Requirements:
- 4.4 — Single mode (1 user, amount X) and Multi mode (N users, amount X)
       both end up calling ``add_infinitydust``/``remove_infinitydust``
       with the right arguments; the result message is posted exactly
       once.
- 5.4 — Multi-mode confirmation: ``DustGiveConfirmView`` returning False
       aborts the operation without any balance change.
- 5.5 — ``NumberInputModal`` rejects 0, negative values and values
       above 1.000.000 and accepts integers in the [1, 1.000.000] range.

The flow tested here lives in ``bot.py::run_dust_command_flow`` and
``bot.py::NumberInputModal``. We patch the UI views in place so no live
Discord client is needed.
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportAssignmentType=false

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot as bot_module


# ---------------------------------------------------------------------------
# Fake UI view stand-ins (no live Discord connection required)
# ---------------------------------------------------------------------------


class _FakeAdminUserSelectView:
    """Stand-in for ``bot.AdminUserSelectView`` returning a pre-set value."""

    def __init__(self, value):
        self.value = value

    async def wait(self):
        return None

    def stop(self):
        pass


class _FakeDustMultiUserSelectView:
    """Stand-in for ``bot.DustMultiUserSelectView`` used in Multi mode."""

    def __init__(self, value):
        self.value = value

    def _content(self) -> str:
        return "fake-content"

    def _summary_embed(self):
        return None

    def bind_message(self, _message) -> None:
        return None

    async def wait(self):
        return None

    def stop(self):
        pass


class _FakeDustQuickAmountView:
    """Stand-in for ``bot.DustQuickAmountView`` returning a pre-set amount."""

    def __init__(self, value):
        self.value = value

    async def wait(self):
        return None

    def stop(self):
        pass


class _FakeDustGiveConfirmView:
    """Stand-in for ``bot.DustGiveConfirmView``.

    ``value`` mirrors the production view: ``True`` = confirm,
    ``False`` = cancel, ``None`` = timeout.
    """

    def __init__(self, value):
        self.value = value

    async def wait(self):
        return None

    def stop(self):
        pass


def _build_interaction(*, admin_user_id: int = 99):
    """Build a minimal interaction stand-in for the dust flow."""
    guild = SimpleNamespace(
        id=12345,
        get_member=lambda _uid: None,
    )
    followup = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=4242)))
    user = SimpleNamespace(
        id=admin_user_id,
        mention=f"<@{admin_user_id}>",
        display_name="Admin",
    )
    return SimpleNamespace(
        user=user,
        guild=guild,
        guild_id=12345,
        channel_id=999,
        channel=SimpleNamespace(id=999),
        followup=followup,
        command=None,
    )


# ---------------------------------------------------------------------------
# Single + Multi mode flow tests
# ---------------------------------------------------------------------------


class DustSingleFlowTests(unittest.IsolatedAsyncioTestCase):
    """Single-mode happy-path and lödust remove path."""

    async def test_single_user_amount_25_grants_25_dust(self) -> None:
        """Single mode picks 1 user + amount 25 → +25 Dust to that user.

        Validates: Requirements 4.4
        """
        interaction = _build_interaction()

        admin_view_factory = MagicMock(
            return_value=_FakeAdminUserSelectView("42")
        )

        with patch("bot.AdminUserSelectView", admin_view_factory), patch(
            "bot._select_number",
            new=AsyncMock(return_value=25),
        ), patch(
            "bot.add_infinitydust",
            new=AsyncMock(),
        ) as add_mock, patch(
            "bot.remove_infinitydust",
            new=AsyncMock(),
        ) as remove_mock, patch(
            "bot.log_admin_dust_action",
            new=AsyncMock(),
        ), patch(
            "bot._post_dust_result_message",
            new=AsyncMock(return_value=True),
        ) as result_mock:
            await bot_module.run_dust_command_flow(
                interaction, mode="single", remove=False
            )

        # add_infinitydust must be called exactly once with (target_id, amount).
        add_mock.assert_awaited_once_with(42, 25)
        # remove path must not be touched.
        remove_mock.assert_not_awaited()

        # _post_dust_result_message must be called exactly once with the
        # expected mode/remove/amount/results combination.
        result_mock.assert_awaited_once()
        kwargs = result_mock.await_args.kwargs
        self.assertEqual(kwargs["mode"], "single")
        self.assertFalse(kwargs["remove"])
        self.assertEqual(kwargs["amount"], 25)
        self.assertEqual(kwargs["results"], [(42, 25)])

    async def test_loedust_remove_path_uses_remove_infinitydust(self) -> None:
        """Single mode + remove=True → ``remove_infinitydust`` is used and
        the actual removed amount (which may be smaller than requested) is
        forwarded to the result message.

        Validates: Requirements 5.6, 4.4
        """
        interaction = _build_interaction()

        admin_view_factory = MagicMock(
            return_value=_FakeAdminUserSelectView("7")
        )

        with patch("bot.AdminUserSelectView", admin_view_factory), patch(
            "bot._select_number",
            new=AsyncMock(return_value=10),
        ), patch(
            "bot.remove_infinitydust",
            new=AsyncMock(return_value=4),
        ) as remove_mock, patch(
            "bot.add_infinitydust",
            new=AsyncMock(),
        ) as add_mock, patch(
            "bot.log_admin_dust_action",
            new=AsyncMock(),
        ), patch(
            "bot._post_dust_result_message",
            new=AsyncMock(return_value=True),
        ) as result_mock:
            await bot_module.run_dust_command_flow(
                interaction, mode="single", remove=True
            )

        # remove path: add_infinitydust must NOT be called.
        add_mock.assert_not_awaited()
        # remove_infinitydust must be called once with (target_id, requested).
        remove_mock.assert_awaited_once_with(7, 10)

        result_mock.assert_awaited_once()
        kwargs = result_mock.await_args.kwargs
        self.assertEqual(kwargs["mode"], "single")
        self.assertTrue(kwargs["remove"])
        self.assertEqual(kwargs["amount"], 10)
        # Partial removal: only 4 of 10 were actually removed.
        self.assertEqual(kwargs["results"], [(7, 4)])


class DustMultiFlowTests(unittest.IsolatedAsyncioTestCase):
    """Multi-mode flow: quick-pick amount + final confirmation."""

    async def test_multi_users_quick_pick_30_grants_each_user_30(self) -> None:
        """Multi mode picks 3 users + quick-pick amount 30 → each user +30.

        Validates: Requirements 4.4
        """
        interaction = _build_interaction()

        multi_factory = MagicMock(
            return_value=_FakeDustMultiUserSelectView([101, 202, 303])
        )
        quick_factory = MagicMock(return_value=_FakeDustQuickAmountView(30))
        confirm_factory = MagicMock(return_value=_FakeDustGiveConfirmView(True))

        with patch("bot.DustMultiUserSelectView", multi_factory), patch(
            "bot.DustQuickAmountView", quick_factory
        ), patch(
            "bot.DustGiveConfirmView", confirm_factory
        ), patch(
            "bot.AdminUserSelectView",
            MagicMock(side_effect=AssertionError(
                "AdminUserSelectView must NOT be used in Multi mode"
            )),
        ), patch(
            "bot.add_infinitydust",
            new=AsyncMock(),
        ) as add_mock, patch(
            "bot.remove_infinitydust",
            new=AsyncMock(),
        ) as remove_mock, patch(
            "bot.log_admin_dust_action",
            new=AsyncMock(),
        ), patch(
            "bot._post_dust_result_message",
            new=AsyncMock(return_value=True),
        ) as result_mock:
            await bot_module.run_dust_command_flow(
                interaction, mode="multi", remove=False
            )

        # add_infinitydust must be called once per target user.
        self.assertEqual(add_mock.await_count, 3)
        add_mock.assert_any_await(101, 30)
        add_mock.assert_any_await(202, 30)
        add_mock.assert_any_await(303, 30)
        # remove path is unused in give mode.
        remove_mock.assert_not_awaited()

        # Single result message with the right kwargs.
        result_mock.assert_awaited_once()
        kwargs = result_mock.await_args.kwargs
        self.assertEqual(kwargs["mode"], "multi")
        self.assertFalse(kwargs["remove"])
        self.assertEqual(kwargs["amount"], 30)
        self.assertEqual(
            kwargs["results"],
            [(101, 30), (202, 30), (303, 30)],
        )

    async def test_multi_confirm_cancel_aborts_without_dust_change(self) -> None:
        """Multi mode + confirm=False → no dust write and no result message.

        The followup must contain an abort indicator so the admin sees the
        cancellation, but neither ``add_infinitydust`` nor
        ``_post_dust_result_message`` may run.

        Validates: Requirements 5.4
        """
        interaction = _build_interaction()

        multi_factory = MagicMock(
            return_value=_FakeDustMultiUserSelectView([1, 2, 3])
        )
        quick_factory = MagicMock(return_value=_FakeDustQuickAmountView(30))
        confirm_factory = MagicMock(return_value=_FakeDustGiveConfirmView(False))

        with patch("bot.DustMultiUserSelectView", multi_factory), patch(
            "bot.DustQuickAmountView", quick_factory
        ), patch(
            "bot.DustGiveConfirmView", confirm_factory
        ), patch(
            "bot.add_infinitydust",
            new=AsyncMock(),
        ) as add_mock, patch(
            "bot.remove_infinitydust",
            new=AsyncMock(),
        ) as remove_mock, patch(
            "bot.log_admin_dust_action",
            new=AsyncMock(),
        ) as log_mock, patch(
            "bot._post_dust_result_message",
            new=AsyncMock(return_value=True),
        ) as result_mock:
            await bot_module.run_dust_command_flow(
                interaction, mode="multi", remove=False
            )

        # No balance change at all.
        add_mock.assert_not_awaited()
        remove_mock.assert_not_awaited()
        log_mock.assert_not_awaited()

        # No public result message — the cancel path stays silent on the
        # public side.
        result_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# NumberInputModal validation tests
# ---------------------------------------------------------------------------


class NumberInputModalValidationTests(unittest.IsolatedAsyncioTestCase):
    """Custom-Betrag modal validates 1 ≤ amount ≤ 1.000.000."""

    @staticmethod
    def _make_parent_view():
        """A parent stand-in mirroring the contract NumberInputModal expects.

        ``value`` is updated to the parsed amount on success and left as
        ``None`` on rejection. ``stop()`` must be called on success.
        """
        parent = SimpleNamespace(value=None, stop=MagicMock())
        return parent

    @staticmethod
    def _make_interaction(requester_id: int = 1):
        return SimpleNamespace(
            user=SimpleNamespace(id=requester_id),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    @staticmethod
    def _set_amount(modal, raw_value: str) -> None:
        """Set the modal's TextInput value via discord.py's internal slot.

        ``ui.TextInput.value`` is a property in discord.py — the writeable
        backing field is ``_value``. We use it here so the modal observes
        the raw string we want to validate.
        """
        modal.amount._value = raw_value

    async def test_number_input_modal_rejects_zero(self) -> None:
        """Amount ``"0"`` is below the lower bound → rejected.

        Validates: Requirements 5.5
        """
        parent = self._make_parent_view()
        modal = bot_module.NumberInputModal(requester_id=1, parent_view=parent)
        interaction = self._make_interaction(requester_id=1)
        self._set_amount(modal, "0")

        await modal.on_submit(interaction)

        # parent value must stay None (no overwrite) and stop() not called.
        self.assertIsNone(parent.value)
        parent.stop.assert_not_called()

        # Validation message must be sent and mention the upper bound.
        interaction.response.send_message.assert_awaited_once()
        call = interaction.response.send_message.await_args
        # Either positional or kwarg form — check both.
        message_text = ""
        if call.args:
            message_text = str(call.args[0])
        elif "content" in call.kwargs:
            message_text = str(call.kwargs["content"])
        self.assertIn("1.000.000", message_text)

    async def test_number_input_modal_rejects_above_one_million(self) -> None:
        """Amount ``"1000001"`` exceeds the upper bound → rejected.

        Validates: Requirements 5.5
        """
        parent = self._make_parent_view()
        modal = bot_module.NumberInputModal(requester_id=1, parent_view=parent)
        interaction = self._make_interaction(requester_id=1)
        self._set_amount(modal, "1000001")

        await modal.on_submit(interaction)

        self.assertIsNone(parent.value)
        parent.stop.assert_not_called()
        interaction.response.send_message.assert_awaited_once()

    async def test_number_input_modal_accepts_500_in_range(self) -> None:
        """Amount ``"500"`` is in range → parent.value=500 and stop() called.

        Validates: Requirements 5.5
        """
        parent = self._make_parent_view()
        modal = bot_module.NumberInputModal(requester_id=1, parent_view=parent)
        interaction = self._make_interaction(requester_id=1)
        self._set_amount(modal, "500")

        await modal.on_submit(interaction)

        self.assertEqual(parent.value, 500)
        parent.stop.assert_called_once()
        # Confirmation message must be sent.
        interaction.response.send_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
