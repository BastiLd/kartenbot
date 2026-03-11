import unittest

import discord

from botcore.interaction_utils import defer_interaction, edit_interaction_message, send_interaction_response


class _DummyHttpResponse:
    def __init__(self, status: int, reason: str, text: str = "error"):
        self.status = status
        self.reason = reason
        self.text = text


class _DummyMessage:
    def __init__(self, message_id: int = 42):
        self.id = message_id


class _DummyFollowup:
    def __init__(self):
        self.sent = []
        self.edits = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return kwargs

    async def edit_message(self, message_id: int, **kwargs):
        self.edits.append({"message_id": message_id, **kwargs})
        return kwargs


class _DummyResponse:
    def __init__(self):
        self._done = False
        self._interaction = None
        self.sent_messages = []
        self.edits = []
        self.defer_calls = []
        self.send_mode = None
        self.edit_mode = None
        self.defer_mode = None

    def bind(self, interaction) -> None:
        self._interaction = interaction

    def is_done(self):
        return self._done

    async def send_message(self, **kwargs):
        if self.send_mode == "responded":
            raise discord.InteractionResponded(self._interaction)
        if self.send_mode == "notfound":
            raise discord.NotFound(_DummyHttpResponse(404, "Not Found"), "expired")
        self._done = True
        self.sent_messages.append(kwargs)
        return kwargs

    async def edit_message(self, **kwargs):
        if self.edit_mode == "responded":
            raise discord.InteractionResponded(self._interaction)
        if self.edit_mode == "notfound":
            raise discord.NotFound(_DummyHttpResponse(404, "Not Found"), "expired")
        self._done = True
        self.edits.append(kwargs)
        return kwargs

    async def defer(self, *args, **kwargs):
        if self.defer_mode == "typeerror_ephemeral" and "ephemeral" in kwargs:
            raise TypeError("ephemeral unsupported")
        if self.defer_mode == "responded":
            raise discord.InteractionResponded(self._interaction)
        if self.defer_mode == "notfound":
            raise discord.NotFound(_DummyHttpResponse(404, "Not Found"), "expired")
        self._done = True
        self.defer_calls.append(kwargs)
        return None


class _DummyInteraction:
    def __init__(self):
        self.message = _DummyMessage()
        self.followup = _DummyFollowup()
        self.response = _DummyResponse()
        self.response.bind(self)


class InteractionUtilsTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_uses_initial_response_before_interaction_is_done(self) -> None:
        interaction = _DummyInteraction()
        result = await send_interaction_response(interaction, content="Hallo", ephemeral=True)
        self.assertEqual(result, {"content": "Hallo", "ephemeral": True})
        self.assertEqual(interaction.response.sent_messages, [{"content": "Hallo", "ephemeral": True}])
        self.assertEqual(interaction.followup.sent, [])

    async def test_send_uses_followup_when_interaction_is_already_done(self) -> None:
        interaction = _DummyInteraction()
        interaction.response._done = True
        result = await send_interaction_response(interaction, content="Hallo", ephemeral=True)
        self.assertEqual(result, {"content": "Hallo", "ephemeral": True})
        self.assertEqual(interaction.followup.sent, [{"content": "Hallo", "ephemeral": True}])

    async def test_send_falls_back_to_followup_after_interaction_responded(self) -> None:
        interaction = _DummyInteraction()
        interaction.response.send_mode = "responded"
        result = await send_interaction_response(interaction, content="Hallo", ephemeral=True)
        self.assertEqual(result, {"content": "Hallo", "ephemeral": True})
        self.assertEqual(interaction.followup.sent, [{"content": "Hallo", "ephemeral": True}])

    async def test_send_returns_none_for_expired_interaction(self) -> None:
        interaction = _DummyInteraction()
        interaction.response.send_mode = "notfound"
        result = await send_interaction_response(interaction, content="Hallo", ephemeral=True)
        self.assertIsNone(result)

    async def test_defer_uses_response_when_available(self) -> None:
        interaction = _DummyInteraction()
        self.assertTrue(await defer_interaction(interaction))
        self.assertEqual(interaction.response.defer_calls, [{}])

    async def test_defer_retries_without_ephemeral_when_signature_is_older(self) -> None:
        interaction = _DummyInteraction()
        interaction.response.defer_mode = "typeerror_ephemeral"
        self.assertTrue(await defer_interaction(interaction, ephemeral=True))
        self.assertEqual(interaction.response.defer_calls, [{}])

    async def test_edit_falls_back_to_followup_when_already_responded(self) -> None:
        interaction = _DummyInteraction()
        interaction.response.edit_mode = "responded"
        result = await edit_interaction_message(interaction, content="Aktualisiert")
        self.assertEqual(result, {"content": "Aktualisiert"})
        self.assertEqual(
            interaction.followup.edits,
            [{"message_id": 42, "content": "Aktualisiert"}],
        )


if __name__ == "__main__":
    unittest.main()
