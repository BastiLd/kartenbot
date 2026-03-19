import unittest

from services import battle_state


class BattleStateTests(unittest.TestCase):
    def test_summarize_card_buffs_splits_health_and_damage(self) -> None:
        total_health, damage_map = battle_state.summarize_card_buffs(
            [
                ("health", 0, 15),
                ("damage", 1, 4),
                ("damage", 1, 3),
                ("damage", 3, 9),
            ]
        )

        self.assertEqual(total_health, 15)
        self.assertEqual(damage_map, {1: 7, 3: 9})

    def test_build_runtime_maps_initializes_all_players(self) -> None:
        runtime = battle_state.build_battle_runtime_maps((7, 0))

        self.assertEqual(runtime["cooldowns_by_player"], {7: {}, 0: {}})
        self.assertEqual(runtime["active_effects"], {7: [], 0: []})
        self.assertEqual(runtime["pending_multiplier"], {7: 1.0, 0: 1.0})

    def test_status_icons_reflect_active_effects(self) -> None:
        active_effects: battle_state.BattleEffectsMap = {
            1: [
                {"type": "burning"},
                {"type": "confusion"},
                {"type": "stealth"},
                {"type": "airborne"},
            ]
        }

        self.assertEqual(battle_state.status_icons(active_effects, 1), " 🔥 🌀 🥷 ✈️")

    def test_cooldown_helpers_and_strong_attack(self) -> None:
        cooldowns: dict[int, int] = {}
        battle_state.start_attack_cooldown(cooldowns, 2, turns=2)

        self.assertTrue(battle_state.is_attack_on_cooldown(cooldowns, 2))
        self.assertTrue(battle_state.is_strong_attack([91, 100], 0))

        battle_state.reduce_cooldowns(cooldowns)
        self.assertEqual(cooldowns[2], 1)
        battle_state.reduce_cooldowns(cooldowns)
        self.assertNotIn(2, cooldowns)

    def test_resolve_incoming_modifier_applies_reflect_and_store(self) -> None:
        incoming_modifiers: battle_state.BattleEffectsMap = {1: []}
        absorbed_damage: battle_state.BattleIntMap = {1: 0}
        battle_state.queue_incoming_modifier(
            incoming_modifiers,
            1,
            percent=0.5,
            reflect=0.5,
            store_ratio=1.0,
            turns=1,
        )

        damage, reflected, dodged, counter, _modifier = battle_state.resolve_incoming_modifiers(
            incoming_modifiers,
            absorbed_damage,
            1,
            40,
        )

        self.assertEqual((damage, reflected, dodged, counter), (20, 10, False, 0))
        self.assertEqual(absorbed_damage[1], 20)

    def test_airborne_two_phase_prepares_and_resolves_landing(self) -> None:
        active_effects: battle_state.BattleEffectsMap = {5: [], 0: []}
        incoming_modifiers: battle_state.BattleEffectsMap = {5: [], 0: []}
        airborne_pending_landing: battle_state.BattlePendingLandingMap = {5: None, 0: None}
        events: list[str] = []

        battle_state.start_airborne_two_phase(
            active_effects,
            airborne_pending_landing,
            incoming_modifiers,
            5,
            [30, 50],
            events,
            source_attack_index=2,
            cooldown_turns=3,
        )

        self.assertTrue(battle_state.has_effect(active_effects, 5, "airborne"))
        self.assertIsNotNone(airborne_pending_landing[5])
        self.assertEqual(incoming_modifiers[5][0]["source"], "airborne")

        landing = battle_state.resolve_forced_landing_if_due(
            active_effects,
            airborne_pending_landing,
            5,
            events,
        )

        assert landing is not None
        self.assertEqual(landing["damage"], [30, 50])
        self.assertEqual(landing["cooldown_attack_index"], 2)
        self.assertFalse(battle_state.has_effect(active_effects, 5, "airborne"))
        self.assertIsNone(airborne_pending_landing[5])


if __name__ == "__main__":
    unittest.main()
