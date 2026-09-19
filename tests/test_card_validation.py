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

    # --- Alternative Designs (bild_2, bild_3) --------------------------------

    def test_design_images_are_optional(self) -> None:
        card = _make_valid_card()
        self.assertEqual(validate_cards([card]), [])
        card["bild_2"] = ""
        card["bild_3"] = "   "
        self.assertEqual(validate_cards([card]), [])

    def test_valid_design_images_pass(self) -> None:
        card = _make_valid_card()
        card["bild_2"] = "https://i.imgur.com/abc.png"
        self.assertEqual(validate_cards([card]), [])
        card["bild_3"] = "http://i.imgur.com/def.png"
        self.assertEqual(validate_cards([card]), [])

    def test_design_image_without_http_fails(self) -> None:
        card = _make_valid_card()
        card["bild_2"] = "i.imgur.com/abc.png"
        self.assertIn("1: bild_2 ist ungueltig", validate_cards([card]))

    def test_design_image_too_long_fails(self) -> None:
        card = _make_valid_card()
        card["bild_2"] = "https://i.imgur.com/" + "a" * 500
        issues = validate_cards([card])
        self.assertTrue(any("bild_2 ist zu lang" in issue for issue in issues))

    def test_design_image_must_be_text(self) -> None:
        card = _make_valid_card()
        card["bild_2"] = 42
        self.assertIn("1: bild_2 ist kein Text", validate_cards([card]))

    def test_design_3_without_design_2_fails(self) -> None:
        card = _make_valid_card()
        card["bild_3"] = "https://i.imgur.com/def.png"
        issues = validate_cards([card])
        self.assertTrue(any("bild_3 ohne bild_2" in issue for issue in issues))
        card["bild_2"] = ""
        issues = validate_cards([card])
        self.assertTrue(any("bild_3 ohne bild_2" in issue for issue in issues))
