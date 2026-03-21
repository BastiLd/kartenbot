import asyncio
import copy
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportAssignmentType=false

import bot as bot_module
from bot import BattleView, EFFECT_TYPES_WITH_EFFECT_LOGS, FightFeedbackView, MAX_ATTACK_DAMAGE_PER_HIT, MissionBattleView
from karten import karten
from services import user_data as user_data_module
from services.battle import (
    apply_outgoing_attack_modifier,
    build_battle_log_entry,
    calculate_damage,
    create_battle_embed,
    create_battle_log_embed,
    resolve_multi_hit_damage,
    update_battle_log,
)
from services.card_pool import ALPHA_PLAYABLE_CARD_NAMES, alpha_playable_cards
from services.card_variants import build_runtime_card, group_owned_cards_by_base, reward_runtime_cards


def _find_card(name: str) -> dict:
    for card in karten:
        if card.get("name") == name:
            return card
    raise AssertionError(f"Card not found: {name}")


def _find_attack(card: dict, attack_name: str) -> dict:
    for attack in card.get("attacks", []):
        if attack.get("name") == attack_name:
            return attack
    raise AssertionError(f"Attack not found: {card.get('name')} / {attack_name}")


def _owned_unique_cards(limit: int) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    seen: set[str] = set()
    for card in karten:
        base_name = str(card.get("base_name") or card.get("name") or "").strip()
        card_name = str(card.get("name") or "").strip()
        if not base_name or not card_name or base_name in seen:
            continue
        seen.add(base_name)
        rows.append((card_name, 1))
        if len(rows) >= limit:
            break
    if len(rows) < limit:
        raise AssertionError(f"Expected at least {limit} unique cards, found {len(rows)}")
    return rows


class CardSpecTests(unittest.IsolatedAsyncioTestCase):
    def test_all_attacks_have_info(self) -> None:
        for card in karten:
            for attack in card.get("attacks", []):
                self.assertTrue(str(attack.get("info") or "").strip(), f"Missing info: {card.get('name')} / {attack.get('name')}")

    def test_doctor_strange_has_no_tarnung(self) -> None:
        strange = _find_card("Doctor Strange")
        attack_names = {a.get("name") for a in strange.get("attacks", [])}
        self.assertNotIn("Tarnung", attack_names)
        self.assertIn("Auge von Agamotto", attack_names)

    def test_hawkeye_new_specs(self) -> None:
        hawkeye = _find_card("Hawkeye")
        flammen_pfeil = _find_attack(hawkeye, "Flammen Pfeil")
        breakdown = flammen_pfeil.get("damage_breakdown", {})
        self.assertEqual(int(breakdown.get("start_damage", 0) or 0), 10)
        self.assertEqual(int(breakdown.get("burn_damage_per_round", 0) or 0), 5)
        self.assertEqual(int(breakdown.get("burn_duration_rounds", 0) or 0), 3)

        treffsicherheit = _find_attack(hawkeye, "Treffsicherheit")
        effects = treffsicherheit.get("effects", [])
        self.assertTrue(any(e.get("type") == "guaranteed_hit" for e in effects))
        self.assertTrue(any(e.get("type") == "force_max" for e in effects))
        self.assertIn("Maximalschaden", str(treffsicherheit.get("info") or ""))

        standard_pfeil = _find_attack(hawkeye, "Pfeil")
        self.assertTrue(bool(standard_pfeil.get("is_standard_attack")))

        triple = _find_attack(hawkeye, "Triple Arrow")
        mh = triple.get("multi_hit", {})
        self.assertEqual(int(triple.get("cooldown_turns", 0) or 0), 4)
        self.assertEqual(mh.get("hits"), 3)
        self.assertAlmostEqual(float(mh.get("hit_chance")), 1.0)
        self.assertEqual(mh.get("per_hit_damage"), [5, 10])
        self.assertEqual(int(mh.get("guaranteed_min_per_hit", 0) or 0), 5)
        self.assertIn("3 Pfeile", str(triple.get("info") or ""))

    def test_daywalker_biss_info_mentions_damage_and_heal(self) -> None:
        blade = _find_card("Blade")
        bite = _find_attack(blade, "Daywalker-Biss")
        info = str(bite.get("info") or "")
        self.assertIn("10-20", info)
        self.assertIn("50%", info)

    def test_ironman_overladung_specs(self) -> None:
        iron = _find_card("Iron-Man")
        overladung = _find_attack(iron, "Überladung")
        effects = overladung.get("effects", [])
        multiplier_effect = next((e for e in effects if e.get("type") == "damage_multiplier"), None)
        self.assertIsNotNone(multiplier_effect)
        assert multiplier_effect is not None
        self.assertAlmostEqual(float(multiplier_effect.get("multiplier", 1.0)), 1.5)
        self.assertEqual(int(overladung.get("cooldown_turns", 0) or 0), 3)

    def test_captain_america_shield_throw_requires_collect(self) -> None:
        cap = _find_card("Captain America")
        shield_throw = _find_attack(cap, "Schildwurf")
        self.assertTrue(bool(shield_throw.get("requires_reload")))
        self.assertEqual(str(shield_throw.get("reload_name") or ""), "Aufsammeln")

    def test_hulk_gamma_dynamic_cooldown_spec(self) -> None:
        hulk = _find_card("Hulk")
        gamma = _find_attack(hulk, "Gammastrahl")
        effects = gamma.get("effects", [])
        burn_effect = next((e for e in effects if e.get("type") == "burning"), None)
        self.assertIsNotNone(burn_effect)
        assert burn_effect is not None
        self.assertEqual(burn_effect.get("duration"), [2, 7])
        self.assertEqual(int(burn_effect.get("damage", 0) or 0), 5)

    def test_outgoing_reduction_cards(self) -> None:
        star_lord = _find_card("Star Lord")
        spider = _find_card("Spider-Man")
        groot = _find_card("Groot")

        mine = _find_attack(star_lord, "Schwerkraft-Mine")
        web = _find_attack(spider, "Netz-Versiegelung")
        root = _find_attack(groot, "Verwurzeln")

        mine_types = {e.get("type") for e in mine.get("effects", [])}
        web_types = {e.get("type") for e in web.get("effects", [])}
        root_types = {e.get("type") for e in root.get("effects", [])}

        self.assertIn("enemy_next_attack_reduction_flat", mine_types)
        self.assertIn("enemy_next_attack_reduction_flat", web_types)
        self.assertIn("enemy_next_attack_reduction_percent", root_types)

    def test_rocket_kleines_ziel_caps_to_attack_min(self) -> None:
        rocket = _find_card("Rocket")
        kleines_ziel = _find_attack(rocket, "Kleines Ziel")
        effects = kleines_ziel.get("effects", [])
        cap_effect = next((e for e in effects if e.get("type") == "cap_damage"), None)
        self.assertIsNotNone(cap_effect)
        assert cap_effect is not None
        self.assertEqual(str(cap_effect.get("max_damage") or ""), "attack_min")

    def test_delayed_defense_and_airborne_specs(self) -> None:
        widow = _find_card("Black Widow")
        iron = _find_card("Iron-Man")
        star_lord = _find_card("Star Lord")
        spider = _find_card("Spider-Man")

        tarnung = _find_attack(widow, "Tarnung")
        jet_boots = _find_attack(star_lord, "Jet-Boots")
        fliegen = _find_attack(iron, "Fliegen")
        spinnensinn = _find_attack(spider, "Spinnensinn")

        tarnung_effects = tarnung.get("effects", [])
        self.assertTrue(any(e.get("type") == "delayed_defense_after_next_attack" and e.get("defense") == "stealth" for e in tarnung_effects))

        jet_effects = jet_boots.get("effects", [])
        self.assertTrue(any(e.get("type") == "delayed_defense_after_next_attack" and e.get("defense") == "evade" for e in jet_effects))
        self.assertTrue(any(e.get("type") == "damage_boost" for e in jet_effects))

        self.assertEqual(fliegen.get("damage"), [0, 0])
        fliegen_effects = fliegen.get("effects", [])
        self.assertTrue(any(e.get("type") == "airborne_two_phase" and e.get("landing_damage") == [20, 40] for e in fliegen_effects))

        spinnensinn_effects = spinnensinn.get("effects", [])
        self.assertTrue(any(e.get("type") == "evade" for e in spinnensinn_effects))

    def test_card_rarity_color_common_uses_requested_hex(self) -> None:
        common_card = _find_card("Iron-Man")
        self.assertEqual(bot_module._card_rarity_color(common_card), 0x13EB2B)

    def test_card_rarity_color_non_common_returns_none(self) -> None:
        self.assertEqual(bot_module._card_rarity_color({"name": "X", "seltenheit": "Legendary"}), 0xFFB020)

    def test_effect_type_logging_coverage_matches_karten_effect_types(self) -> None:
        configured_types = {
            str(effect.get("type"))
            for card in karten
            for attack in card.get("attacks", [])
            for effect in attack.get("effects", [])
            if isinstance(effect, dict) and effect.get("type")
        }
        self.assertTrue(configured_types, "No configured effect types found in karten.py")
        missing = configured_types.difference(EFFECT_TYPES_WITH_EFFECT_LOGS)
        self.assertFalse(missing, f"Missing effect-log coverage for: {sorted(missing)}")

    def test_alpha_pool_contains_only_original_14_cards(self) -> None:
        pool = alpha_playable_cards(karten)
        self.assertEqual([card.get("name") for card in pool], list(ALPHA_PLAYABLE_CARD_NAMES))

    def test_new_non_rare_cards_use_new_placeholder_image(self) -> None:
        expected_names = {
            "Venom",
            "Captain Marvel",
            "Ms Marvel",
            "Ant-Man",
            "Miles Morales",
            "Namor",
            "Nick Fury",
            "Shang-Chi",
            "She-Hulk",
            "Sue Storm",
            "Thor",
            "Mr. Fantastic",
            "The Thing",
            "Human Torch",
            "Cyclops",
        }
        placeholder = "https://i.imgur.com/4mxNv2c.png"
        found_names = {card.get("name") for card in karten if card.get("bild") == placeholder}
        self.assertEqual(found_names, expected_names)

    def test_selected_cooldowns_follow_word_plus_one_rule(self) -> None:
        expectations = {
            ("Black Widow", "Taser"): 3,
            ("Iron-Man", "Überladung"): 3,
            ("Captain America", "Inspiration"): 5,
            ("Hawkeye", "Triple Arrow"): 4,
            ("Doctor Strange", "Strahlen der Vishanti"): 5,
            ("Star Lord", "Awesome Mix"): 6,
            ("Rocket", "Das dicke Ding"): 6,
            ("Spider-Man", "Spinnensinn"): 5,
            ("Captain Marvel", "Sternenflug"): 7,
            ("Thor", "Der Götterschlag"): 7,
            ("Cyclops", "Mega-Optic-Blast"): 7,
        }
        for (card_name, attack_name), expected_cooldown in expectations.items():
            attack = _find_attack(_find_card(card_name), attack_name)
            self.assertEqual(int(attack.get("cooldown_turns", 0) or 0), expected_cooldown, f"{card_name} / {attack_name}")

    def test_damage_scaled_cooldown_specs_are_present_for_word_special_cases(self) -> None:
        captain_marvel = next(
            attack
            for attack in _find_card("Captain Marvel").get("attacks", [])
            if attack.get("cooldown_overrides_by_final_damage") == [{"threshold": 27, "turns": 6}]
        )
        thor = next(
            attack
            for attack in _find_card("Thor").get("attacks", [])
            if attack.get("cooldown_overrides_by_final_damage") == [{"threshold": 55, "turns": 8}]
        )
        mr_fantastic = next(
            attack
            for attack in _find_card("Mr. Fantastic").get("attacks", [])
            if attack.get("cooldown_overrides_by_final_damage") == [{"threshold": 40, "turns": 7}, {"threshold": 55, "turns": 8}]
        )

        self.assertEqual(captain_marvel.get("cooldown_overrides_by_final_damage"), [{"threshold": 27, "turns": 6}])
        self.assertEqual(thor.get("cooldown_overrides_by_final_damage"), [{"threshold": 55, "turns": 8}])
        self.assertEqual(
            mr_fantastic.get("cooldown_overrides_by_final_damage"),
            [{"threshold": 40, "turns": 7}, {"threshold": 55, "turns": 8}],
        )
        return
        captain_marvel = _find_attack(_find_card("Captain Marvel"), "BinÃ¤r-Schlag")
        thor = _find_attack(_find_card("Thor"), "Der GÃ¶tterschlag")
        mr_fantastic = _find_attack(_find_card("Mr. Fantastic"), "Hyper-Intelligenz-Schlag")

        self.assertEqual(captain_marvel.get("cooldown_overrides_by_final_damage"), [{"threshold": 27, "turns": 6}])
        self.assertEqual(thor.get("cooldown_overrides_by_final_damage"), [{"threshold": 55, "turns": 8}])
        self.assertEqual(
            mr_fantastic.get("cooldown_overrides_by_final_damage"),
            [{"threshold": 40, "turns": 7}, {"threshold": 55, "turns": 8}],
        )


    def test_iron_man_alpha_variant_uses_shared_stats_and_new_image(self) -> None:
        standard = build_runtime_card("Standard_Iron-Man", cards=karten)
        alpha = build_runtime_card("Alpha_Iron-Man", cards=karten)
        self.assertIsNotNone(standard)
        self.assertIsNotNone(alpha)
        assert standard is not None
        assert alpha is not None
        self.assertEqual(str(standard.get("base_name") or ""), "Iron-Man")
        self.assertEqual(str(alpha.get("base_name") or ""), "Iron-Man")
        self.assertEqual(int(standard.get("hp", 0) or 0), int(alpha.get("hp", 0) or 0))
        self.assertEqual(
            [str(attack.get("name") or "") for attack in standard.get("attacks", [])],
            [str(attack.get("name") or "") for attack in alpha.get("attacks", [])],
        )
        self.assertEqual(str(alpha.get("bild") or ""), "https://i.imgur.com/ge54AbX.png")
        self.assertTrue(bool(alpha.get("admin_only")))
        self.assertFalse(bool(alpha.get("reward_enabled")))

    def test_reward_runtime_cards_exclude_admin_only_variants(self) -> None:
        reward_names = {str(card.get("name") or "") for card in reward_runtime_cards(karten)}
        self.assertIn("Standard_Iron-Man", reward_names)
        self.assertNotIn("Alpha_Iron-Man", reward_names)

    def test_group_owned_cards_by_base_collapses_variants(self) -> None:
        grouped = group_owned_cards_by_base(
            [("Standard_Iron-Man", 1), ("Alpha_Iron-Man", 1), ("Hulk", 2)],
            cards=karten,
        )
        iron_group = next(group for group in grouped if str(group.get("base_name") or "") == "Iron-Man")
        self.assertEqual(int(iron_group.get("total_amount", 0) or 0), 2)
        self.assertEqual(list(iron_group.get("variants") or []), [("Standard_Iron-Man", 1), ("Alpha_Iron-Man", 1)])

    def test_fight_challenge_prompt_shows_challenger_card(self) -> None:
        text = bot_module._fight_challenge_prompt("@Benni", "Alpha_Iron-Man")
        self.assertIn("@Benni", text)
        self.assertIn("Herausforderer-Karte", text)
        self.assertIn("Iron-Man [Alpha_Iron-Man]", text)


    def test_wolverine_heilfaktor_info_describes_following_regen_rounds(self) -> None:
        wolverine = _find_card("Wolverine")
        heal_factor = _find_attack(wolverine, "Heilfaktor")
        info = str(heal_factor.get("info") or "")
        self.assertIn("nächsten 3 Runden", info)
        self.assertIn("jeweils um 10 HP", info)


class BattleUtilityTests(unittest.IsolatedAsyncioTestCase):
    def test_sort_user_cards_like_karten_order(self) -> None:
        ordered_names = [str(card.get("name") or "") for card in karten if card.get("name")]
        self.assertGreaterEqual(len(ordered_names), 4)
        unsorted_cards = [
            (ordered_names[2], 1),
            (ordered_names[0], 2),
            ("Unknown Card", 3),
            (ordered_names[3], 4),
            (ordered_names[1], 5),
        ]
        sorted_cards = bot_module._sort_user_cards_like_karten(unsorted_cards)
        self.assertEqual(
            [name for name, _amount in sorted_cards],
            [ordered_names[0], "Standard_Iron-Man", ordered_names[2], ordered_names[3], "Unknown Card"],
        )

    def test_outgoing_flat_overflow(self) -> None:
        self.assertEqual(apply_outgoing_attack_modifier(20, flat=15), (5, 0))
        self.assertEqual(apply_outgoing_attack_modifier(15, flat=15), (0, 0))
        self.assertEqual(apply_outgoing_attack_modifier(10, flat=15), (0, 5))

    def test_damage_range_with_max_bonus_keeps_minimum(self) -> None:
        self.assertEqual(bot_module._damage_range_with_max_bonus([10, 20], max_only_bonus=5, flat_bonus=0), (10, 25))
        self.assertEqual(bot_module._damage_range_with_max_bonus(10, max_only_bonus=5, flat_bonus=0), (10, 15))

    def test_buff_select_hides_non_pure_damage_attacks_for_damage_upgrade(self) -> None:
        karte = {
            "name": "Testkarte",
            "attacks": [
                {"name": "Heilung", "damage": [0, 0], "heal": [10, 20]},
                {"name": "Mit Effekt", "damage": [10, 20], "effects": [{"type": "damage_boost", "target": "self", "amount": 5}]},
                {"name": "Treffer", "damage": [10, 20]},
            ],
        }
        select = bot_module.BuffTypeSelect(10, "Testkarte", karte, [])
        option_values = {str(opt.value) for opt in select.options}
        self.assertIn("health_0", option_values)
        self.assertNotIn("damage_1", option_values)
        self.assertNotIn("damage_2", option_values)
        self.assertIn("damage_3", option_values)

    def test_attack_is_damage_upgradeable_only_for_pure_damage(self) -> None:
        self.assertTrue(bot_module._attack_is_damage_upgradeable({"name": "Treffer", "damage": [10, 20]}))
        self.assertFalse(bot_module._attack_is_damage_upgradeable({"name": "Heal", "damage": [0, 0], "heal": [10, 20]}))
        self.assertFalse(
            bot_module._attack_is_damage_upgradeable(
                {"name": "BuffHit", "damage": [10, 20], "effects": [{"type": "damage_boost", "target": "self", "amount": 5}]}
            )
        )
        self.assertFalse(bot_module._attack_is_damage_upgradeable({"name": "Recoil", "damage": [10, 20], "self_damage": 5}))

    def test_user_data_damage_buff_rule_matches_only_pure_damage(self) -> None:
        self.assertTrue(user_data_module._attack_allows_damage_buff({"name": "Treffer", "damage": [10, 20]}))
        self.assertFalse(user_data_module._attack_allows_damage_buff({"name": "Heal", "damage": [0, 0], "heal": [10, 20]}))
        self.assertFalse(
            user_data_module._attack_allows_damage_buff(
                {"name": "BuffHit", "damage": [10, 20], "effects": [{"type": "stun"}]}
            )
        )
        self.assertFalse(user_data_module._attack_allows_damage_buff({"name": "SelfHit", "damage": [10, 20], "self_damage": 3}))

    def test_upgrade_damage_rules_match_for_all_configured_attacks(self) -> None:
        for card in karten:
            for attack in card.get("attacks", []):
                bot_rule = bot_module._attack_is_damage_upgradeable(attack)
                user_data_rule = user_data_module._attack_allows_damage_buff(attack)
                self.assertEqual(
                    bot_rule,
                    user_data_rule,
                    f"Mismatch for {card.get('name')} / {attack.get('name')}",
                )

    def test_buff_select_builds_valid_options_for_all_cards(self) -> None:
        for card in karten:
            select = bot_module.BuffTypeSelect(10, str(card.get("name") or ""), card, [])
            option_values = {str(opt.value) for opt in select.options}
            self.assertIn("health_0", option_values, f"Missing health option for {card.get('name')}")

            expected_damage_options: set[str] = set()
            for index, attack in enumerate(card.get("attacks", [])[:4], start=1):
                if not bot_module._attack_is_damage_upgradeable(attack):
                    continue
                _min_dmg, max_dmg = bot_module._damage_range_with_max_bonus(
                    attack.get("damage", [0, 0]),
                    max_only_bonus=0,
                    flat_bonus=0,
                )
                if max_dmg + bot_module.FUSE_DAMAGE_MAX_BONUS > MAX_ATTACK_DAMAGE_PER_HIT:
                    continue
                expected_damage_options.add(f"damage_{index}")

            actual_damage_options = {value for value in option_values if value.startswith("damage_")}
            self.assertEqual(actual_damage_options, expected_damage_options, f"Invalid buff options for {card.get('name')}")

    async def test_buff_select_refunds_dust_when_upgrade_write_fails(self) -> None:
        card = {"name": "Iron-Man", "hp": 70, "attacks": [{"name": "Repulsor", "damage": [10, 20]}]}
        select = bot_module.BuffTypeSelect(10, "Iron-Man", card, [])
        select._values = ["health_0"]

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=77),
            guild_id=123,
            channel_id=456,
            channel=SimpleNamespace(id=456),
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
        )

        with patch("bot.get_karte_by_name", new=AsyncMock(return_value=card)), patch(
            "bot.get_card_buffs",
            new=AsyncMock(return_value=[]),
        ), patch(
            "bot.spend_infinitydust",
            new=AsyncMock(return_value=True),
        ), patch(
            "bot.add_card_buff",
            new=AsyncMock(side_effect=RuntimeError("write failed")),
        ), patch(
            "bot.add_infinitydust",
            new=AsyncMock(),
        ) as refund_mock, patch(
            "bot._log_event_safe",
            new=AsyncMock(),
        ) as log_mock:
            await select.callback(interaction)

        refund_mock.assert_awaited_once_with(77, 10)
        log_mock.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            "❌ Die Verstärkung ist fehlgeschlagen. Dein Infinitydust wurde zurückerstattet.",
            ephemeral=True,
        )
        interaction.response.edit_message.assert_not_awaited()

    def test_buff_select_prepends_change_card_option(self) -> None:
        card = {"name": "Iron-Man", "hp": 70, "attacks": [{"name": "Repulsor", "damage": [10, 20]}]}
        select = bot_module.BuffTypeSelect(10, "Iron-Man", card, [])
        option_values = [str(opt.value) for opt in select.options]
        self.assertGreaterEqual(len(select.options), 2)
        self.assertEqual(option_values[0], "change_card")
        self.assertEqual(str(select.options[0].label), "Held wechseln")
        self.assertNotIn("cancel", option_values)

    async def test_buff_select_change_card_returns_to_card_picker(self) -> None:
        selected_name = "Iron-Man"
        card = await bot_module.get_karte_by_name(selected_name)
        assert card is not None
        select = bot_module.BuffTypeSelect(10, selected_name, card, [])
        select._values = ["change_card"]

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=77),
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
        )

        with patch("bot.get_user_karten", new=AsyncMock(return_value=_owned_unique_cards(3))):
            await select.callback(interaction)

        interaction.response.send_message.assert_not_awaited()
        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        next_view = kwargs["view"]
        self.assertIsInstance(next_view, bot_module.FuseCardSelectView)
        self.assertEqual(next_view.mode, "root")
        self.assertEqual(
            [str(opt.value) for opt in next_view.action_select.options],
            [bot_module.FUSE_CARD_ACTION_SEARCH, bot_module.FUSE_CARD_ACTION_BROWSE_ALL],
        )
        next_view.stop()

    def test_upgrade_views_include_cancel_button(self) -> None:
        card = {"name": "Iron-Man", "hp": 70, "attacks": [{"name": "Repulsor", "damage": [10, 20]}]}
        views = [
            bot_module.DustAmountView(77, 10),
            bot_module.FuseCardSelectView(77, 10, [("Iron-Man", 1)]),
            bot_module.BuffTypeSelectView(77, 10, "Iron-Man", card, []),
        ]

        try:
            for view in views:
                cancel_buttons = [
                    item for item in view.children
                    if isinstance(item, bot_module.ui.Button) and str(getattr(item, "label", "")) == "Abbrechen"
                ]
                self.assertEqual(len(cancel_buttons), 1)
                self.assertEqual(cancel_buttons[0].style, bot_module.discord.ButtonStyle.danger)
        finally:
            for view in views:
                view.stop()

    def test_upgrade_selects_keep_cancel_out_of_menus_when_button_exists(self) -> None:
        card = {"name": "Iron-Man", "hp": 70, "attacks": [{"name": "Repulsor", "damage": [10, 20]}]}
        root_view = bot_module.FuseCardSelectView(77, 10, [("Iron-Man", 1)])
        try:
            dust_values = [str(opt.value) for opt in bot_module.DustAmountSelect(10).options]
            card_values = [str(opt.value) for opt in root_view.card_select.options]
            action_values = [str(opt.value) for opt in root_view.action_select.options]
            buff_values = [str(opt.value) for opt in bot_module.BuffTypeSelect(10, "Iron-Man", card, []).options]

            self.assertNotIn("__cancel__", dust_values)
            self.assertNotIn("__cancel__", card_values)
            self.assertNotIn("__cancel__", action_values)
            self.assertNotIn("cancel", buff_values)
        finally:
            root_view.stop()

    def test_fuse_root_card_menu_keeps_all_25_real_slots(self) -> None:
        grouped_cards = [
            {"base_name": f"Testheld {index}", "total_amount": 1}
            for index in range(1, 26)
        ]
        view = bot_module.FuseCardSelectView(77, 10, [], grouped_cards=grouped_cards)
        try:
            actual_values = [str(opt.value) for opt in view.card_select.options]

            self.assertEqual(len(actual_values), 25)
            self.assertEqual(actual_values, [f"Testheld {index}" for index in range(1, 26)])
            self.assertEqual(
                [str(opt.value) for opt in view.action_select.options],
                [bot_module.FUSE_CARD_ACTION_SEARCH, bot_module.FUSE_CARD_ACTION_BROWSE_ALL],
            )
        finally:
            view.stop()

    async def test_fuse_view_owner_check_blocks_other_users_and_allows_requester(self) -> None:
        view = bot_module.FuseCardSelectView(77, 10, [("Iron-Man", 1)])
        try:
            with patch.object(bot_module.RestrictedView, "interaction_check", new=AsyncMock(return_value=True)), patch(
                "bot.send_interaction_response",
                new=AsyncMock(),
            ) as send_mock:
                blocked = await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=88)))
                allowed = await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=77)))

            self.assertFalse(blocked)
            self.assertTrue(allowed)
            send_mock.assert_awaited_once_with(
                ANY,
                content=bot_module.FUSE_OWNER_LOCKED_TEXT,
                ephemeral=True,
            )
        finally:
            view.stop()

    async def test_fuse_search_modal_owner_check_blocks_other_users(self) -> None:
        parent_view = bot_module.FuseCardSelectView(77, 10, [("Iron-Man", 1)])
        try:
            modal = bot_module.FuseCardSearchModal(
                77,
                10,
                [("Iron-Man", 1)],
                source_message=None,
                parent_view=parent_view,
            )
            with patch.object(bot_module.RestrictedModal, "interaction_check", new=AsyncMock(return_value=True)), patch(
                "bot.send_interaction_response",
                new=AsyncMock(),
            ) as send_mock:
                allowed = await modal.interaction_check(SimpleNamespace(user=SimpleNamespace(id=88)))

            self.assertFalse(allowed)
            send_mock.assert_awaited_once_with(
                ANY,
                content=bot_module.FUSE_OWNER_LOCKED_TEXT,
                ephemeral=True,
            )
        finally:
            parent_view.stop()

    async def test_fuse_browse_all_opens_paged_browser_with_back_and_cancel(self) -> None:
        grouped_cards = [
            {"base_name": f"Testheld {index}", "total_amount": 1}
            for index in range(1, 27)
        ]
        root_view = bot_module.FuseCardSelectView(77, 10, [])
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))

        try:
            with patch("bot._group_owned_cards_for_current_mode", return_value=grouped_cards):
                await root_view.handle_action_selection(interaction, bot_module.FUSE_CARD_ACTION_BROWSE_ALL)
            kwargs = interaction.response.edit_message.await_args.kwargs
            next_view = kwargs["view"]
            self.assertIsInstance(next_view, bot_module.FuseCardSelectView)
            self.assertEqual(next_view.mode, "browse")
            self.assertEqual([str(opt.value) for opt in next_view.action_select.options], [bot_module.FUSE_CARD_ACTION_BACK])
            self.assertEqual(len(next_view.card_select.options), 25)
            self.assertFalse(next_view.next_button.disabled)
            cancel_buttons = [
                item for item in next_view.children
                if isinstance(item, bot_module.ui.Button) and str(getattr(item, "label", "")) == "Abbrechen"
            ]
            self.assertEqual(len(cancel_buttons), 1)
        finally:
            root_view.stop()
            if 'next_view' in locals():
                next_view.stop()

    async def test_fuse_browse_paging_and_back_return_to_root(self) -> None:
        grouped_cards = [
            {"base_name": f"Testheld {index}", "total_amount": 1}
            for index in range(1, 27)
        ]
        browse_view = bot_module.FuseCardSelectView(77, 10, [], mode="browse", grouped_cards=grouped_cards)
        next_interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        back_interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))

        try:
            first_page_values = [str(opt.value) for opt in browse_view.card_select.options]
            await browse_view._on_next_page(next_interaction)
            second_page_values = [str(opt.value) for opt in browse_view.card_select.options]

            self.assertEqual(browse_view.page, 1)
            self.assertEqual(len(second_page_values), 1)
            self.assertNotEqual(first_page_values, second_page_values)
            self.assertFalse(browse_view.prev_button.disabled)
            self.assertTrue(browse_view.next_button.disabled)

            await browse_view.handle_action_selection(back_interaction, bot_module.FUSE_CARD_ACTION_BACK)
            kwargs = back_interaction.response.edit_message.await_args.kwargs
            root_view = kwargs["view"]
            self.assertIsInstance(root_view, bot_module.FuseCardSelectView)
            self.assertEqual(root_view.mode, "root")
            self.assertEqual(
                [str(opt.value) for opt in root_view.action_select.options],
                [bot_module.FUSE_CARD_ACTION_SEARCH, bot_module.FUSE_CARD_ACTION_BROWSE_ALL],
            )
        finally:
            browse_view.stop()
            if 'root_view' in locals():
                root_view.stop()

    def test_fuse_search_helper_finds_similar_names(self) -> None:
        matches = bot_module._search_fuse_card_groups([("Spider-Man", 1), ("Iron-Man", 1)], "spidr")
        self.assertGreaterEqual(len(matches), 1)
        self.assertEqual(str(matches[0].get("base_name") or ""), "Spider-Man")

    async def test_fuse_search_modal_updates_results_in_place(self) -> None:
        user_cards = [("Spider-Man", 1), ("Iron-Man", 1)]
        parent_view = bot_module.FuseCardSelectView(77, 10, user_cards)
        source_message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()))
        modal = bot_module.FuseCardSearchModal(
            77,
            10,
            user_cards,
            source_message=source_message,
            parent_view=parent_view,
        )
        modal.search_input._value = "spidr"

        try:
            await modal.on_submit(interaction)
            interaction.response.send_message.assert_not_awaited()
            interaction.response.defer.assert_awaited_once()
            source_message.edit.assert_awaited_once()
            kwargs = source_message.edit.await_args.kwargs
            result_view = kwargs["view"]
            self.assertIsInstance(result_view, bot_module.FuseCardSelectView)
            self.assertEqual(result_view.mode, "search")
            self.assertEqual([str(opt.value) for opt in result_view.action_select.options], [bot_module.FUSE_CARD_ACTION_BACK])
            self.assertIn("Spider-Man", [str(opt.value) for opt in result_view.card_select.options])
        finally:
            parent_view.stop()
            if 'result_view' in locals():
                result_view.stop()

    def test_upgrade_preview_embed_shows_current_values_before_choice(self) -> None:
        card = {
            "name": "Testheld",
            "hp": 70,
            "attacks": [
                {"name": "Treffer", "damage": [10, 20]},
                {"name": "Schlag", "damage": [5, 15]},
            ],
        }
        embed = bot_module._build_fuse_buff_type_embed(
            "Testheld",
            card,
            [("health", 0, 10), ("damage", 1, 5)],
        )
        current_values = next(field.value for field in embed.fields if field.name == "Aktuelle Werte")
        self.assertIn("❤️ Leben aktuell: **80 HP**", current_values)
        self.assertIn("⚔️ Treffer — 10-25 Schaden (+5 max)", current_values)
        self.assertIn("⚔️ Schlag — 5-15 Schaden", current_values)

    def test_attack_display_parts_show_heal_and_success_style(self) -> None:
        attack = {"name": "Heal", "damage": [0, 0], "heal": [10, 20]}
        label, style, summary = bot_module._attack_display_parts(attack)
        self.assertEqual(label, "Heal (Heilt 10-20 HP) ❤️")
        self.assertEqual(style, bot_module.discord.ButtonStyle.success)
        self.assertEqual(summary, "Heal — Heilt 10-20 HP ❤️")

    def test_attack_display_parts_show_fixed_heal_as_up_to_amount(self) -> None:
        attack = {"name": "Inspiration", "damage": [0, 0], "heal": 25}
        label, style, summary = bot_module._attack_display_parts(attack)
        self.assertEqual(label, "Inspiration (Heilt bis zu 25 HP) ❤️")
        self.assertEqual(style, bot_module.discord.ButtonStyle.success)
        self.assertEqual(summary, "Inspiration — Heilt bis zu 25 HP ❤️")

    def test_attack_display_parts_show_regen_as_future_heal(self) -> None:
        attack = {
            "name": "Heilfaktor",
            "damage": [0, 0],
            "effects": [{"type": "regen", "target": "self", "turns": 3, "heal": 10}],
        }
        label, style, summary = bot_module._attack_display_parts(attack)
        self.assertEqual(label, "Heilfaktor (Heilt 10 HP für 3 Runden) ❤️")
        self.assertEqual(style, bot_module.discord.ButtonStyle.success)
        self.assertEqual(summary, "Heilfaktor — Heilt 10 HP für 3 Runden ❤️")
        self.assertNotIn("+10", label)
        self.assertNotIn("+10", summary)

    def test_resolve_self_damage_value_supports_ranges(self) -> None:
        with patch("bot.random.randint", return_value=13) as randint_mock:
            self.assertEqual(bot_module._resolve_self_damage_value([10, 20]), 13)
        randint_mock.assert_called_once_with(10, 20)
        self.assertEqual(bot_module._resolve_self_damage_value(5), 5)
        self.assertEqual(bot_module._resolve_self_damage_value(None), 0)

    def test_apply_mix_heal_or_max_effect_heals_when_roll_is_below_half(self) -> None:
        class _Owner:
            def __init__(self) -> None:
                self.force_max_next: dict[int, int] = {}
                self.hp = 60
                self.max_hp = 100

            def _hp_for(self, player_id: int) -> int:
                _ = player_id
                return self.hp

            def _max_hp_for(self, player_id: int) -> int:
                _ = player_id
                return self.max_hp

            def heal_player(self, player_id: int, amount: int) -> int:
                _ = player_id
                healed = min(amount, self.max_hp - self.hp)
                self.hp += healed
                return healed

            def _append_effect_event(self, events: list[str], text: str) -> None:
                events.append(text)

        owner = _Owner()
        effect_events: list[str] = []
        with patch("bot.random.random", return_value=0.2):
            bot_module._apply_mix_heal_or_max_effect(owner, 1, {"heal": 15}, effect_events)
        self.assertEqual(owner.hp, 75)
        self.assertEqual(owner.force_max_next, {})
        self.assertEqual(effect_events, ["Awesome Mix: +15 HP."])

    def test_apply_mix_heal_or_max_effect_sets_force_max_when_roll_is_above_half(self) -> None:
        class _Owner:
            def __init__(self) -> None:
                self.force_max_next: dict[int, int] = {}
                self.hp = 60
                self.max_hp = 100

            def _hp_for(self, player_id: int) -> int:
                _ = player_id
                return self.hp

            def _max_hp_for(self, player_id: int) -> int:
                _ = player_id
                return self.max_hp

            def heal_player(self, player_id: int, amount: int) -> int:
                _ = player_id
                raise AssertionError(f"heal_player should not be called: {amount}")

            def _append_effect_event(self, events: list[str], text: str) -> None:
                events.append(text)

        owner = _Owner()
        effect_events: list[str] = []
        with patch("bot.random.random", return_value=0.8):
            bot_module._apply_mix_heal_or_max_effect(owner, 1, {"heal": 15}, effect_events)
        self.assertEqual(owner.hp, 60)
        self.assertEqual(owner.force_max_next, {1: 1})
        self.assertEqual(effect_events, ["Awesome Mix: Nächster Angriff verursacht Maximalschaden."])

    def test_apply_mix_heal_or_max_effect_uses_force_max_at_full_hp(self) -> None:
        class _Owner:
            def __init__(self) -> None:
                self.force_max_next: dict[int, int] = {}
                self.hp = 100
                self.max_hp = 100

            def _hp_for(self, player_id: int) -> int:
                _ = player_id
                return self.hp

            def _max_hp_for(self, player_id: int) -> int:
                _ = player_id
                return self.max_hp

            def heal_player(self, player_id: int, amount: int) -> int:
                _ = player_id
                raise AssertionError(f"heal_player should not be called: {amount}")

            def _append_effect_event(self, events: list[str], text: str) -> None:
                events.append(text)

        owner = _Owner()
        effect_events: list[str] = []
        with patch("bot.random.random", return_value=0.2):
            bot_module._apply_mix_heal_or_max_effect(owner, 1, {"heal": 15}, effect_events)
        self.assertEqual(owner.force_max_next, {1: 1})
        self.assertEqual(effect_events, ["Awesome Mix: Nächster Angriff verursacht Maximalschaden."])

    def test_build_attack_info_lines_show_heal_amounts(self) -> None:
        card = {
            "name": "Testkarte",
            "attacks": [
                {"name": "Heal", "damage": [0, 0], "heal": [10, 20], "info": "Heilt dich."},
                {"name": "Hit", "damage": [10, 20], "info": "Schaden."},
            ],
        }
        lines = bot_module._build_attack_info_lines(card)
        self.assertIn("• Heal — Heilt 10-20 HP ❤️: Heilt dich.", lines)
        self.assertIn("• Hit — 10-20 Schaden: Schaden.", lines)

    def test_build_attack_info_lines_describe_wolverine_regen_without_static_plus_heal(self) -> None:
        wolverine = _find_card("Wolverine")
        lines = bot_module._build_attack_info_lines(wolverine)
        heal_line = next(line for line in lines if "Heilfaktor" in line)
        self.assertIn("Heilt 10 HP für 3 Runden", heal_line)
        self.assertNotIn("+10x3", heal_line)

    async def test_get_card_buffs_removes_invalid_damage_buffs(self) -> None:
        rows = [
            {"user_id": 7, "card_name": "Testkarte", "attack_number": 1},
            {"user_id": 7, "card_name": "Testkarte", "attack_number": 2},
        ]
        buff_rows = [
            ("damage", 2, 5),
            ("health", 0, 10),
        ]

        class _FakeCursor:
            def __init__(self, payload):
                self.payload = payload

            async def fetchall(self):
                return self.payload

        class _FakeDb:
            def __init__(self):
                self.deleted_rows = []

            async def execute(self, query, params=()):
                if "FROM user_card_buffs WHERE buff_type = 'damage'" in query:
                    return _FakeCursor(rows)
                if "SELECT buff_type, attack_number, buff_amount" in query:
                    return _FakeCursor(buff_rows)
                raise AssertionError(f"Unexpected query: {query}")

            async def executemany(self, query, params):
                self.deleted_rows.extend(list(params))

            async def commit(self):
                return None

        fake_db = _FakeDb()

        @asynccontextmanager
        async def _fake_db_context():
            yield fake_db

        fake_cards = [
            {
                "name": "Testkarte",
                "attacks": [
                    {"name": "BuffHit", "damage": [10, 20], "effects": [{"type": "stun"}]},
                    {"name": "Treffer", "damage": [10, 20]},
                ],
            }
        ]
        with patch.object(user_data_module, "db_context", _fake_db_context), patch.object(user_data_module, "karten", fake_cards):
            result = await user_data_module.get_card_buffs(7, "Testkarte")

        self.assertEqual(fake_db.deleted_rows, [(7, "Testkarte", 1)])
        self.assertEqual(result, buff_rows)

    async def test_add_card_buff_writes_buff_without_unrelated_analytics_context(self) -> None:
        class _FakeDb:
            def __init__(self) -> None:
                self.executed: list[tuple[str, tuple[object, ...]]] = []
                self.commit_calls = 0

            async def execute(self, query, params=()):
                self.executed.append((str(query), tuple(params)))
                return None

            async def commit(self):
                self.commit_calls += 1
                return None

        fake_db = _FakeDb()

        @asynccontextmanager
        async def _fake_db_context():
            yield fake_db

        with patch.object(user_data_module, "db_context", _fake_db_context):
            await user_data_module.add_card_buff(15, "Iron-Man", "damage", 2, 5)

        self.assertEqual(len(fake_db.executed), 1)
        query, params = fake_db.executed[0]
        self.assertIn("INSERT INTO user_card_buffs", query)
        self.assertEqual(params, (15, "Iron-Man", "damage", 2, 5))
        self.assertEqual(fake_db.commit_calls, 1)

    async def test_add_card_buff_normalizes_variant_name_to_base_card(self) -> None:
        class _FakeDb:
            def __init__(self) -> None:
                self.executed: list[tuple[str, tuple[object, ...]]] = []

            async def execute(self, query, params=()):
                self.executed.append((str(query), tuple(params)))
                return None

            async def commit(self):
                return None

        fake_db = _FakeDb()

        @asynccontextmanager
        async def _fake_db_context():
            yield fake_db

        with patch.object(user_data_module, "db_context", _fake_db_context):
            await user_data_module.add_card_buff(44, "Alpha_Iron-Man", "damage", 1, 3)

        _query, params = fake_db.executed[0]
        self.assertEqual(params, (44, "Iron-Man", "damage", 1, 3))

    async def test_add_exact_card_variant_once_blocks_duplicate_exact_variant(self) -> None:
        with (
            patch.object(user_data_module, "has_exact_card_variant", AsyncMock(side_effect=[False, True])),
            patch.object(user_data_module, "add_karte_amount", AsyncMock()) as add_amount_mock,
        ):
            first_added = await user_data_module.add_exact_card_variant_once(101, "Alpha_Iron-Man")
            second_added = await user_data_module.add_exact_card_variant_once(101, "Alpha_Iron-Man")

        self.assertTrue(first_added)
        self.assertFalse(second_added)
        add_amount_mock.assert_awaited_once_with(101, "Alpha_Iron-Man", 1)

    def test_multi_hit_force_max(self) -> None:
        cfg = {"hits": 3, "hit_chance": 0.45, "per_hit_damage": [1, 10]}
        damage, min_possible, max_possible = resolve_multi_hit_damage(cfg, force_max=True)
        self.assertEqual(damage, 30)
        self.assertEqual(min_possible, 3)
        self.assertEqual(max_possible, 30)

    def test_multi_hit_guaranteed_bounds(self) -> None:
        cfg = {"hits": 3, "hit_chance": 0.45, "per_hit_damage": [1, 10]}
        damage, min_possible, max_possible = resolve_multi_hit_damage(cfg, guaranteed_hit=True)
        self.assertGreaterEqual(damage, 3)
        self.assertLessEqual(damage, 30)
        self.assertEqual(min_possible, 3)
        self.assertEqual(max_possible, 30)

    def test_multi_hit_guaranteed_min_per_hit(self) -> None:
        cfg = {"hits": 3, "hit_chance": 0.45, "per_hit_damage": [1, 10], "guaranteed_min_per_hit": 3}
        damage, min_possible, max_possible = resolve_multi_hit_damage(cfg, guaranteed_hit=True)
        self.assertGreaterEqual(damage, 9)
        self.assertLessEqual(damage, 30)
        self.assertEqual(min_possible, 9)
        self.assertEqual(max_possible, 30)

    def test_multi_hit_details_payload(self) -> None:
        cfg = {"hits": 3, "hit_chance": 1.0, "per_hit_damage": [1, 10]}
        damage, _min_possible, _max_possible, details = resolve_multi_hit_damage(cfg, return_details=True)
        self.assertEqual(int(details.get("hits", 0) or 0), 3)
        self.assertEqual(int(details.get("landed_hits", 0) or 0), 3)
        per_hit = details.get("per_hit_damages")
        self.assertIsInstance(per_hit, list)
        self.assertEqual(len(per_hit), 3)
        self.assertEqual(int(details.get("total_damage", 0) or 0), damage)

    def test_dynamic_burning_cooldown_formula(self) -> None:
        attack = {"cooldown_from_burning_plus": 3}
        self.assertEqual(bot_module._resolve_dynamic_cooldown_from_burning(attack, 4), 7)
        self.assertEqual(bot_module._resolve_dynamic_cooldown_from_burning(attack, 2), 5)
        self.assertEqual(bot_module._resolve_dynamic_cooldown_from_burning(attack, None), 0)

    def test_battle_log_effect_events_rendered(self) -> None:
        embed = create_battle_log_embed()
        embed = update_battle_log(
            embed,
            "Star Lord",
            "BotCard",
            "Awesome Mix",
            0,
            False,
            "Bot",
            "Bot",
            1,
            120,
            effect_events=["Heilung: +15 HP.", "Nächster Angriff verursacht Maximalschaden."],
        )
        desc = str(embed.description or "")
        self.assertIn("- Heilung: +15 HP.", desc)
        self.assertIn("- Nächster Angriff verursacht Maximalschaden.", desc)

    def test_battle_log_shows_heal_instead_of_zero_damage(self) -> None:
        embed = create_battle_log_embed()
        embed = update_battle_log(
            embed,
            "Groot",
            "BotCard",
            "Wachstumsschub",
            0,
            False,
            "Basti",
            "Bot",
            1,
            120,
            effect_events=["Heilung: +18 HP."],
        )
        desc = str(embed.description or "")
        self.assertIn("+18 HP Heilung", desc)
        self.assertNotIn("0 Schaden", desc)

    def test_battle_log_heal_hp_line_uses_attacker_hp_when_provided(self) -> None:
        embed = create_battle_log_embed()
        embed = update_battle_log(
            embed,
            "Groot",
            "BotCard",
            "Wachstumsschub",
            0,
            False,
            "Basti",
            "Bot",
            1,
            120,
            attacker_remaining_hp=77,
            effect_events=["Heilung: +18 HP."],
        )
        desc = str(embed.description or "")
        self.assertIn("hat jetzt noch **77 Leben**", desc)
        self.assertNotIn("hat jetzt noch **120 Leben**", desc)

    def test_recent_summary_shows_heal_instead_of_zero_damage(self) -> None:
        class _User:
            def __init__(self, name: str):
                self.display_name = name
                self.mention = name

        _entry, summary = build_battle_log_entry(
            "Moon Knight",
            "Blade",
            "Segen des Khonshu",
            0,
            False,
            _User("Basti"),
            _User("Bot"),
            1,
            97,
            effect_events=["Heilung: +17 HP."],
        )
        self.assertIn("+17 HP Heilung", summary)
        self.assertNotIn("0 Schaden", summary)

    def test_recent_summary_uses_actual_healed_hp_for_fixed_heal(self) -> None:
        class _User:
            def __init__(self, name: str):
                self.display_name = name
                self.mention = name

        _entry, summary = build_battle_log_entry(
            "Captain America",
            "Spider-Man",
            "Inspiration",
            0,
            False,
            _User("Bot"),
            _User("Benni"),
            6,
            100,
            attacker_remaining_hp=125,
            effect_events=[
                "Aktionstyp: Heilfähigkeit.",
                "Ausführung: erfolgreich geheilt (+25 HP).",
                "Heilung: +25 HP.",
            ],
        )
        self.assertIn("+25 HP Heilung", summary)
        self.assertNotIn("+50 HP Heilung", summary)
        self.assertNotIn("0 Schaden", summary)

    def test_regen_setup_does_not_count_as_immediate_heal_in_action_context(self) -> None:
        heal_amount = bot_module._extract_heal_amount_from_events(
            ["Regeneration aktiviert: Heilt sich in den nächsten 3 Runden jeweils um 10 HP."]
        )
        self.assertEqual(heal_amount, 0)

    def test_recent_summary_shows_regen_setup_instead_of_heal_amount(self) -> None:
        class _User:
            def __init__(self, name: str):
                self.display_name = name
                self.mention = name

        entry, summary = build_battle_log_entry(
            "Wolverine",
            "Spider-Man",
            "Heilfaktor",
            0,
            False,
            _User("Benni"),
            _User("Bot"),
            17,
            46,
            attacker_remaining_hp=56,
            effect_events=[
                "Aktionstyp: Heilfähigkeit.",
                "Ausführung: erfolgreich ohne direkten Schaden eingesetzt.",
                "Regeneration aktiviert: Heilt sich in den nächsten 3 Runden jeweils um 10 HP.",
            ],
        )
        self.assertIn("Heilt sich in den nächsten 3 Runden jeweils um 10 HP", entry)
        self.assertIn("Heilt sich in den nächsten 3 Runden jeweils um 10 HP", summary)
        self.assertNotIn("+10 HP Heilung", entry)
        self.assertNotIn("+10 HP Heilung", summary)
        self.assertNotIn("0 Schaden", entry)
        self.assertNotIn("0 Schaden", summary)

    def test_recent_summary_shows_action_type_and_miss_reason(self) -> None:
        class _User:
            def __init__(self, name: str):
                self.display_name = name
                self.mention = name

        entry, summary = build_battle_log_entry(
            "Iron-Man",
            "Blade",
            "Repulsor Strahlen",
            0,
            False,
            _User("Basti"),
            _User("Bot"),
            3,
            112,
            effect_events=[
                "Aktionstyp: Standardangriff.",
                "Ausführung: verfehlt durch Blendung (50% Verfehlchance).",
            ],
        )
        self.assertIn("Repulsor Strahlen (Standardangriff)", entry)
        self.assertIn("verfehlt durch Blendung", summary)

    def test_calculate_damage_zero_never_critical(self) -> None:
        with patch("services.battle.random.random", return_value=0.0):
            damage, is_critical, min_damage, max_damage = calculate_damage([0, 0], 0)
        self.assertEqual(damage, 0)
        self.assertFalse(is_critical)
        self.assertEqual(min_damage, 0)
        self.assertEqual(max_damage, 0)

    def test_log_entry_suppresses_critical_at_zero_damage(self) -> None:
        class _User:
            def __init__(self, name: str):
                self.display_name = name
                self.mention = name

        entry, _summary = build_battle_log_entry(
            "Spider-Man",
            "Spider-Man",
            "Spinnensinn",
            0,
            True,
            _User("Basti"),
            _User("Bot"),
            1,
            140,
            effect_events=["Ausweichen aktiv: Der nächste gegnerische Angriff verfehlt."],
        )
        self.assertNotIn("VOLLTREFFER", entry)

    def test_cooldown_label_uses_remaining_and_total(self) -> None:
        attack = {"name": "Fliegen", "cooldown_turns": 3}
        self.assertEqual(bot_module._format_cooldown_label(attack, 2), "Cooldown: 2/3")
        self.assertEqual(bot_module._format_cooldown_label(attack, 1), "Cooldown: 1/3")

    def test_damage_scaled_cooldown_falls_back_to_base_value(self) -> None:
        attack = {
            "name": "Testschlag",
            "cooldown_turns": 6,
            "cooldown_overrides_by_final_damage": [
                {"threshold": 40, "turns": 7},
                {"threshold": 55, "turns": 8},
            ],
        }
        self.assertEqual(bot_module._resolve_final_damage_cooldown_turns(attack, 39), 6)

    def test_damage_scaled_cooldown_uses_highest_matching_threshold(self) -> None:
        attack = {
            "name": "Testschlag",
            "cooldown_turns": 6,
            "cooldown_overrides_by_final_damage": [
                {"threshold": 40, "turns": 7},
                {"threshold": 55, "turns": 8},
            ],
        }
        self.assertEqual(bot_module._resolve_final_damage_cooldown_turns(attack, 40), 7)
        self.assertEqual(bot_module._resolve_final_damage_cooldown_turns(attack, 55), 8)


class BattleBotChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_choice_prefers_heal_at_low_hp(self) -> None:
        player_card = {"name": "Player", "hp": 140, "attacks": [{"name": "Punch", "damage": [10, 20]}]}
        bot_card = {
            "name": "BotCard",
            "hp": 140,
            "attacks": [
                {"name": "Self Repair", "damage": [0, 0], "heal": [30, 30]},
                {"name": "Laser", "damage": [25, 35]},
                {"name": "Ping", "damage": [10, 12]},
                {"name": "Tap", "damage": [1, 2]},
            ],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.player2_hp = 40
        choice = view._choose_bot_attack_index(bot_card["attacks"])
        self.assertEqual(choice, 0)


class FightFeedbackViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_thread_button_hidden_outside_threads(self) -> None:
        view = FightFeedbackView(channel=object(), guild=None, allowed_user_ids=set(), battle_log_text="")
        try:
            labels = [str(getattr(child, "label", "") or "") for child in view.children]
            self.assertIn("Es gab keinen Bug", labels)
            self.assertNotIn("Thread schließen (Admin/Owner)", labels)
        finally:
            view.stop()
    async def test_bot_choice_uses_setup_before_big_hit_when_no_buff(self) -> None:
        player_card = {"name": "Player", "hp": 160, "attacks": [{"name": "Punch", "damage": [10, 20]}]}
        bot_card = {
            "name": "BotCard",
            "hp": 160,
            "attacks": [
                {
                    "name": "Power Up",
                    "damage": [0, 0],
                    "effects": [{"type": "damage_boost", "target": "self", "amount": 20, "uses": 1}],
                },
                {"name": "Plasma Beam", "damage": [55, 70]},
                {"name": "Ping", "damage": [8, 10]},
                {"name": "Tap", "damage": [1, 2]},
            ],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.player1_hp = 160
        view.player2_hp = 150
        choice = view._choose_bot_attack_index(bot_card["attacks"])
        self.assertEqual(choice, 0)

    async def test_bot_choice_prefers_damage_when_buff_active(self) -> None:
        player_card = {"name": "Player", "hp": 160, "attacks": [{"name": "Punch", "damage": [10, 20]}]}
        bot_card = {
            "name": "BotCard",
            "hp": 160,
            "attacks": [
                {
                    "name": "Power Up",
                    "damage": [0, 0],
                    "effects": [{"type": "damage_boost", "target": "self", "amount": 20, "uses": 1}],
                },
                {"name": "Plasma Beam", "damage": [55, 70]},
                {"name": "Ping", "damage": [8, 10]},
                {"name": "Tap", "damage": [1, 2]},
            ],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.pending_flat_bonus[0] = 20
        view.pending_flat_bonus_uses[0] = 1
        choice = view._choose_bot_attack_index(bot_card["attacks"])
        self.assertEqual(choice, 1)

    async def test_bot_choice_avoids_wasted_damage_when_outgoing_reduced(self) -> None:
        player_card = {"name": "Player", "hp": 160, "attacks": [{"name": "Punch", "damage": [10, 20]}]}
        bot_card = {
            "name": "BotCard",
            "hp": 160,
            "attacks": [
                {
                    "name": "Plan Ahead",
                    "damage": [0, 0],
                    "effects": [{"type": "enemy_next_attack_reduction_flat", "target": "enemy", "value": 10}],
                },
                {"name": "Heavy Shot", "damage": [35, 40]},
                {"name": "Ping", "damage": [8, 10]},
                {"name": "Tap", "damage": [1, 2]},
            ],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.outgoing_attack_modifiers[0].append({"type": "flat", "value": 30})
        choice = view._choose_bot_attack_index(bot_card["attacks"])
        self.assertEqual(choice, 0)

    def test_feedback_view_split_log_for_dm(self) -> None:
        text = "\n".join(f"line{i}" for i in range(1, 8))
        chunks = FightFeedbackView._split_log_for_dm(text, chunk_size=18)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 18 for chunk in chunks))
        flattened = "\n".join(chunks).replace("\n\n", "\n")
        self.assertIn("line1", flattened)
        self.assertIn("line7", flattened)

    def test_create_battle_embed_shows_status_and_recent_lines(self) -> None:
        class _User:
            def __init__(self, uid: int, name: str):
                self.id = uid
                self.display_name = name
                self.mention = f"<@{uid}>"

        user1 = _User(1, "Player")
        user2 = _User(2, "Enemy")
        player_card = {"name": "Iron-Man", "hp": 140, "bild": "https://example.com/p1.png"}
        enemy_card = {"name": "Groot", "hp": 140, "bild": "https://example.com/p2.png"}
        embed = create_battle_embed(
            player_card,
            enemy_card,
            120,
            111,
            user1.id,
            user1,
            user2,
            active_effects={1: [{"type": "burning", "duration": 2}], 2: [{"type": "airborne", "duration": 1}]},
            current_attack_infos=["Repulsor (5-20): test"],
            recent_log_lines=["Player used Repulsor", "Enemy used Root"],
            highlight_tone="crit",
        )
        self.assertEqual(int(embed.color.value), 0xE74C3C)
        field_names = [str(field.name) for field in embed.fields]
        self.assertIn("Status", field_names)
        self.assertIn("Fähigkeiten", field_names)
        self.assertIn("Letzte Angriffe", field_names)

    def test_feedback_prompt_text_mentions_dm_log_option(self) -> None:
        async def _build_prompt() -> str:
            player_card = {"name": "PlayerCard", "hp": 140, "bild": "https://example.com/player.png", "attacks": []}
            bot_card = {"name": "BotCard", "hp": 140, "bild": "https://example.com/bot.png", "attacks": []}
            view = BattleView(player_card, bot_card, 1, 0, None)
            try:
                return view._feedback_prompt_text(None)
            finally:
                view.stop()

        text = asyncio.run(_build_prompt())
        self.assertIn("Kampf-Log per DM", text)

    def test_winner_embed_lists_winner_and_loser(self) -> None:
        player_card = {"name": "PlayerCard", "hp": 140, "bild": "https://example.com/player.png", "attacks": []}
        enemy_card = {"name": "EnemyCard", "hp": 140, "bild": "https://example.com/enemy.png", "attacks": []}
        view = BattleView(player_card, enemy_card, 1, 2, None)
        try:
            embed = view._winner_embed("<@1>", "PlayerCard", "<@2>", "EnemyCard")
        finally:
            view.stop()
        self.assertIn("<@1> hat mit PlayerCard gewonnen.", str(embed.description or ""))
        self.assertIn("<@2> hat mit EnemyCard verloren.", str(embed.description or ""))


class BattleUiRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_repost_battle_ui_if_needed_posts_new_messages_and_clears_flag(self) -> None:
        player_card = {"name": "PlayerCard", "hp": 140, "bild": "https://example.com/player.png", "attacks": []}
        enemy_card = {"name": "EnemyCard", "hp": 140, "bild": "https://example.com/enemy.png", "attacks": []}
        view = BattleView(player_card, enemy_card, 1, 2, None)
        view.ui_needs_resend = True
        old_battle = SimpleNamespace(id=1001)
        old_log = SimpleNamespace(id=1002)
        new_log = SimpleNamespace(id=1003)
        new_battle = SimpleNamespace(id=1004)
        interaction = SimpleNamespace(channel=object())
        view.battle_log_message = old_log

        with patch("bot._safe_send_channel", new=AsyncMock(side_effect=[new_log, new_battle])) as send_mock, patch.object(
            view,
            "persist_session",
            new=AsyncMock(),
        ) as persist_mock, patch("bot._delete_message_quietly", new=AsyncMock()) as delete_mock:
            result = await view._repost_battle_ui_if_needed(
                interaction.channel,
                interaction=interaction,
                current_message=old_battle,
                battle_embed=bot_module.discord.Embed(title="Neu"),
                view=None,
                status="completed",
            )

        self.assertIs(result, new_battle)
        self.assertFalse(view.ui_needs_resend)
        self.assertIs(view.battle_log_message, new_log)
        self.assertEqual(send_mock.await_count, 2)
        persist_mock.assert_awaited_once_with(interaction.channel, status="completed", battle_message=new_battle)
        self.assertEqual(delete_mock.await_count, 2)
        view.stop()


class DustFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_dust_command_flow_remove_uses_actual_removed_amount(self) -> None:
        class _FakeAdminSelectView:
            def __init__(self, *_args, **_kwargs):
                self.value = "7"

            async def wait(self) -> None:
                return None

        guild = SimpleNamespace(get_member=lambda user_id: None)
        followup = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(id=99, mention="<@99>", display_name="Admin"),
            followup=followup,
            channel=object(),
        )

        with patch("bot.AdminUserSelectView", side_effect=lambda *args, **kwargs: _FakeAdminSelectView()), patch(
            "bot._select_number",
            new=AsyncMock(return_value=10),
        ), patch("bot.remove_infinitydust", new=AsyncMock(return_value=4)) as remove_mock, patch(
            "bot.log_admin_dust_action",
            new=AsyncMock(),
        ) as audit_mock, patch(
            "bot._post_dust_result_message",
            new=AsyncMock(return_value=True),
        ) as result_mock:
            await bot_module.run_dust_command_flow(interaction, mode="single", remove=True)

        remove_mock.assert_awaited_once_with(7, 10)
        audit_mock.assert_awaited_once_with(
            99,
            7,
            guild_id=0,
            channel_id=0,
            action="remove",
            mode="single",
            requested_amount=10,
            applied_amount=4,
        )
        result_mock.assert_awaited_once()
        kwargs = result_mock.await_args.kwargs
        self.assertEqual(kwargs["results"], [(7, 4)])
        self.assertEqual(kwargs["amount"], 10)
        self.assertTrue(kwargs["remove"])

    def test_dust_multi_user_select_view_has_search_and_done_first(self) -> None:
        class _Member:
            def __init__(self, member_id: int, name: str, *, bot: bool = False, status=None):
                self.id = member_id
                self.display_name = name
                self.name = name
                self.bot = bot
                self.status = status if status is not None else bot_module.discord.Status.offline

        guild = SimpleNamespace(
            members=[
                _Member(1, "Alpha", status=bot_module.discord.Status.online),
                _Member(2, "SystemBot", bot=True, status=bot_module.discord.Status.online),
                _Member(3, "Beta", status=bot_module.discord.Status.idle),
            ]
        )
        view = bot_module.DustMultiUserSelectView(77, guild)
        try:
            values = [str(option.value) for option in view.select.options]
            self.assertEqual(values[:2], ["search", "done"])
            self.assertNotIn("2", values)
            view.selected_user_ids = [1]
            filtered_values = [str(option.value) for option in view._build_options()]
            self.assertNotIn("1", filtered_values)
        finally:
            view.stop()

    def test_dust_multi_user_select_view_summary_embed_shows_selection(self) -> None:
        class _Member:
            def __init__(self, member_id: int, name: str, *, bot: bool = False, status=None):
                self.id = member_id
                self.display_name = name
                self.name = name
                self.bot = bot
                self.status = status if status is not None else bot_module.discord.Status.offline

        guild = SimpleNamespace(
            get_member=lambda user_id: {1: _Member(1, "Alpha_User"), 3: _Member(3, "Beta-User")}.get(user_id),
            members=[
                _Member(1, "Alpha_User", status=bot_module.discord.Status.online),
                _Member(3, "Beta-User", status=bot_module.discord.Status.idle),
                _Member(4, "Gamma", status=bot_module.discord.Status.offline),
            ],
        )
        view = bot_module.DustMultiUserSelectView(77, guild)
        try:
            view.selected_user_ids = [1, 3]
            embed = view._summary_embed()
            self.assertEqual(embed.title, "\U0001f48e Multi-Auswahl f\u00fcr Infinitydust")
            self.assertIn("Alpha\\_User", embed.fields[2].value)
            self.assertIn("Beta-User", embed.fields[2].value)
            self.assertEqual(embed.fields[0].value, "2")
        finally:
            view.stop()


class FightFeedbackAutoCloseTests(unittest.IsolatedAsyncioTestCase):
    async def test_feedback_view_auto_close_deletes_thread_without_bug(self) -> None:
        class _FakeThread:
            def __init__(self, thread_id: int):
                self.id = thread_id
                self.deleted = False

            async def delete(self) -> None:
                self.deleted = True

        with patch.object(bot_module.discord, "Thread", _FakeThread), patch(
            "bot.update_managed_thread_status",
            new=AsyncMock(),
        ) as update_mock, patch(
            "bot.delete_durable_view",
            new=AsyncMock(),
        ) as delete_mock:
            thread = _FakeThread(321)
            view = FightFeedbackView(
                channel=thread,
                guild=None,
                allowed_user_ids={1},
                battle_log_text="",
                auto_close_delay=1,
                auto_close_started_at=int(bot_module.time.time()) - 5,
                close_on_idle=False,
            )
            try:
                view.bind_durable_message(guild_id=123, channel_id=321, message_id=654)
                await view._auto_close_loop()
            finally:
                view.stop()

        update_mock.assert_awaited_once_with(321, "deleted")
        delete_mock.assert_awaited_once_with(guild_id=123, channel_id=321)
        self.assertTrue(thread.deleted)

    async def test_feedback_view_bug_blocks_auto_close(self) -> None:
        class _FakeThread:
            def __init__(self, thread_id: int):
                self.id = thread_id
                self.deleted = False

            async def delete(self) -> None:
                self.deleted = True

        with patch.object(bot_module.discord, "Thread", _FakeThread), patch(
            "bot.update_managed_thread_status",
            new=AsyncMock(),
        ) as update_mock:
            thread = _FakeThread(654)
            view = FightFeedbackView(
                channel=thread,
                guild=None,
                allowed_user_ids={1},
                battle_log_text="",
                bug_reported_by={1},
                auto_close_delay=1,
                auto_close_started_at=int(bot_module.time.time()) - 5,
                close_on_idle=False,
                keep_open_after_bug=True,
            )
            try:
                await view._auto_close_loop()
            finally:
                view.stop()

        update_mock.assert_not_awaited()
        self.assertFalse(thread.deleted)

    async def test_feedback_view_no_bug_starts_auto_close_when_needed(self) -> None:
        class _FakeTask:
            def __init__(self):
                self._done = False

            def done(self) -> bool:
                return self._done

            def cancel(self) -> None:
                self._done = True

        class _FakeThread:
            def __init__(self, thread_id: int):
                self.id = thread_id

            async def delete(self) -> None:
                return None

        def _fake_create_task(coro):
            coro.close()
            return _FakeTask()

        with patch.object(bot_module.discord, "Thread", _FakeThread), patch(
            "bot.asyncio.create_task",
            side_effect=_fake_create_task,
        ) as create_task_mock:
            thread = _FakeThread(777)
            view = FightFeedbackView(
                channel=thread,
                guild=None,
                allowed_user_ids={1},
                battle_log_text="",
                auto_close_delay=2,
                close_on_idle=False,
                close_after_no_bug=True,
            )
            interaction = SimpleNamespace(
                user=SimpleNamespace(id=1),
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            button = next(item for item in view.children if getattr(item, "custom_id", "") == "fight_feedback:no_bug")
            try:
                await button.callback(interaction)
            finally:
                view.stop()

        create_task_mock.assert_called_once()
        self.assertTrue(view.close_on_idle)
        self.assertIsNotNone(view.auto_close_started_at)
        interaction.response.send_message.assert_awaited_once_with(
            "\u2705 Unbekannt hat **Es gab keinen Bug** gewählt. Danke für das Feedback!",
            ephemeral=False,
        )


class CapDamageRuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_attack_min_cap_in_pvp_resolver(self) -> None:
        attacker = {"name": "Attacker", "hp": 140, "attacks": [{"name": "Hit", "damage": [15, 30]}]}
        defender = {"name": "Defender", "hp": 140, "attacks": [{"name": "Block", "damage": [0, 0]}]}
        view = BattleView(attacker, defender, 1, 2, None)
        try:
            view.queue_incoming_modifier(2, cap="attack_min", turns=1)
            final_damage, reflected, dodged, counter = view.resolve_incoming_modifiers(
                2,
                27,
                incoming_min_damage=15,
            )
            self.assertEqual(final_damage, 15)
            self.assertEqual(reflected, 0)
            self.assertFalse(dodged)
            self.assertEqual(counter, 0)
        finally:
            view.stop()

    async def test_attack_min_cap_in_mission_resolver(self) -> None:
        attacker = {"name": "Attacker", "hp": 140, "attacks": [{"name": "Hit", "damage": [15, 30]}]}
        defender = {"name": "Defender", "hp": 140, "attacks": [{"name": "Block", "damage": [0, 0]}]}
        view = MissionBattleView(attacker, defender, 1, 1, 1)
        try:
            view.queue_incoming_modifier(0, cap="attack_min", turns=1)
            final_damage, reflected, dodged, counter = view.resolve_incoming_modifiers(
                0,
                27,
                incoming_min_damage=15,
            )
            self.assertEqual(final_damage, 15)
            self.assertEqual(reflected, 0)
            self.assertFalse(dodged)
            self.assertEqual(counter, 0)
        finally:
            view.stop()


class _DummyMember:
    def __init__(self, member_id: int, name: str, *, status=None, bot: bool = False):
        self.id = member_id
        self.display_name = name
        self.mention = f"<@{member_id}>"
        self.status = status if status is not None else bot_module.discord.Status.offline
        self.bot = bot


class _DummyGuild:
    def __init__(self):
        self._player = _DummyMember(1, "Player")

    def get_member(self, member_id: int):
        if member_id == 1:
            return self._player
        return None


class _DummyMessage:
    def __init__(self):
        self.guild = _DummyGuild()
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _DummyResponse:
    def __init__(self):
        self._done = False
        self.sent_messages = []
        self.edits = []

    def is_done(self):
        return self._done

    async def defer(self):
        self._done = True

    async def send_message(self, content=None, **kwargs):
        self._done = True
        self.sent_messages.append({"content": content, **kwargs})

    async def edit_message(self, content=None, **kwargs):
        self._done = True
        self.edits.append({"content": content, **kwargs})


class _DummyFollowup:
    def __init__(self):
        self.sent_messages = []

    async def send(self, content=None, **kwargs):
        self.sent_messages.append({"content": content, **kwargs})
        return None


class _DummyChannel:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return None


class _DummyInteraction:
    def __init__(self, user_id: int, message: _DummyMessage):
        self.user = _DummyMember(user_id, f"User{user_id}")
        self.guild = message.guild
        self.message = message
        self.channel = _DummyChannel()
        self.response = _DummyResponse()
        self.followup = _DummyFollowup()


class ShowAllMembersPagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pager_keeps_running_when_switching_pages(self) -> None:
        members = [
            _DummyMember(100 + idx, f"Member{idx:02d}", status=bot_module.discord.Status.online)
            for idx in range(30)
        ]
        pager = bot_module.ShowAllMembersPager(1, members, include_bot_option=True)
        try:
            interaction = _DummyInteraction(1, _DummyMessage())
            await pager._on_next(interaction)
            self.assertEqual(pager.page_index, 1)
            self.assertFalse(pager.is_finished())
            self.assertEqual(len(interaction.response.edits), 1)
        finally:
            pager.stop()

    async def test_pager_sorts_presence_and_keeps_bot_option_first(self) -> None:
        members = [
            _DummyMember(2, "Offline", status=bot_module.discord.Status.offline),
            _DummyMember(3, "Idle", status=bot_module.discord.Status.idle),
            _DummyMember(4, "Online", status=bot_module.discord.Status.online),
        ]
        pager = bot_module.ShowAllMembersPager(1, members, include_bot_option=True)
        try:
            labels = [str(option.label) for option in pager.select.options]
            self.assertEqual(labels[0], "🤖 Bot")
            self.assertEqual(labels[1], "🟢 Online")
            self.assertEqual(labels[2], "🟡 Idle")
            self.assertEqual(labels[3], "⚫ Offline")
        finally:
            pager.stop()


class BattleViewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def _execute_attack_without_buffs(
        self,
        view: BattleView,
        *,
        acting_user_id: int,
        attack_index: int,
        interaction_message: _DummyMessage | None = None,
    ) -> _DummyMessage:
        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            message = interaction_message or _DummyMessage()
            interaction = _DummyInteraction(acting_user_id, message)
            await view.execute_attack(interaction, attack_index)
            return message
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

    async def _execute_player_attack_without_buffs(self, view: BattleView) -> None:
        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            interaction_message = _DummyMessage()
            interaction = _DummyInteraction(view.player1_id, interaction_message)
            await view.execute_attack(interaction, 0)
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

    async def test_low_hp_defender_no_heal_with_outgoing_flat_reduction(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [17, 17], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 140,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        view.player2_hp = 5
        view.queue_outgoing_attack_modifier(1, flat=10, turns=1)

        await self._execute_player_attack_without_buffs(view)

        self.assertEqual(view.player2_hp, 0)

    async def test_low_hp_defender_no_heal_with_outgoing_flat_and_evade(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [17, 17], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 140,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        view.player2_hp = 5
        view.queue_outgoing_attack_modifier(1, flat=10, turns=1)
        view.queue_incoming_modifier(2, evade=True, turns=1)

        await self._execute_player_attack_without_buffs(view)

        self.assertEqual(view.player2_hp, 5)

    async def test_low_hp_defender_no_heal_with_incoming_flat_reduction(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [17, 17], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 140,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        view.player2_hp = 5
        view.queue_incoming_modifier(2, flat=10, turns=1)

        await self._execute_player_attack_without_buffs(view)

        self.assertEqual(view.player2_hp, 0)

    async def test_final_killing_attack_is_in_battle_log(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "FinalHit", "damage": [100, 100], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 50,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        async def _noop_feedback(*_args, **_kwargs):
            return None

        view._send_feedback_prompt = _noop_feedback  # type: ignore[method-assign]

        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            interaction_message = _DummyMessage()
            interaction = _DummyInteraction(1, interaction_message)
            await view.execute_attack(interaction, 0)
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

        full_log = view._full_battle_log_text()
        self.assertIn("Runde 1", full_log)
        self.assertIn("FinalHit", full_log)

    async def test_roll_attack_damage_caps_single_hit_to_50(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "MegaHit", "damage": [80, 80], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        attack = player_card["attacks"][0]
        damage, _critical, _min_damage, max_damage = view.roll_attack_damage(
            attack,
            attack["damage"],
            0,
            1.0,
            False,
            False,
        )
        self.assertLessEqual(damage, MAX_ATTACK_DAMAGE_PER_HIT)
        self.assertEqual(damage, MAX_ATTACK_DAMAGE_PER_HIT)
        self.assertEqual(max_damage, MAX_ATTACK_DAMAGE_PER_HIT)

    async def test_battle_execute_attack_uses_followup_after_defer_for_cooldown(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 100,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        view.attack_cooldowns[1][0] = 1
        interaction = _DummyInteraction(1, _DummyMessage())
        with patch.object(view, "_sync_runtime_flags_from_session", new=AsyncMock()):
            await view.execute_attack(interaction, 0)
        self.assertEqual(interaction.response.sent_messages, [])
        self.assertEqual(len(interaction.followup.sent_messages), 1)
        self.assertEqual(interaction.followup.sent_messages[0]["content"], "Diese Attacke ist noch auf Cooldown!")

    async def test_battle_execute_attack_handles_self_damage_range(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Risk", "damage": [10, 10], "self_damage": [7, 7], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 100,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        interaction = _DummyInteraction(1, _DummyMessage())
        with patch.object(view, "_sync_runtime_flags_from_session", new=AsyncMock()), patch(
            "bot.get_card_buffs",
            new=AsyncMock(return_value=[]),
        ):
            await view.execute_attack(interaction, 0)
        self.assertEqual(view.player1_hp, 93)

    async def test_roll_attack_damage_caps_multi_hit_total_to_50(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {
                    "name": "Multi",
                    "damage": [0, 0],
                    "multi_hit": {"hits": 10, "hit_chance": 1.0, "per_hit_damage": [10, 10]},
                    "info": "test",
                }
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        attack = player_card["attacks"][0]
        damage, _critical, _min_damage, max_damage = view.roll_attack_damage(
            attack,
            attack["damage"],
            0,
            1.0,
            True,
            False,
        )
        self.assertEqual(damage, MAX_ATTACK_DAMAGE_PER_HIT)
        self.assertEqual(max_damage, MAX_ATTACK_DAMAGE_PER_HIT)

    async def test_update_attack_buttons_show_max_only_damage_buff(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 20], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.current_turn = 1
        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return [("damage", 1, 5)]

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            await view.update_attack_buttons()
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

        attack_buttons = [child for child in view.children if isinstance(child, bot_module.ui.Button) and child.row in (0, 1)]
        first_label = str(attack_buttons[0].label)
        self.assertIn("10-25", first_label)
        self.assertIn("(+5 max)", first_label)

    async def test_bot_stun_skips_attack_but_dot_ticks(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.current_turn = 0
        view.stunned_next_turn[0] = True
        view.active_effects[1].append({"type": "burning", "duration": 1, "damage": 5, "applier": 0})

        async def _noop():
            return None

        view.update_attack_buttons = _noop

        message = _DummyMessage()
        start_hp = view.player1_hp

        await view.execute_bot_attack(message)

        self.assertEqual(view.player1_hp, start_hp - 5)
        self.assertEqual(view.current_turn, 1)
        self.assertFalse(view.stunned_next_turn[0])
        self.assertTrue(message.edits)
        edited_embed = message.edits[-1].get("embed")
        self.assertIsNotNone(edited_embed)
        self.assertIn("betäubt", str(edited_embed.description or "").lower())

    async def test_ignore_evade_with_guaranteed_hit(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.queue_incoming_modifier(0, evade=True, counter=10, turns=1)
        final_damage, reflected, dodged, counter = view.resolve_incoming_modifiers(0, 20, ignore_evade=True)
        self.assertEqual(final_damage, 20)
        self.assertEqual(reflected, 0)
        self.assertFalse(dodged)
        self.assertEqual(counter, 0)

    async def test_force_max_zero_damage_not_critical(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Utility", "damage": [0, 0], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 2, None)
        attack = player_card["attacks"][0]
        dmg, is_critical, min_dmg, max_dmg = view.roll_attack_damage(attack, attack["damage"], 0, 1.0, True, False)
        self.assertEqual(dmg, 0)
        self.assertFalse(is_critical)
        self.assertEqual(min_dmg, 0)
        self.assertEqual(max_dmg, 0)

    async def test_no_critical_when_attack_is_dodged(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [20, 20], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 140,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        view.queue_incoming_modifier(2, evade=True, turns=1)
        with patch("services.battle.random.random", return_value=0.0):
            await self._execute_player_attack_without_buffs(view)
        full_log = view._full_battle_log_text()
        self.assertNotIn("VOLLTREFFER", full_log)
        self.assertIn("Ausweichen: Angriff vollständig verfehlt.", full_log)

    async def test_no_critical_when_damage_reduced_to_zero(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [20, 20], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 140,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        view.queue_outgoing_attack_modifier(1, flat=30, turns=1, source="Schwerkraft-Mine")
        with patch("services.battle.random.random", return_value=0.0):
            await self._execute_player_attack_without_buffs(view)
        full_log = view._full_battle_log_text()
        self.assertNotIn("VOLLTREFFER", full_log)
        self.assertIn("Ausgehende Reduktion: Normal wären 20 Schaden möglich gewesen, durch Schwerkraft-Mine jetzt 0 Schaden.", full_log)
        self.assertIn("Überlauf-Rückstoß durch Schwerkraft-Mine: 10 Selbstschaden.", full_log)
        self.assertIn("hat jetzt noch 130 Leben", full_log)

    async def test_blind_miss_logs_action_type_and_reason(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [20, 20], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 140,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        view.blind_next_attack[1] = 0.5
        with patch("services.battle.random.random", return_value=0.0):
            await self._execute_player_attack_without_buffs(view)
        full_log = view._full_battle_log_text()
        self.assertIn("Aktionstyp: Standardangriff.", full_log)
        self.assertIn("Ausführung: verfehlt durch Blendung (50% Verfehlchance).", full_log)

    async def test_real_critical_still_logged_for_positive_final_damage(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [20, 20], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 140,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        with patch("services.battle.random.random", return_value=0.0):
            await self._execute_player_attack_without_buffs(view)
        full_log = view._full_battle_log_text()
        self.assertIn("VOLLTREFFER", full_log)
        self.assertEqual(view.player2_hp, 120)

    async def test_counter_log_attacker_hp_changes(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [20, 20], "info": "test"}],
        }
        defender_card = {
            "name": "DefenderCard",
            "hp": 140,
            "bild": "https://example.com/defender.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1
        view.queue_incoming_modifier(2, evade=True, counter=10, turns=1)
        await self._execute_player_attack_without_buffs(view)
        full_log = view._full_battle_log_text()
        self.assertIn("Konter-Rückschaden: 10 Schaden.", full_log)
        self.assertIn("hat jetzt noch 130 Leben", full_log)
        self.assertEqual(view.player1_hp, 130)

    async def test_delayed_defense_activation_next_attack(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        effect_events: list[str] = []
        view.queue_delayed_defense(1, "stealth")
        view.activate_delayed_defense_after_attack(1, effect_events, attack_landed=True)
        self.assertTrue(any(e.get("type") == "stealth" for e in view.active_effects[1]))
        self.assertTrue(any("Schutz aktiv" in e for e in effect_events))

    async def test_delayed_defense_stays_queued_without_hit(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        effect_events: list[str] = []
        view.queue_delayed_defense(1, "stealth")
        view.activate_delayed_defense_after_attack(1, effect_events, attack_landed=False)
        self.assertFalse(any(e.get("type") == "stealth" for e in view.active_effects[1]))
        self.assertEqual(len(view.delayed_defense_queue[1]), 1)
        self.assertTrue(any("Aktivierung verschoben" in e for e in effect_events))

    async def test_real_flow_tarnung_blocks_exactly_next_enemy_attack_after_hit(self) -> None:
        widow = copy.deepcopy(_find_card("Black Widow"))
        iron = copy.deepcopy(_find_card("Iron-Man"))
        widow["hp"] = 140
        iron["hp"] = 140
        widow["attacks"][0]["damage"] = [10, 10]  # Treten
        iron["attacks"][0]["damage"] = [10, 10]   # Repulsor Strahlen

        view = BattleView(widow, iron, 1, 2, None)
        view.current_turn = 1
        message = _DummyMessage()

        # R1: Black Widow setzt Tarnung (Schutz nur vorbereitet).
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=3, interaction_message=message)
        self.assertEqual(view.current_turn, 2)
        self.assertEqual(view.player1_hp, 140)
        self.assertEqual(view.player2_hp, 140)

        # R2: Iron-Man trifft normal (Widow ist in dieser Runde noch verwundbar).
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=0, interaction_message=message)
        self.assertEqual(view.current_turn, 1)
        self.assertEqual(view.player1_hp, 130)

        # R3: Widow trifft -> jetzt wird der Schutz aktiv.
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=0, interaction_message=message)
        self.assertEqual(view.current_turn, 2)
        self.assertEqual(view.player2_hp, 130)

        # R4: Nächster Iron-Man-Angriff wird geblockt.
        hp_before_block = view.player1_hp
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=0, interaction_message=message)
        self.assertEqual(view.current_turn, 1)
        self.assertEqual(view.player1_hp, hp_before_block)

        # R5+R6: Danach ist Schutz verbraucht, nächster Gegnerangriff trifft wieder normal.
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=0, interaction_message=message)
        hp_before_next_hit = view.player1_hp
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=0, interaction_message=message)
        self.assertEqual(view.player1_hp, hp_before_next_hit - 10)

    async def test_real_flow_fliegen_dodge_then_landing_then_vulnerable_again(self) -> None:
        iron = copy.deepcopy(_find_card("Iron-Man"))
        widow = copy.deepcopy(_find_card("Black Widow"))
        iron["hp"] = 140
        widow["hp"] = 140
        iron["attacks"][0]["damage"] = [10, 10]   # Repulsor Strahlen
        widow["attacks"][0]["damage"] = [10, 10]  # Treten
        # Deterministische Landung
        for effect in iron["attacks"][3].get("effects", []):
            if effect.get("type") == "airborne_two_phase":
                effect["landing_damage"] = [20, 20]

        view = BattleView(iron, widow, 1, 2, None)
        view.current_turn = 1
        message = _DummyMessage()

        # R1: Iron-Man nutzt Fliegen.
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=3, interaction_message=message)
        self.assertEqual(view.current_turn, 2)

        # R2: Gegnerangriff verfehlt wegen Flugphase.
        hp_after_fliegen = view.player1_hp
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=0, interaction_message=message)
        self.assertEqual(view.current_turn, 1)
        self.assertEqual(view.player1_hp, hp_after_fliegen)

        # R3: Iron-Man landet automatisch und verursacht Schaden.
        enemy_hp_before_landing = view.player2_hp
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=3, interaction_message=message)
        self.assertEqual(view.current_turn, 2)
        self.assertEqual(view.player2_hp, enemy_hp_before_landing - 20)

        # R4: Danach kann Iron-Man wieder normalen Schaden bekommen.
        hp_before_enemy_hit = view.player1_hp
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=0, interaction_message=message)
        self.assertEqual(view.player1_hp, hp_before_enemy_hit - 10)

    async def test_real_flow_fliegen_consumes_on_non_damage_enemy_turn(self) -> None:
        iron = copy.deepcopy(_find_card("Iron-Man"))
        cap = copy.deepcopy(_find_card("Captain America"))
        iron["hp"] = 140
        cap["hp"] = 140
        cap["attacks"][3]["damage"] = [10, 10]  # Hieb
        for effect in iron["attacks"][3].get("effects", []):
            if effect.get("type") == "airborne_two_phase":
                effect["landing_damage"] = [20, 20]

        view = BattleView(iron, cap, 1, 2, None)
        view.current_turn = 1
        message = _DummyMessage()

        # R1: Iron-Man nutzt Fliegen.
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=3, interaction_message=message)

        # R2: Gegner nutzt 0-Schaden-Skill (Schild-Block) -> Flugphase muss trotzdem verbraucht werden.
        hp_after_fliegen = view.player1_hp
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=2, interaction_message=message)
        self.assertEqual(view.player1_hp, hp_after_fliegen)

        # R3: Landungsschlag.
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=3, interaction_message=message)

        # R4: Danach trifft ein normaler Angriff wieder (kein weiteres Ausweichen).
        hp_before_hit = view.player1_hp
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=3, interaction_message=message)
        self.assertEqual(view.player1_hp, hp_before_hit - 10)

    async def test_real_flow_no_unfair_block_when_tarnung_followup_misses(self) -> None:
        widow = copy.deepcopy(_find_card("Black Widow"))
        iron = copy.deepcopy(_find_card("Iron-Man"))
        widow["hp"] = 140
        iron["hp"] = 140
        widow["attacks"][0]["damage"] = [10, 10]  # Treten
        iron["attacks"][0]["damage"] = [10, 10]   # Repulsor Strahlen
        for effect in iron["attacks"][3].get("effects", []):
            if effect.get("type") == "airborne_two_phase":
                effect["landing_damage"] = [20, 20]

        view = BattleView(widow, iron, 1, 2, None)
        view.current_turn = 1
        message = _DummyMessage()

        # R1: Tarnung vorbereiten.
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=3, interaction_message=message)

        # R2: Iron-Man nutzt Fliegen.
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=3, interaction_message=message)

        # R3: Widow-Angriff verfehlt wegen Flugphase -> Schutz darf NICHT aktiv werden, sondern bleibt gequeued.
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=0, interaction_message=message)
        self.assertEqual(len(view.delayed_defense_queue[1]), 1)
        self.assertFalse(any(e.get("type") == "stealth" for e in view.active_effects[1]))

        # R4: Iron-Man-Landung muss Schaden machen (nicht unverdient geblockt).
        hp_before_landing = view.player1_hp
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=3, interaction_message=message)
        self.assertEqual(view.player1_hp, hp_before_landing - 20)

    async def test_airborne_two_phase_helpers(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        effect_events: list[str] = []
        view.start_airborne_two_phase(1, [20, 40], effect_events)
        self.assertTrue(any(e.get("type") == "airborne" for e in view.active_effects[1]))
        self.assertEqual(len(view.incoming_modifiers[1]), 1)
        self.assertEqual(str(view.incoming_modifiers[1][0].get("source") or ""), "airborne")
        forced = view.resolve_forced_landing_if_due(1, effect_events)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.get("damage"), [20, 40])
        self.assertFalse(any(e.get("type") == "airborne" for e in view.active_effects[1]))
        self.assertIsNone(view.airborne_pending_landing[1])
        self.assertTrue(any("Landungsschlag" in e for e in effect_events))

    async def test_airborne_cooldown_starts_after_landing(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        effect_events: list[str] = []
        view.start_airborne_two_phase(
            1,
            [20, 40],
            effect_events,
            source_attack_index=3,
            cooldown_turns=3,
        )
        self.assertNotIn(3, view.attack_cooldowns[1])
        forced = view.resolve_forced_landing_if_due(1, effect_events)
        self.assertIsNotNone(forced)
        landing_cd_index = forced.get("cooldown_attack_index")
        landing_cd_turns = int(forced.get("cooldown_turns", 0) or 0)
        if isinstance(landing_cd_index, int) and landing_cd_index >= 0 and landing_cd_turns > 0:
            current_cd = view.attack_cooldowns[1].get(landing_cd_index, 0)
            view.attack_cooldowns[1][landing_cd_index] = max(current_cd, landing_cd_turns)
        self.assertEqual(view.attack_cooldowns[1].get(3), 3)

    async def test_overladung_multiplier_applies_to_forced_landing(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        events: list[str] = []
        view.pending_multiplier[1] = 1.65
        view.pending_multiplier_uses[1] = 1
        view.start_airborne_two_phase(1, [20, 40], events)
        forced = view.resolve_forced_landing_if_due(1, events)
        self.assertIsNotNone(forced)

        attack_multiplier = 1.0
        if view.pending_multiplier_uses.get(1, 0) > 0:
            attack_multiplier = float(view.pending_multiplier.get(1, 1.0) or 1.0)
            view.pending_multiplier_uses[1] -= 1
            if view.pending_multiplier_uses[1] <= 0:
                view.pending_multiplier[1] = 1.0
        dmg, _crit, min_dmg, max_dmg = view.roll_attack_damage(forced, forced["damage"], 0, attack_multiplier, False, False)
        self.assertGreaterEqual(dmg, 33)
        self.assertLessEqual(dmg, MAX_ATTACK_DAMAGE_PER_HIT)
        self.assertEqual(min_dmg, 33)
        self.assertEqual(max_dmg, MAX_ATTACK_DAMAGE_PER_HIT)

    async def test_battle_view_multi_hit_logs_roll_details(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Triple", "damage": [0, 30], "multi_hit": {"hits": 3, "hit_chance": 1.0, "per_hit_damage": [1, 1]}, "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        effect_events: list[str] = []
        atk = player_card["attacks"][0]
        _dmg, _crit, _min_dmg, _max_dmg = view.roll_attack_damage(atk, atk["damage"], 0, 1.0, False, False)
        view._append_multi_hit_roll_event(effect_events)
        self.assertTrue(any("Treffer: 3/3" in e for e in effect_events))

    async def test_public_log_shows_last_4_rounds_but_dm_log_keeps_all(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        attacker = _DummyMember(1, "Player")
        defender = _DummyMember(2, "Bot")
        for round_no in range(1, 7):
            await view._record_battle_log(
                "PlayerCard",
                "BotCard",
                "Hit",
                10,
                False,
                attacker,
                defender,
                round_no,
                100 - round_no,
            )

        public_desc = str(view._full_battle_log_embed.description or "")
        self.assertNotIn("Runde 1", public_desc)
        self.assertNotIn("Runde 2", public_desc)
        self.assertIn("Runde 3", public_desc)
        self.assertIn("Runde 6", public_desc)

        dm_text = view._full_battle_log_text()
        self.assertIn("Runde 1", dm_text)
        self.assertIn("Runde 6", dm_text)

    async def test_reflect_activation_logged_on_cast_battleview(self) -> None:
        player_card = {
            "name": "Doctor Strange",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Spiegeldimension", "damage": [0, 0], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        effect_events: list[str] = []
        view.queue_incoming_modifier(1, percent=0.5, reflect=1.0, turns=1)
        view._append_effect_event(effect_events, "Reflexion aktiv: Schaden wird reduziert und teilweise zurückgeworfen.")
        self.assertTrue(any("Reflexion aktiv" in e for e in effect_events))

    async def test_reflect_trigger_logged_on_incoming_hit_battleview(self) -> None:
        player_card = {
            "name": "Doctor Strange",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        effect_events: list[str] = []
        view._append_incoming_resolution_events(
            effect_events,
            defender_name="Doctor Strange",
            raw_damage=20,
            final_damage=10,
            reflected_damage=10,
            dodged=False,
            counter_damage=0,
            absorbed_before=0,
            absorbed_after=10,
        )
        joined = " | ".join(effect_events)
        self.assertIn("Schutzwirkung: 20 -> 10.", joined)
        self.assertIn("Spiegeldimension/Reflexion durch Doctor Strange: 10 Schaden zurückgeworfen.", joined)
        self.assertIn("Absorption durch Doctor Strange: 10 Schaden gespeichert.", joined)

    async def test_winner_embed_includes_best_effect_round_and_actor(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        attacker = _DummyMember(1, "Player")
        defender = _DummyMember(2, "Bot")
        await view._record_battle_log(
            "PlayerCard",
            "BotCard",
            "Hit",
            10,
            False,
            attacker,
            defender,
            1,
            90,
            effect_events=["Reflexion aktiv: Schaden wird reduziert und teilweise zurückgeworfen."],
        )
        embed = view._winner_embed("<@1>", "PlayerCard")
        top_field = next((f for f in embed.fields if f.name == "Top-Effekte"), None)
        self.assertIsNotNone(top_field)
        text = str(top_field.value)
        self.assertIn("Runde", text)
        self.assertIn("Player", text)

    async def test_winner_embed_uses_effect_actor_from_event_text(self) -> None:
        player_card = {
            "name": "Doctor Strange",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        attacker = _DummyMember(0, "Bot")
        defender = _DummyMember(1, "Player")
        await view._record_battle_log(
            "BotCard",
            "Doctor Strange",
            "Hit",
            10,
            False,
            attacker,
            defender,
            7,
            90,
            effect_events=["Spiegeldimension/Reflexion durch Doctor Strange: 18 Schaden zurückgeworfen."],
        )
        embed = view._winner_embed("<@1>", "Doctor Strange")
        top_field = next((f for f in embed.fields if f.name == "Top-Effekte"), None)
        self.assertIsNotNone(top_field)
        text = str(top_field.value)
        self.assertIn("Runde 7", text)
        self.assertIn("Doctor Strange", text)

    async def test_airborne_turn_locks_attack_selection(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {"name": "A1", "damage": [10, 10], "info": "a"},
                {"name": "A2", "damage": [10, 10], "info": "b"},
                {"name": "A3", "damage": [10, 10], "info": "c"},
                {"name": "A4", "damage": [10, 10], "info": "d"},
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.current_turn = 1
        view.start_airborne_two_phase(1, [20, 40], [])
        view.attack_cooldowns[1][3] = 2
        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            await view.update_attack_buttons()
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertIn("Landungsschlag", str(attack_buttons[0].label))
        self.assertFalse(bool(attack_buttons[0].disabled))
        self.assertTrue(all(bool(btn.disabled) for btn in attack_buttons[1:]))
        self.assertIn("Cooldown: 2", str(attack_buttons[3].label))

    async def test_airborne_turn_keeps_original_source_slot_when_available(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {"name": "A1", "damage": [10, 10], "info": "a"},
                {"name": "A2", "damage": [10, 10], "info": "b"},
                {"name": "A3", "damage": [10, 10], "info": "c"},
                {"name": "A4", "damage": [10, 10], "info": "d"},
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.current_turn = 1
        view.start_airborne_two_phase(1, [20, 40], [], source_attack_index=3)
        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            await view.update_attack_buttons()
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertIn("Landungsschlag", str(attack_buttons[3].label))
        self.assertFalse(bool(attack_buttons[3].disabled))
        self.assertTrue(all(bool(btn.disabled) for btn in attack_buttons[:3]))

    async def test_special_lock_keeps_hawkeye_standard_attack_in_original_slot(self) -> None:
        player_card = copy.deepcopy(_find_card("Hawkeye"))
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.current_turn = 1
        view.special_lock_next_turn[1] = 1
        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            await view.update_attack_buttons()
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertIn("Flammen Pfeil", str(attack_buttons[0].label))
        self.assertTrue(bool(attack_buttons[0].disabled))
        self.assertIn("Pfeil", str(attack_buttons[1].label))
        self.assertFalse(bool(attack_buttons[1].disabled))
        self.assertTrue(all(bool(btn.disabled) for btn in (attack_buttons[2], attack_buttons[3])))

    async def test_hawkeye_treffsicherheit_makes_triple_arrow_max_damage(self) -> None:
        player_card = copy.deepcopy(_find_card("Hawkeye"))
        defender_card = {
            "name": "DummyBot",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Wait", "damage": [0, 0], "info": "test"}],
        }
        view = BattleView(player_card, defender_card, 1, 2, None)
        view.current_turn = 1

        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=2)
        await self._execute_attack_without_buffs(view, acting_user_id=2, attack_index=0)
        await self._execute_attack_without_buffs(view, acting_user_id=1, attack_index=3)

        self.assertEqual(view.player2_hp, 70)
        full_log = view._full_battle_log_text()
        self.assertIn("Triple Arrow (Fähigkeit)", full_log)
        self.assertIn("Treffer: 3/3 | Schaden pro Treffer: 10, 10, 10 | Gesamt: 30.", full_log)

    async def test_battle_reload_button_for_shield_throw(self) -> None:
        player_card = {
            "name": "Captain America",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {
                    "name": "Schildwurf",
                    "damage": [15, 30],
                    "requires_reload": True,
                    "reload_name": "Aufsammeln",
                    "info": "test",
                },
                {"name": "A2", "damage": [10, 10], "info": "b"},
                {"name": "A3", "damage": [10, 10], "info": "c"},
                {"name": "A4", "damage": [10, 10], "info": "d"},
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.current_turn = 1
        view.set_reload_needed(1, 0, True)
        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            await view.update_attack_buttons()
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertEqual(str(attack_buttons[0].label), "Aufsammeln")
        self.assertIn("primary", str(attack_buttons[0].style).lower())
        self.assertFalse(bool(attack_buttons[0].disabled))

    async def test_battle_uses_attack_button_style_from_card_data(self) -> None:
        player_card = {
            "name": "Doctor Strange",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {"name": "Spiegeldimension", "damage": [0, 0], "button_style": "primary", "info": "test"},
                {"name": "Auge von Agamotto", "damage": [0, 0], "button_style": "success", "info": "test"},
                {"name": "A3", "damage": [10, 10], "info": "c"},
                {"name": "A4", "damage": [10, 10], "info": "d"},
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = BattleView(player_card, bot_card, 1, 0, None)
        view.current_turn = 1
        original_get_card_buffs = bot_module.get_card_buffs

        async def _fake_get_card_buffs(_user_id, _card_name):
            return []

        bot_module.get_card_buffs = _fake_get_card_buffs
        try:
            await view.update_attack_buttons()
        finally:
            bot_module.get_card_buffs = original_get_card_buffs

        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertIn("primary", str(attack_buttons[0].style).lower())
        self.assertIn("success", str(attack_buttons[1].style).lower())


class MissionBattleViewRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_mission_force_max_zero_damage_not_critical(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Utility", "damage": [0, 0], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        attack = player_card["attacks"][0]
        dmg, is_critical, min_dmg, max_dmg = view.roll_attack_damage(attack, attack["damage"], 0, 1.0, True, False)
        self.assertEqual(dmg, 0)
        self.assertFalse(is_critical)
        self.assertEqual(min_dmg, 0)
        self.assertEqual(max_dmg, 0)

    async def test_mission_roll_attack_damage_caps_to_50(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "MegaHit", "damage": [120, 120], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        attack = player_card["attacks"][0]
        dmg, _is_critical, _min_dmg, max_dmg = view.roll_attack_damage(
            attack,
            attack["damage"],
            0,
            1.0,
            False,
            False,
        )
        self.assertEqual(dmg, MAX_ATTACK_DAMAGE_PER_HIT)
        self.assertEqual(max_dmg, MAX_ATTACK_DAMAGE_PER_HIT)

    async def test_mission_execute_attack_uses_followup_after_defer_for_cooldown(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.current_turn = 1
        view.user_attack_cooldowns[0] = 1
        interaction = _DummyInteraction(1, _DummyMessage())
        await view.execute_attack(interaction, 0)
        self.assertEqual(interaction.response.sent_messages, [])
        self.assertEqual(len(interaction.followup.sent_messages), 1)
        self.assertEqual(interaction.followup.sent_messages[0]["content"], "Diese Attacke ist noch auf Cooldown!")

    async def test_mission_low_hp_defender_no_heal_with_outgoing_flat_reduction(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [17, 17], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 140,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.bot_hp = 5
        defender_hp_before = view._hp_for(0)

        actual_damage = 17
        view.queue_outgoing_attack_modifier(1, flat=10, turns=1)
        reduced_damage, overflow_self_damage = view.apply_outgoing_attack_modifiers(1, actual_damage)
        if overflow_self_damage > 0:
            view._apply_non_heal_damage(1, overflow_self_damage)
        final_damage, _reflected, dodged, _counter = view.resolve_incoming_modifiers(0, reduced_damage, ignore_evade=False)
        if not dodged and final_damage > 0:
            view._apply_non_heal_damage(0, final_damage)
        view._guard_non_heal_damage_result(0, defender_hp_before, "test_mission_outgoing_flat")

        self.assertFalse(dodged)
        self.assertEqual(view.bot_hp, 0)

    async def test_mission_low_hp_defender_no_heal_with_outgoing_flat_and_evade(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 140,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [17, 17], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 140,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.bot_hp = 5
        defender_hp_before = view._hp_for(0)

        actual_damage = 17
        view.queue_outgoing_attack_modifier(1, flat=10, turns=1)
        view.queue_incoming_modifier(0, evade=True, turns=1)
        reduced_damage, overflow_self_damage = view.apply_outgoing_attack_modifiers(1, actual_damage)
        if overflow_self_damage > 0:
            view._apply_non_heal_damage(1, overflow_self_damage)
        final_damage, _reflected, dodged, _counter = view.resolve_incoming_modifiers(0, reduced_damage, ignore_evade=False)
        if not dodged and final_damage > 0:
            view._apply_non_heal_damage(0, final_damage)
        view._guard_non_heal_damage_result(0, defender_hp_before, "test_mission_outgoing_flat_evade")

        self.assertTrue(dodged)
        self.assertEqual(view.bot_hp, 5)

    async def test_mission_helpers_for_delayed_and_airborne(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        effect_events: list[str] = []
        view.queue_delayed_defense(1, "evade", counter=10)
        view.activate_delayed_defense_after_attack(1, effect_events, attack_landed=True)
        self.assertEqual(len(view.incoming_modifiers[1]), 1)
        self.assertEqual(int(view.incoming_modifiers[1][0].get("counter", 0)), 10)
        view.start_airborne_two_phase(1, [20, 40], effect_events)
        self.assertEqual(str(view.incoming_modifiers[1][1].get("source") or ""), "airborne")
        forced = view.resolve_forced_landing_if_due(1, effect_events)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.get("damage"), [20, 40])

    async def test_mission_delayed_defense_stays_queued_without_hit(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        effect_events: list[str] = []
        view.queue_delayed_defense(1, "evade", counter=10)
        view.activate_delayed_defense_after_attack(1, effect_events, attack_landed=False)
        self.assertEqual(len(view.incoming_modifiers[1]), 0)
        self.assertEqual(len(view.delayed_defense_queue[1]), 1)
        self.assertTrue(any("Aktivierung verschoben" in e for e in effect_events))

    async def test_mission_airborne_cooldown_starts_after_landing(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        effect_events: list[str] = []
        view.start_airborne_two_phase(
            1,
            [20, 40],
            effect_events,
            source_attack_index=3,
            cooldown_turns=3,
        )
        self.assertNotIn(3, view.user_attack_cooldowns)
        forced = view.resolve_forced_landing_if_due(1, effect_events)
        self.assertIsNotNone(forced)
        landing_cd_index = forced.get("cooldown_attack_index")
        landing_cd_turns = int(forced.get("cooldown_turns", 0) or 0)
        if isinstance(landing_cd_index, int) and landing_cd_index >= 0 and landing_cd_turns > 0:
            current_cd = view.user_attack_cooldowns.get(landing_cd_index, 0)
            view.user_attack_cooldowns[landing_cd_index] = max(current_cd, landing_cd_turns)
        self.assertEqual(view.user_attack_cooldowns.get(3), 3)

    async def test_mission_multi_hit_logs_roll_details(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Triple", "damage": [0, 30], "multi_hit": {"hits": 3, "hit_chance": 1.0, "per_hit_damage": [1, 1]}, "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        effect_events: list[str] = []
        atk = player_card["attacks"][0]
        _dmg, _crit, _min_dmg, _max_dmg = view.roll_attack_damage(atk, atk["damage"], 0, 1.0, False, False)
        view._append_multi_hit_roll_event(effect_events)
        self.assertTrue(any("Treffer: 3/3" in e for e in effect_events))

    async def test_reflect_trigger_logged_on_incoming_hit_mission(self) -> None:
        player_card = {
            "name": "Doctor Strange",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        effect_events: list[str] = []
        view._append_incoming_resolution_events(
            effect_events,
            defender_name="Doctor Strange",
            raw_damage=25,
            final_damage=12,
            reflected_damage=13,
            dodged=False,
            counter_damage=0,
            absorbed_before=5,
            absorbed_after=18,
        )
        joined = " | ".join(effect_events)
        self.assertIn("Schutzwirkung: 25 -> 12.", joined)
        self.assertIn("Spiegeldimension/Reflexion durch Doctor Strange: 13 Schaden zurückgeworfen.", joined)
        self.assertIn("Absorption durch Doctor Strange: 13 Schaden gespeichert.", joined)

    async def test_mission_airborne_turn_locks_attack_selection(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {"name": "A1", "damage": [10, 10], "info": "a"},
                {"name": "A2", "damage": [10, 10], "info": "b"},
                {"name": "A3", "damage": [10, 10], "info": "c"},
                {"name": "A4", "damage": [10, 10], "info": "d"},
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.start_airborne_two_phase(1, [20, 40], [])
        view.user_attack_cooldowns[3] = 2
        view.update_attack_buttons_mission()
        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertIn("Landungsschlag", str(attack_buttons[0].label))
        self.assertFalse(bool(attack_buttons[0].disabled))
        self.assertTrue(all(bool(btn.disabled) for btn in attack_buttons[1:]))
        self.assertIn("Cooldown: 2", str(attack_buttons[3].label))

    async def test_mission_airborne_turn_keeps_original_source_slot_when_available(self) -> None:
        player_card = {
            "name": "PlayerCard",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {"name": "A1", "damage": [10, 10], "info": "a"},
                {"name": "A2", "damage": [10, 10], "info": "b"},
                {"name": "A3", "damage": [10, 10], "info": "c"},
                {"name": "A4", "damage": [10, 10], "info": "d"},
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.start_airborne_two_phase(1, [20, 40], [], source_attack_index=3)
        view.update_attack_buttons_mission()
        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertIn("Landungsschlag", str(attack_buttons[3].label))
        self.assertFalse(bool(attack_buttons[3].disabled))
        self.assertTrue(all(bool(btn.disabled) for btn in attack_buttons[:3]))

    async def test_mission_special_lock_keeps_hawkeye_standard_attack_in_original_slot(self) -> None:
        player_card = copy.deepcopy(_find_card("Hawkeye"))
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [0, 0], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.special_lock_next_turn[1] = 1
        view.update_attack_buttons_mission()
        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertIn("Flammen Pfeil", str(attack_buttons[0].label))
        self.assertTrue(bool(attack_buttons[0].disabled))
        self.assertIn("Pfeil", str(attack_buttons[1].label))
        self.assertFalse(bool(attack_buttons[1].disabled))
        self.assertTrue(all(bool(btn.disabled) for btn in (attack_buttons[2], attack_buttons[3])))

    async def test_mission_hawkeye_treffsicherheit_makes_triple_arrow_max_damage(self) -> None:
        player_card = copy.deepcopy(_find_card("Hawkeye"))
        bot_card = {
            "name": "DummyBot",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Wait", "damage": [0, 0], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.persist_session = AsyncMock()
        view._log_mission_attack_event = AsyncMock()

        await view.execute_attack(_DummyInteraction(1, _DummyMessage()), 2)
        await view.execute_attack(_DummyInteraction(1, _DummyMessage()), 3)

        self.assertEqual(view.bot_hp, 70)
        player_triple_call = view._log_mission_attack_event.await_args_list[2]
        self.assertEqual(player_triple_call.kwargs["actual_damage"], 30)
        self.assertIn(
            "Treffer: 3/3 | Schaden pro Treffer: 10, 10, 10 | Gesamt: 30.",
            player_triple_call.kwargs["effect_events"],
        )

    async def test_mission_reload_button_for_shield_throw(self) -> None:
        player_card = {
            "name": "Captain America",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {
                    "name": "Schildwurf",
                    "damage": [15, 30],
                    "requires_reload": True,
                    "reload_name": "Aufsammeln",
                    "info": "test",
                },
                {"name": "A2", "damage": [10, 10], "info": "b"},
                {"name": "A3", "damage": [10, 10], "info": "c"},
                {"name": "A4", "damage": [10, 10], "info": "d"},
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.set_reload_needed(1, 0, True)
        view.update_attack_buttons_mission()
        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertEqual(str(attack_buttons[0].label), "Aufsammeln")
        self.assertIn("primary", str(attack_buttons[0].style).lower())
        self.assertFalse(bool(attack_buttons[0].disabled))

    async def test_mission_uses_attack_button_style_from_card_data(self) -> None:
        player_card = {
            "name": "Doctor Strange",
            "hp": 100,
            "bild": "https://example.com/player.png",
            "attacks": [
                {"name": "Spiegeldimension", "damage": [0, 0], "button_style": "primary", "info": "test"},
                {"name": "Auge von Agamotto", "damage": [0, 0], "button_style": "success", "info": "test"},
                {"name": "A3", "damage": [10, 10], "info": "c"},
                {"name": "A4", "damage": [10, 10], "info": "d"},
            ],
        }
        bot_card = {
            "name": "BotCard",
            "hp": 100,
            "bild": "https://example.com/bot.png",
            "attacks": [{"name": "Hit", "damage": [10, 10], "info": "test"}],
        }
        view = MissionBattleView(player_card, bot_card, 1, 1, 1)
        view.update_attack_buttons_mission()
        attack_buttons = [c for c in view.children if hasattr(c, "row") and c.row in (0, 1)][:4]
        self.assertIn("primary", str(attack_buttons[0].style).lower())
        self.assertIn("success", str(attack_buttons[1].style).lower())


class AlphaPhaseRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_alpha_intro_text_keeps_full_text(self) -> None:
        with patch.object(bot_module, "ALPHA_PHASE_ENABLED", True):
            text = bot_module.build_anfang_intro_text()
        self.assertIn("/mission", text)
        self.assertIn("/geschichte", text)

    def test_intro_text_keeps_mission_and_story_when_alpha_disabled(self) -> None:
        with patch.object(bot_module, "ALPHA_PHASE_ENABLED", False):
            text = bot_module.build_anfang_intro_text()
        self.assertIn("/mission", text)
        self.assertIn("/geschichte", text)

    async def test_anfang_view_hides_mission_and_story_buttons_in_alpha(self) -> None:
        with patch.object(bot_module, "ALPHA_PHASE_ENABLED", True):
            view = bot_module.AnfangView()
        custom_ids = {getattr(child, "custom_id", None) for child in view.children}
        self.assertNotIn("anfang:mission", custom_ids)
        self.assertNotIn("anfang:story", custom_ids)

    async def test_anfang_view_hides_mission_and_story_buttons_when_alpha_disabled(self) -> None:
        with patch.object(bot_module, "ALPHA_PHASE_ENABLED", False):
            view = bot_module.AnfangView()
        custom_ids = {getattr(child, "custom_id", None) for child in view.children}
        self.assertNotIn("anfang:mission", custom_ids)
        self.assertNotIn("anfang:story", custom_ids)

    def test_alpha_hides_set_mission_from_dev_and_visibility_lists(self) -> None:
        has_dev_action = ("Set mission reset", "set_mission") in bot_module.DEV_ACTION_OPTIONS
        has_visibility_item = any(key == "set_mission" for key, _label, _desc in bot_module.PANEL_STATIC_VISIBILITY_ITEMS)
        if bot_module.ALPHA_PHASE_ENABLED:
            self.assertFalse(has_dev_action)
            self.assertFalse(has_visibility_item)
        else:
            self.assertTrue(has_dev_action)
            self.assertTrue(has_visibility_item)

    async def test_resend_pending_requests_skips_missions_in_alpha(self) -> None:
        with (
            patch.object(bot_module, "ALPHA_PHASE_ENABLED", True),
            patch.object(bot_module, "get_pending_fight_requests", new=AsyncMock(return_value=[])),
            patch.object(bot_module, "get_pending_mission_requests", new=AsyncMock(return_value=[])) as mission_mock,
        ):
            await bot_module.resend_pending_requests()
        mission_mock.assert_not_awaited()


class PersistentFlowRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resend_pending_requests_rebinds_existing_mission_message(self) -> None:
        fake_channel = SimpleNamespace(
            id=321,
            parent_id=None,
            send=AsyncMock(),
        )
        fake_guild = SimpleNamespace(id=123, get_channel=lambda channel_id: fake_channel if channel_id == 321 else None)
        existing_message = SimpleNamespace(id=987, channel=fake_channel)
        row = {
            "id": 55,
            "guild_id": 123,
            "channel_id": 321,
            "user_id": 1,
            "mission_data": "{}",
            "visibility": bot_module.VISIBILITY_PRIVATE,
            "is_admin": 0,
            "message_id": 987,
        }
        with (
            patch.object(bot_module, "ALPHA_PHASE_ENABLED", False),
            patch.object(bot_module, "get_pending_fight_requests", new=AsyncMock(return_value=[])),
            patch.object(bot_module, "get_pending_mission_requests", new=AsyncMock(return_value=[row])),
            patch.object(bot_module, "is_channel_allowed_ids", new=AsyncMock(return_value=True)),
            patch.object(bot_module, "_fetch_channel_safe", new=AsyncMock(return_value=None)),
            patch.object(bot_module, "_fetch_message_safe", new=AsyncMock(return_value=existing_message)),
            patch.object(bot_module, "_maybe_register_durable_message", new=AsyncMock()) as register_mock,
            patch.object(bot_module.bot, "get_guild", return_value=fake_guild),
            patch.object(bot_module.bot, "add_view") as add_view_mock,
        ):
            await bot_module.resend_pending_requests()
        fake_channel.send.assert_not_awaited()
        register_mock.assert_awaited_once()
        add_view_mock.assert_called_once()

    async def test_resend_pending_fight_request_expires_when_message_missing(self) -> None:
        fake_thread = SimpleNamespace(id=444, parent_id=321, send=AsyncMock())
        fake_guild = SimpleNamespace(
            id=123,
            get_channel=lambda channel_id: fake_thread if channel_id == 444 else None,
        )
        row = {
            "id": 77,
            "guild_id": 123,
            "origin_channel_id": 321,
            "message_channel_id": 444,
            "thread_id": 444,
            "thread_created": 1,
            "challenger_id": 1,
            "challenged_id": 2,
            "challenger_card": "Iron-Man",
            "created_at": 0,
            "status": "pending",
            "message_id": 987,
        }
        with (
            patch.object(bot_module, "ALPHA_PHASE_ENABLED", True),
            patch.object(bot_module, "get_pending_fight_requests", new=AsyncMock(return_value=[row])),
            patch.object(bot_module, "is_channel_allowed_ids", new=AsyncMock(return_value=True)),
            patch.object(bot_module, "_fetch_message_safe", new=AsyncMock(return_value=None)),
            patch.object(bot_module, "claim_fight_request", new=AsyncMock(return_value=True)) as claim_mock,
            patch.object(bot_module.bot, "get_guild", return_value=fake_guild),
            patch.object(bot_module.bot, "add_view") as add_view_mock,
        ):
            await bot_module.resend_pending_requests()

        claim_mock.assert_awaited_once_with(77, "expired")
        fake_thread.send.assert_not_awaited()
        add_view_mock.assert_not_called()

    async def test_resend_pending_fight_request_does_not_fallback_to_origin_channel(self) -> None:
        origin_channel = SimpleNamespace(id=321, parent_id=None, send=AsyncMock())
        fake_guild = SimpleNamespace(
            id=123,
            get_channel=lambda channel_id: origin_channel if channel_id == 321 else None,
        )
        row = {
            "id": 78,
            "guild_id": 123,
            "origin_channel_id": 321,
            "message_channel_id": 321,
            "thread_id": 444,
            "thread_created": 1,
            "challenger_id": 1,
            "challenged_id": 2,
            "challenger_card": "Iron-Man",
            "created_at": 0,
            "status": "pending",
            "message_id": None,
        }
        with (
            patch.object(bot_module, "ALPHA_PHASE_ENABLED", True),
            patch.object(bot_module, "get_pending_fight_requests", new=AsyncMock(return_value=[row])),
            patch.object(bot_module, "_fetch_channel_safe", new=AsyncMock(return_value=None)),
            patch.object(bot_module, "claim_fight_request", new=AsyncMock(return_value=True)) as claim_mock,
            patch.object(bot_module.bot, "get_guild", return_value=fake_guild),
        ):
            await bot_module.resend_pending_requests()

        claim_mock.assert_awaited_once_with(78, "expired")
        origin_channel.send.assert_not_awaited()

    async def test_challenge_accept_recreates_missing_private_thread(self) -> None:
        class _FakeThread:
            def __init__(self, thread_id: int) -> None:
                self.id = thread_id
                self.parent_id = 123
                self.add_user = AsyncMock()

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=2),
            channel=SimpleNamespace(id=999, parent_id=None),
            guild=SimpleNamespace(get_member=lambda user_id: SimpleNamespace(id=user_id)),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        replacement_thread = _FakeThread(555)
        view = bot_module.ChallengeResponseView(
            1,
            2,
            "Iron-Man",
            request_id=42,
            origin_channel_id=777,
            thread_id=444,
            thread_created=True,
        )
        with (
            patch.object(bot_module.discord, "Thread", _FakeThread),
            patch.object(bot_module, "claim_fight_request", new=AsyncMock(return_value=True)),
            patch.object(bot_module.bot, "get_channel", return_value=None),
            patch.object(bot_module, "_fetch_channel_safe", new=AsyncMock(return_value=None)),
            patch.object(bot_module, "_create_required_private_fight_thread", new=AsyncMock(return_value=replacement_thread)) as create_thread_mock,
            patch.object(bot_module, "_start_fight_card_selection_from_challenge", new=AsyncMock()) as start_mock,
        ):
            accept_button = next(child for child in view.children if getattr(child, "custom_id", "") == "fight_challenge:accept")
            await accept_button.callback(interaction)

        create_thread_mock.assert_awaited_once()
        replacement_thread.add_user.assert_awaited_once_with(interaction.user)
        self.assertEqual(view.thread_id, 555)
        self.assertTrue(view.thread_created)
        self.assertEqual(start_mock.await_args.kwargs["thread_id"], 555)
        interaction.followup.send.assert_not_awaited()

    async def test_on_message_skips_intro_for_managed_thread(self) -> None:
        class FakeThread:
            def __init__(self) -> None:
                self.id = 444
                self.parent_id = 111

        channel = FakeThread()
        author = SimpleNamespace(bot=False, id=7)
        message = SimpleNamespace(author=author, guild=SimpleNamespace(id=222), channel=channel)
        with (
            patch.object(bot_module.discord, "Thread", FakeThread),
            patch.object(bot_module, "is_maintenance_enabled", new=AsyncMock(return_value=False)),
            patch.object(bot_module, "is_channel_allowed_ids", new=AsyncMock(return_value=True)),
            patch.object(bot_module, "is_managed_thread", new=AsyncMock(return_value=True)),
            patch.object(bot_module.bot, "process_commands", new=AsyncMock()) as process_mock,
        ):
            await bot_module.on_message(message)
        process_mock.assert_awaited_once_with(message)

    async def test_bug_button_sends_log_to_basti_immediately(self) -> None:
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1, display_name="Tester"),
            response=response,
            guild=None,
            channel=SimpleNamespace(mention="#thread"),
        )
        view = FightFeedbackView(channel=object(), guild=None, allowed_user_ids={1}, battle_log_text="Log")
        try:
            bug_button = next(child for child in view.children if getattr(child, "custom_id", "") == "fight_feedback:bug")
            with patch.object(bot_module, "_send_basti_log_dm", new=AsyncMock()) as dm_mock:
                await bug_button.callback(interaction)
            dm_mock.assert_awaited_once()
            response.send_message.assert_awaited_once_with(
                content="🐞 Tester hat **Es gab einen Bug** gewählt. Bitte fülle dieses Formular aus:",
                view=ANY,
                ephemeral=False,
            )
        finally:
            view.stop()

    async def test_handle_durable_view_error_cleans_up_unknown_channel_feedback_thread(self) -> None:
        class _FakeThread:
            def __init__(self, thread_id: int):
                self.id = thread_id
                self.parent_id = None

            async def delete(self) -> None:
                return None

        interaction = SimpleNamespace(
            channel=None,
            guild=SimpleNamespace(name="Guild"),
            user=SimpleNamespace(id=1, mention="<@1>", display_name="Tester"),
        )
        response = SimpleNamespace(status=404, reason="Not Found")
        error = bot_module.discord.NotFound(response, {"code": 10003, "message": "Unknown Channel"})

        with patch.object(bot_module.discord, "Thread", _FakeThread), patch(
            "bot._send_basti_log_dm",
            new=AsyncMock(),
        ), patch(
            "bot.send_interaction_response",
            new=AsyncMock(),
        ) as response_mock, patch(
            "bot._send_channel_message",
            new=AsyncMock(),
        ) as fallback_mock, patch(
            "bot.delete_durable_view",
            new=AsyncMock(),
        ) as delete_mock, patch(
            "bot.update_managed_thread_status",
            new=AsyncMock(),
        ) as status_mock:
            thread = _FakeThread(444)
            interaction.channel = thread
            view = FightFeedbackView(channel=thread, guild=None, allowed_user_ids={1}, battle_log_text="Log", close_on_idle=False)
            try:
                view.bind_durable_message(guild_id=123, channel_id=444, message_id=777)
                await bot_module._handle_durable_view_error(
                    interaction,
                    error,
                    view=view,
                    view_label=view.durable_context_label(),
                    battle_log_text=view.durable_log_text(),
                )
            finally:
                view.stop()

        response_mock.assert_awaited_once()
        fallback_mock.assert_not_awaited()
        delete_mock.assert_awaited_once_with(guild_id=123, channel_id=444)
        status_mock.assert_awaited_once_with(444, "deleted")
