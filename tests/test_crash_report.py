"""Tests für den globalen Slash-Command-Crash-Report (Update v0.2.2).

Der ``KatabumpCommandTree.on_error``-Handler soll:
  * erwartete Ablehnungen (``app_commands.CheckFailure`` aus ``interaction_check``)
    still ignorieren – kein Owner-Report, keine Nutzer-Meldung,
  * bei echten Fehlern automatisch eine DM mit Traceback an den Owner schicken
    und den ausführenden Nutzer kurz (ephemeral) informieren.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord import app_commands

import bot as bot_module


def _fake_interaction():
    return SimpleNamespace(
        command=SimpleNamespace(qualified_name="täglich", name="täglich"),
        guild=SimpleNamespace(name="TestGuild"),
        channel=SimpleNamespace(id=456, mention="<#456>"),
        user=SimpleNamespace(id=77, display_name="Tester", name="Tester"),
    )


class CrashReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_failure_is_ignored(self) -> None:
        tree = bot_module.bot.tree
        interaction = _fake_interaction()
        with patch("bot._send_basti_log_dm", new=AsyncMock()) as dm_mock, patch(
            "bot.send_interaction_response", new=AsyncMock()
        ) as notice_mock:
            await tree.on_error(interaction, app_commands.CheckFailure("blocked"))
        dm_mock.assert_not_awaited()
        notice_mock.assert_not_awaited()

    async def test_real_error_reports_to_owner_and_notifies_user(self) -> None:
        tree = bot_module.bot.tree
        interaction = _fake_interaction()
        boom = RuntimeError("boom-explosion")
        with patch("bot._send_basti_log_dm", new=AsyncMock()) as dm_mock, patch(
            "bot.send_interaction_response", new=AsyncMock()
        ) as notice_mock:
            await tree.on_error(interaction, boom)

        # Owner bekommt genau einen Report mit Traceback + Kontext.
        dm_mock.assert_awaited_once()
        log_text = dm_mock.await_args.args[0]
        self.assertIn("RuntimeError", log_text)
        self.assertIn("boom-explosion", log_text)
        self.assertEqual(dm_mock.await_args.kwargs.get("title"), "Command-Fehler / Traceback")
        context_lines = dm_mock.await_args.kwargs.get("context_lines") or []
        joined = "\n".join(context_lines)
        self.assertIn("/täglich", joined)
        self.assertIn("TestGuild", joined)
        self.assertIn("77", joined)

        # Ausführender Nutzer wird kurz (ephemeral) informiert.
        notice_mock.assert_awaited_once()
        self.assertTrue(notice_mock.await_args.kwargs.get("ephemeral"))

    async def test_unwraps_command_invoke_error_original(self) -> None:
        tree = bot_module.bot.tree
        interaction = _fake_interaction()
        original = ValueError("inner-cause")
        wrapped = SimpleNamespace(original=original)  # imitiert CommandInvokeError.original
        with patch("bot._send_basti_log_dm", new=AsyncMock()) as dm_mock, patch(
            "bot.send_interaction_response", new=AsyncMock()
        ):
            await tree.on_error(interaction, wrapped)
        dm_mock.assert_awaited_once()
        log_text = dm_mock.await_args.args[0]
        self.assertIn("inner-cause", log_text)


if __name__ == "__main__":
    unittest.main()
