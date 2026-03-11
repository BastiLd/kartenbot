import copy
import unittest

from karten import karten
from services.card_validation import normalize_rarity_key, validate_cards


def _make_valid_card() -> dict:
    return {
        "name": "Test Hero",
        "beschreibung": "Testbeschreibung.",
        "bild": "https://example.com/test-hero.png",
        "seltenheit": "Legendary",
        "hp": 140,
        "attacks": [
            {
                "name": "Testschlag",
                "damage": [10, 20],
                "info": "Ein sauberer Testangriff.",
                "effects": [{"type": "damage_boost", "target": "self", "amount": 5, "uses": 1, "chance": 1.0}],
            }
        ],
    }


class CardValidationTests(unittest.TestCase):
    def test_real_card_data_validates_cleanly(self) -> None:
        self.assertEqual(validate_cards(karten), [])

    def test_rarity_aliases_match_existing_bot_logic(self) -> None:
        self.assertEqual(normalize_rarity_key("Gewöhnlich"), "common")
        self.assertEqual(normalize_rarity_key("Legendary"), "legendary")

    def test_duplicate_card_names_fail_case_insensitive(self) -> None:
        cards = [_make_valid_card(), copy.deepcopy(_make_valid_card())]
        cards[1]["name"] = "test hero"
        issues = validate_cards(cards)
        self.assertTrue(any("doppelter Kartenname" in issue for issue in issues))

    def test_duplicate_attack_names_fail_case_insensitive(self) -> None:
        card = _make_valid_card()
        attack_copy = copy.deepcopy(card["attacks"][0])
        attack_copy["name"] = "testschlag"
        card["attacks"].append(attack_copy)
        issues = validate_cards([card])
        self.assertTrue(any("doppelter Attackenname" in issue for issue in issues))

    def test_missing_required_card_field_fails(self) -> None:
        card = _make_valid_card()
        del card["beschreibung"]
        issues = validate_cards([card])
        self.assertIn("1: fehlt beschreibung", issues)

    def test_unknown_rarity_fails(self) -> None:
        card = _make_valid_card()
        card["seltenheit"] = "Mythic"
        issues = validate_cards([card])
        self.assertTrue(any("ungueltige seltenheit" in issue for issue in issues))

    def test_unknown_effect_type_fails(self) -> None:
        card = _make_valid_card()
        card["attacks"][0]["effects"] = [{"type": "teleport"}]
        issues = validate_cards([card])
        self.assertTrue(any("unbekannter effect type" in issue for issue in issues))

    def test_invalid_damage_shape_fails(self) -> None:
        card = _make_valid_card()
        card["attacks"][0]["damage"] = [20, 10]
        issues = validate_cards([card])
        self.assertTrue(any("damage range ist ungueltig" in issue for issue in issues))

    def test_invalid_multi_hit_shape_fails(self) -> None:
        card = _make_valid_card()
        card["attacks"][0]["multi_hit"] = {"hits": 0, "hit_chance": 1.2, "per_hit_damage": [10]}
        issues = validate_cards([card])
        self.assertTrue(any("multi_hit.hits" in issue for issue in issues))
        self.assertTrue(any("multi_hit.hit_chance" in issue for issue in issues))
        self.assertTrue(any("multi_hit.per_hit_damage" in issue for issue in issues))
