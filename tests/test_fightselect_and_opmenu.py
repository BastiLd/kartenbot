"""Tests für zwei Community-Bugfixes (v2.3.18):

1. ``FightCardSelectView`` muss bei mehr als 25 Basiskarten blättern können
   (vorher wurden nur die ersten 25 Karten gezeigt, kein "Weiter"-Button).
2. ``/op-verwaltung`` bietet "card give (Solo)" und "card give (Multi)" als
   eigene Menüpunkte und behält alle bestehenden Aktionen.
"""

import unittest

import bot
from karten import karten


def _distinct_base_card_names(limit: int) -> list[str]:
    base_to_name: dict[str, str] = {}
    for card in karten:
        base_name = str(card.get("base_name") or card.get("name") or "").strip()
        name = str(card.get("name") or "").strip()
        if base_name and name and base_name not in base_to_name:
            base_to_name[base_name] = name
    return list(base_to_name.values())[:limit]


class FightCardSelectPaginationTests(unittest.IsolatedAsyncioTestCase):
    def _make_view(self, card_names: list[str]) -> "bot.FightCardSelectView":
        return bot.FightCardSelectView(
            111,
            222,
            card_names[0],
            card_names,
            origin_channel_id=None,
            thread_id=None,
            thread_created=False,
        )

    def test_pagination_appears_with_more_than_25_cards(self) -> None:
        names = _distinct_base_card_names(30)
        self.assertGreaterEqual(len(names), 26, "Test braucht >25 verschiedene Basiskarten")
        view = self._make_view(names)
        self.assertGreater(view._page_count(), 1, "Mit >25 Karten muss es mehrere Seiten geben")
        # Auf Seite 0: höchstens 25 Optionen (Discord-Limit) und ein aktiver Weiter-Button.
        self.assertLessEqual(len(view.select.options), 25)
        buttons = [c for c in view.children if isinstance(c, bot.ui.Button)]
        next_btn = next((b for b in buttons if b.custom_id == "fight_card_select:next"), None)
        prev_btn = next((b for b in buttons if b.custom_id == "fight_card_select:prev"), None)
        self.assertIsNotNone(next_btn, "Weiter-Button muss vorhanden sein")
        self.assertIsNotNone(prev_btn, "Zurück-Button muss vorhanden sein")
        assert next_btn is not None and prev_btn is not None
        self.assertFalse(next_btn.disabled, "Weiter muss auf Seite 1 aktiv sein")
        self.assertTrue(prev_btn.disabled, "Zurück muss auf Seite 1 deaktiviert sein")

    def test_second_page_shows_remaining_cards(self) -> None:
        names = _distinct_base_card_names(30)
        view = self._make_view(names)
        page0_values = {o.value for o in view.select.options}
        view.page = 1
        view._render()
        page1_values = {o.value for o in view.select.options}
        self.assertTrue(page1_values, "Seite 2 darf nicht leer sein")
        self.assertFalse(page0_values & page1_values, "Seiten dürfen sich nicht überlappen")

    def test_no_pagination_with_few_cards(self) -> None:
        names = _distinct_base_card_names(5)
        view = self._make_view(names)
        self.assertEqual(view._page_count(), 1)
        buttons = [c for c in view.children if isinstance(c, bot.ui.Button)]
        self.assertEqual(buttons, [], "Bei wenigen Karten keine Blätter-Buttons")


class ChallengerCardSelectPaginationTests(unittest.IsolatedAsyncioTestCase):
    """Herausforderer-Auswahl in /kampf (``CardSelectView``) muss ebenfalls blättern können."""

    def _make_view(self, count: int) -> "bot.CardSelectView":
        owned = [(name, 1) for name in _distinct_base_card_names(count)]
        return bot.CardSelectView(111, owned, 1)

    def test_pagination_appears_with_more_than_25_cards(self) -> None:
        view = self._make_view(30)
        self.assertGreater(view._page_count(), 1)
        self.assertLessEqual(len(view.select.options), 25)
        buttons = [c for c in view.children if isinstance(c, bot.ui.Button)]
        next_btn = next((b for b in buttons if "Weiter" in (b.label or "")), None)
        prev_btn = next((b for b in buttons if "Zurück" in (b.label or "")), None)
        self.assertIsNotNone(next_btn, "Weiter-Button muss vorhanden sein")
        self.assertIsNotNone(prev_btn, "Zurück-Button muss vorhanden sein")
        assert next_btn is not None and prev_btn is not None
        self.assertFalse(next_btn.disabled)
        self.assertTrue(prev_btn.disabled)

    def test_second_page_has_no_overlap(self) -> None:
        view = self._make_view(30)
        page0 = {o.value for o in view.select.options}
        view.page = 1
        view._render()
        page1 = {o.value for o in view.select.options}
        self.assertTrue(page1)
        self.assertFalse(page0 & page1)

    def test_no_pagination_with_few_cards(self) -> None:
        view = self._make_view(5)
        self.assertEqual(view._page_count(), 1)
        buttons = [c for c in view.children if isinstance(c, bot.ui.Button)]
        self.assertEqual(buttons, [])


class OpVerwaltungMenuTests(unittest.IsolatedAsyncioTestCase):
    def test_menu_has_solo_and_multi_card_give(self) -> None:
        view = bot.GiveOpActionView(123)
        values = {opt.value for opt in view.select.options}
        self.assertIn("card_give_solo", values)
        self.assertIn("card_give_multi", values)

    def test_menu_keeps_all_other_actions(self) -> None:
        view = bot.GiveOpActionView(123)
        values = {opt.value for opt in view.select.options}
        for expected in (
            "card_remove",
            "group_give",
            "group_remove",
            "add_user",
            "remove_user",
            "add_role",
            "remove_role",
        ):
            self.assertIn(expected, values)


if __name__ == "__main__":
    unittest.main()
