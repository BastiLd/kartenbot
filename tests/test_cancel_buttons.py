"""Tests für die Cancel-Buttons der Challenge (Req. 12.1/12.3/12.5)."""

import unittest
from unittest import mock

import bot as bot_module


def _make_challenge_view(challenger_id=111, challenged_id=222):
    return bot_module.ChallengeResponseView(
        challenger_id,
        challenged_id,
        "Iron-Man",
        request_id=999,
        origin_channel_id=None,
        thread_id=555,
        thread_created=True,
    )


class _FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))

    async def defer(self, *args, **kwargs):
        pass


class _FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.mention = f"<@{uid}>"


class _FakeInteraction:
    def __init__(self, uid):
        self.user = _FakeUser(uid)
        self.response = _FakeResponse()
        self.channel = mock.Mock()
        self.guild = None


class ChallengeCancelTests(unittest.IsolatedAsyncioTestCase):
    def test_cancel_button_present(self):
        view = _make_challenge_view()
        ids = {getattr(c, "custom_id", None) for c in view.children}
        self.assertIn("fight_challenge:cancel", ids)

    async def test_third_user_blocked(self):
        view = _make_challenge_view()
        cancel_btn = next(c for c in view.children if getattr(c, "custom_id", None) == "fight_challenge:cancel")
        interaction = _FakeInteraction(uid=333)  # weder Challenger noch Acceptor
        with mock.patch.object(bot_module, "claim_fight_request") as claim:
            await cancel_btn.callback(interaction)
            claim.assert_not_called()
        self.assertTrue(interaction.response.messages)

    async def test_challenger_can_cancel(self):
        view = _make_challenge_view(challenger_id=111, challenged_id=222)
        cancel_btn = next(c for c in view.children if getattr(c, "custom_id", None) == "fight_challenge:cancel")
        interaction = _FakeInteraction(uid=111)
        with mock.patch.object(bot_module, "claim_fight_request", return_value=True) as claim, \
             mock.patch.object(bot_module.afk_tracker, "delete_state") as del_state, \
             mock.patch.object(bot_module, "_safe_send_channel") as send_ch, \
             mock.patch.object(bot_module, "_maybe_delete_fight_thread") as del_thread:
            await cancel_btn.callback(interaction)
            claim.assert_called_once()
            # Req. 12.5: AFK-State wird sofort entfernt.
            del_state.assert_called_once_with("challenge:999")
            del_thread.assert_called_once()


if __name__ == "__main__":
    unittest.main()
