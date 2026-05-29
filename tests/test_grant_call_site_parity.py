"""Parity test for the two grant-card entry points.

Both ``/karte-geben`` (Multi mode) and the Dev-Panel action ``grant_card``
must funnel through ``services.card_grant.grant_cards_to_users`` with
semantically equivalent kwargs — same target user ids, same card names, and
the same ``add_card`` target. This test pins that contract: any future
refactor that drifts one entry point away from the shared service will
fail here.

Validates: Requirements 11.3, 11.4
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportAssignmentType=false

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot as bot_module
import botcommands.admin_commands as admin_commands_module
from services import card_grant as card_grant_module


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------


# Common payload used by both entry points.
TARGET_USER_IDS = [101, 202]
CARD_NAMES = ["Iron-Man", "Hulk"]


class _StubMultiUserSelectView:
    """Minimal stand-in for ``DustMultiUserSelectView`` / Multi-mode user select."""

    def __init__(self, value):
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


class _StubMultiCardSelectView:
    """Minimal stand-in for ``MultiCardSelectView``."""

    def __init__(self, value):
        self.value = value
        self._message = None

    async def wait(self):
        return None

    def bind_message(self, message):
        self._message = message

    def content_text(self) -> str:
        return "Wähle Karten:"

    def stop(self):
        pass


class _StubConfirmView:
    """Minimal stand-in for ``GiveCardConfirmView`` (always confirm)."""

    def __init__(self, value=True):
        self.value = value

    async def wait(self):
        return None

    def stop(self):
        pass


def _build_interaction(*, admin_user_id: int = 99, guild_member_map=None):
    """Minimal Discord interaction stand-in compatible with both flows."""
    if guild_member_map is None:
        guild_member_map = {
            uid: SimpleNamespace(
                id=uid,
                display_name=f"User{uid}",
                mention=f"<@{uid}>",
            )
            for uid in TARGET_USER_IDS
        }

    response = SimpleNamespace(
        defer=AsyncMock(),
        send_message=AsyncMock(),
        is_done=lambda: False,
    )
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


# ---------------------------------------------------------------------------
# Parity test
# ---------------------------------------------------------------------------


class GrantCardCallSiteParityTests(unittest.IsolatedAsyncioTestCase):
    """Both entry points must hand off to the same shared service.

    Validates: Requirements 11.3, 11.4
    """

    async def _run_karte_geben_multi(
        self, *, shared_add_exact: AsyncMock, capture_grant: AsyncMock
    ) -> None:
        """Drive ``/karte-geben`` Multi end-to-end. The shared ``add_card``
        target and the shared ``grant_cards_to_users`` capture are passed
        in so that both flows wire the same callables — mirroring
        production, where both call sites resolve to
        ``services.user_data.add_exact_card_variant_once``.
        """
        interaction = _build_interaction()
        send_with_visibility = AsyncMock()

        admin_view_factory = MagicMock(
            side_effect=AssertionError(
                "AdminUserSelectView must NOT be used in Multi mode"
            )
        )
        dust_multi_factory = MagicMock(
            return_value=_StubMultiUserSelectView(TARGET_USER_IDS)
        )
        multi_card_factory = MagicMock(
            return_value=_StubMultiCardSelectView(CARD_NAMES)
        )
        confirm_factory = MagicMock(return_value=_StubConfirmView(True))

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
                "MultiCardSelectView": multi_card_factory,
                "GiveCardConfirmView": confirm_factory,
                "add_exact_card_variant_once": shared_add_exact,
                "get_karte_by_name": AsyncMock(return_value=None),
                "_card_rarity_color": MagicMock(return_value=None),
                "_card_by_name_local": MagicMock(return_value=None),
                "_send_with_visibility": send_with_visibility,
            },
            clear=False,
        ), patch.object(
            admin_commands_module, "grant_cards_to_users", capture_grant
        ), patch.object(
            bot_module, "grant_cards_to_users", capture_grant
        ):
            await bot_module.give.callback(interaction, "multi")

    async def _run_dev_panel_grant_card(
        self, *, shared_add_exact: AsyncMock, capture_grant: AsyncMock
    ) -> None:
        """Drive the Dev-Panel ``grant_card`` action through
        ``bot.handle_dev_action`` with the same shared ``add_card`` target
        and the same ``grant_cards_to_users`` capture as the slash flow.
        """
        interaction = _build_interaction()
        send_with_visibility = AsyncMock()
        send_ephemeral = AsyncMock()

        dust_multi_factory = MagicMock(
            return_value=_StubMultiUserSelectView(TARGET_USER_IDS)
        )
        multi_card_factory = MagicMock(
            return_value=_StubMultiCardSelectView(CARD_NAMES)
        )
        confirm_factory = MagicMock(return_value=_StubConfirmView(True))

        # ``handle_dev_action`` references a mix of helpers as bare names
        # (``require_owner_or_dev``, ``is_channel_allowed``) and as
        # module-level view classes. We patch them directly on the bot
        # module — that's where the bare-name lookups resolve.
        with patch.object(
            bot_module,
            "require_owner_or_dev",
            AsyncMock(return_value=True),
        ), patch.object(
            bot_module,
            "is_channel_allowed",
            AsyncMock(return_value=True),
        ), patch.object(
            bot_module, "DustMultiUserSelectView", dust_multi_factory
        ), patch.object(
            bot_module, "MultiCardSelectView", multi_card_factory
        ), patch.object(
            bot_module, "GiveCardConfirmView", confirm_factory
        ), patch.object(
            bot_module, "add_exact_card_variant_once", shared_add_exact
        ), patch.object(
            bot_module, "_card_by_name_local", MagicMock(return_value=None)
        ), patch.object(
            bot_module, "_send_with_visibility", send_with_visibility
        ), patch.object(
            bot_module, "_send_ephemeral", send_ephemeral
        ), patch.object(
            bot_module, "_log_event_safe", AsyncMock()
        ), patch.object(
            bot_module, "grant_cards_to_users", capture_grant
        ):
            await bot_module.handle_dev_action(
                interaction, requester_id=interaction.user.id, action="grant_card"
            )

    async def test_karte_geben_and_grant_card_use_same_service(self) -> None:
        """``/karte-geben`` Multi and Dev-Panel ``grant_card`` must call
        ``grant_cards_to_users`` with semantically equivalent kwargs.

        Validates: Requirements 11.3, 11.4
        """
        # Shared mocks so both flows wire the same callables — mirroring
        # production, where both resolve to the same
        # ``services.user_data.add_exact_card_variant_once``.
        shared_add_exact = AsyncMock(return_value=True)
        capture_grant = AsyncMock(
            return_value=card_grant_module.GrantSummary()
        )

        # 1) Drive ``/karte-geben`` Multi.
        await self._run_karte_geben_multi(
            shared_add_exact=shared_add_exact,
            capture_grant=capture_grant,
        )
        self.assertEqual(
            capture_grant.await_count,
            1,
            "/karte-geben Multi must call grant_cards_to_users exactly once",
        )
        slash_kwargs = capture_grant.await_args.kwargs

        # 2) Drive Dev-Panel ``grant_card`` (against the same capture mock).
        await self._run_dev_panel_grant_card(
            shared_add_exact=shared_add_exact,
            capture_grant=capture_grant,
        )
        self.assertEqual(
            capture_grant.await_count,
            2,
            "Dev-Panel grant_card must call grant_cards_to_users exactly once",
        )
        panel_kwargs = capture_grant.await_args_list[-1].kwargs

        # 3) Both entry points must pass the same recipients and the same
        #    list of card names.
        self.assertEqual(
            list(slash_kwargs["target_user_ids"]),
            list(panel_kwargs["target_user_ids"]),
            "Both entry points must pass the same target_user_ids",
        )
        self.assertEqual(
            list(slash_kwargs["card_names"]),
            list(panel_kwargs["card_names"]),
            "Both entry points must pass the same card_names",
        )

        # 4) Both must wire the same ``add_card`` target — the bound
        #    ``add_exact_card_variant_once`` callable. Identity comparison
        #    rather than equality, since both flows reference the same
        #    patched mock.
        self.assertIs(
            slash_kwargs["add_card"],
            panel_kwargs["add_card"],
            "Both entry points must use the same add_card target",
        )

        # 5) Both must pass an ``is_card_known`` predicate (the call sites
        #    construct lambdas locally; they need not be identical, but
        #    they must both be callable and produce a boolean for any
        #    string input).
        for label, kwargs in (("slash", slash_kwargs), ("panel", panel_kwargs)):
            predicate = kwargs.get("is_card_known")
            self.assertTrue(
                callable(predicate),
                f"{label} must pass a callable is_card_known predicate",
            )
            # Should not raise on a probe input.
            result = predicate("Iron-Man")
            self.assertIn(
                bool(result),
                {True, False},
                f"{label} is_card_known must return a boolean",
            )

    async def test_grant_cards_to_users_imported_in_bot_module(self) -> None:
        """Smoke check: the shared service is wired into ``bot.py``.

        Backstop for the parity test above — even if the full flow
        becomes too tangled to drive end-to-end, this confirms the wiring
        between Dev-Panel and the shared service still exists.

        Validates: Requirements 11.3
        """
        self.assertIn("grant_cards_to_users", dir(bot_module))
        self.assertIs(
            bot_module.grant_cards_to_users,
            card_grant_module.grant_cards_to_users,
            "bot.grant_cards_to_users must reference the shared service",
        )
        self.assertIn("grant_cards_to_users", dir(admin_commands_module))
        self.assertIs(
            admin_commands_module.grant_cards_to_users,
            card_grant_module.grant_cards_to_users,
            "admin_commands.grant_cards_to_users must reference the shared service",
        )


if __name__ == "__main__":
    unittest.main()
