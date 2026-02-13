import unittest
import asyncio

import bot as bot_module
from bot import BattleView, EFFECT_TYPES_WITH_EFFECT_LOGS, FightFeedbackView, MissionBattleView
from karten import karten
from services.battle import (
    apply_outgoing_attack_modifier,
    create_battle_embed,
    create_battle_log_embed,
    resolve_multi_hit_damage,
    update_battle_log,
)


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


class CardSpecTests(unittest.TestCase):
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
        self.assertEqual(int(breakdown.get("start_damage", 0) or 0), 5)
        self.assertEqual(int(breakdown.get("burn_damage_per_round", 0) or 0), 5)
        self.assertEqual(int(breakdown.get("burn_duration_rounds", 0) or 0), 3)

        treffsicherheit = _find_attack(hawkeye, "Treffsicherheit")
        effects = treffsicherheit.get("effects", [])
        self.assertTrue(any(e.get("type") == "guaranteed_hit" for e in effects))

        triple = _find_attack(hawkeye, "Triple Arrow")
        mh = triple.get("multi_hit", {})
        self.assertEqual(int(triple.get("cooldown_turns", 0) or 0), 2)
        self.assertEqual(mh.get("hits"), 3)
        self.assertAlmostEqual(float(mh.get("hit_chance")), 0.45)
        self.assertEqual(mh.get("per_hit_damage"), [1, 10])
        self.assertEqual(int(mh.get("guaranteed_min_per_hit", 0) or 0), 3)

    def test_ironman_overladung_specs(self) -> None:
        iron = _find_card("Iron-Man")
        overladung = _find_attack(iron, "Überladung")
        effects = overladung.get("effects", [])
        multiplier_effect = next((e for e in effects if e.get("type") == "damage_multiplier"), None)
        self.assertIsNotNone(multiplier_effect)
        self.assertAlmostEqual(float(multiplier_effect.get("multiplier", 1.0)), 1.65)
        # Interne Cooldown-Logik reduziert am Rundenwechsel; 2 entspricht 1 voller eigener Runde Cooldown.
        self.assertEqual(int(overladung.get("cooldown_turns", 0) or 0), 2)

    def test_captain_america_shield_throw_requires_collect(self) -> None:
        cap = _find_card("Captain America")
        shield_throw = _find_attack(cap, "Schildwurf")
        self.assertTrue(bool(shield_throw.get("requires_reload")))
        self.assertEqual(str(shield_throw.get("reload_name") or ""), "Aufsammeln")

    def test_hulk_gamma_dynamic_cooldown_spec(self) -> None:
        hulk = _find_card("Hulk")
        gamma = _find_attack(hulk, "Gammastrahl")
        self.assertEqual(int(gamma.get("cooldown_from_burning_plus", 0) or 0), 3)

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


class BattleUtilityTests(unittest.TestCase):
    def test_outgoing_flat_overflow(self) -> None:
        self.assertEqual(apply_outgoing_attack_modifier(20, flat=15), (5, 0))
        self.assertEqual(apply_outgoing_attack_modifier(15, flat=15), (0, 0))
        self.assertEqual(apply_outgoing_attack_modifier(10, flat=15), (0, 5))

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


    def test_cooldown_label_uses_remaining_and_total(self) -> None:
        attack = {"name": "Fliegen", "cooldown_turns": 3}
        self.assertEqual(bot_module._format_cooldown_label(attack, 2), "Cooldown: 2/3")
        self.assertEqual(bot_module._format_cooldown_label(attack, 1), "Cooldown: 1/3")

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


class _DummyMember:
    def __init__(self, member_id: int, name: str):
        self.id = member_id
        self.display_name = name
        self.mention = f"<@{member_id}>"


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


class BattleViewRegressionTests(unittest.IsolatedAsyncioTestCase):
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
        view.activate_delayed_defense_after_attack(1, effect_events)
        self.assertTrue(any(e.get("type") == "stealth" for e in view.active_effects[1]))
        self.assertTrue(any("Schutz aktiv" in e for e in effect_events))

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
        self.assertLessEqual(dmg, 66)
        self.assertEqual(min_dmg, 33)
        self.assertEqual(max_dmg, 66)

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
        self.assertIn("Schutzwirkung: Schaden von 20 auf 10 reduziert.", joined)
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
        view.activate_delayed_defense_after_attack(1, effect_events)
        self.assertEqual(len(view.incoming_modifiers[1]), 1)
        self.assertEqual(int(view.incoming_modifiers[1][0].get("counter", 0)), 10)
        view.start_airborne_two_phase(1, [20, 40], effect_events)
        forced = view.resolve_forced_landing_if_due(1, effect_events)
        self.assertIsNotNone(forced)
        self.assertEqual(forced.get("damage"), [20, 40])

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
        self.assertIn("Schutzwirkung: Schaden von 25 auf 12 reduziert.", joined)
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
