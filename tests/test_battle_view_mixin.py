"""Struktur-Smoke-Test für die BattleMechanicsMixin-Auslagerung (Audit D4).

Die 34 zuvor in BattleView UND MissionBattleView byte-identisch duplizierten
Kampf-Mechanik-Methoden wurden in einen gemeinsamen Mixin verschoben. Dieser Test
sichert, dass beide Views weiterhin vom Mixin erben und alle Methoden tatsächlich
aus dem Mixin stammen (nicht versehentlich neu/abweichend definiert oder verloren).
"""

import unittest

import bot

SHARED_METHODS = [
    "_append_effect_event", "_append_multi_hit_roll_event", "_apply_non_heal_damage",
    "_apply_non_heal_damage_with_event", "_card_name_for", "_clear_airborne",
    "_consume_airborne_evade_marker", "_find_effect", "_grant_airborne",
    "_guard_non_heal_damage_result", "_hp_for", "_max_hp_for",
    "_resolve_incoming_modifiers_with_details", "_set_hp_for",
    "activate_delayed_defense_after_attack", "apply_regen_tick", "consume_confusion_if_any",
    "consume_guaranteed_hit", "consume_stealth", "durable_payload", "grant_stealth",
    "has_airborne", "has_stealth", "heal_player", "is_reload_needed", "queue_delayed_defense",
    "queue_incoming_modifier", "queue_outgoing_attack_modifier", "resolve_forced_landing_if_due",
    "resolve_incoming_modifiers", "roll_attack_damage", "set_confusion", "set_reload_needed",
    "start_airborne_two_phase",
]


class BattleMechanicsMixinTests(unittest.TestCase):
    def test_both_views_inherit_mixin(self) -> None:
        self.assertTrue(issubclass(bot.BattleView, bot.BattleMechanicsMixin))
        self.assertTrue(issubclass(bot.MissionBattleView, bot.BattleMechanicsMixin))

    def test_shared_methods_come_from_mixin(self) -> None:
        for name in SHARED_METHODS:
            mixin_fn = getattr(bot.BattleMechanicsMixin, name)
            bv_fn = getattr(bot.BattleView, name)
            mv_fn = getattr(bot.MissionBattleView, name)
            self.assertIs(bv_fn, mixin_fn, f"{name}: BattleView nutzt nicht die Mixin-Methode")
            self.assertIs(mv_fn, mixin_fn, f"{name}: MissionBattleView nutzt nicht die Mixin-Methode")

    def test_attack_callbacks_stay_separate(self) -> None:
        # attack1-4 haben unterschiedliche custom_ids und dürfen NICHT geteilt werden.
        for name in ("attack1", "attack2", "attack3", "attack4"):
            self.assertIsNot(getattr(bot.BattleView, name), getattr(bot.MissionBattleView, name))


if __name__ == "__main__":
    unittest.main()
