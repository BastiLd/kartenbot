"""Regressionstests für die von Nutzern gemeldeten Kampf-Bugs (Juni 2026).

Abgedeckt:
  * Reaktive Evolution (Ultron)          -> flach -2 ab dem ERSTEN Treffer
  * Supernova-Ladung (Human Torch)       -> burn_multiplier verfällt nicht mehr am Zugende
  * System-Optimierung (Ultron)          -> bedingte Selbstheilung unter 50% HP
  * Flammenwand (Human Torch)            -> Standard abfangen (+Schaden) / sonst Caster heilen
  * Karten-/Daten-Konsistenz für die betroffenen Attacken
"""

import unittest
from collections import defaultdict

import bot
import services.effect_handler as effect_handler
from karten import karten


def _attack(card_name, attack_name):
    card = next(c for c in karten if c["name"] == card_name)
    return next(a for a in card["attacks"] if a["name"] == attack_name)


class FakeBattleView:
    """Minimaler Ersatz für BattleView/MissionBattleView zum Testen der Helfer."""

    def __init__(self, hp=None, max_hp=None):
        self.active_effects = defaultdict(list)
        self._hp = dict(hp or {})
        self._max = dict(max_hp or {})
        self.events: list[str] = []

    def _hp_for(self, pid):
        return self._hp.get(pid, 0)

    def _max_hp_for(self, pid):
        return self._max.get(pid, 140)

    def heal_player(self, pid, amount):
        before = self._hp.get(pid, 0)
        after = min(self._max_hp_for(pid), before + int(amount))
        self._hp[pid] = after
        return after - before

    def _append_effect_event(self, events, text):
        events.append(text)

    def _card_name_for(self, pid):
        return f"Karte{pid}"

    def _apply_non_heal_damage_with_event(self, events, pid, amount, *, source="", self_damage=False):
        self._hp[pid] = self._hp.get(pid, 0) - int(amount)
        events.append(f"{source}: {amount}")
        return int(amount)


class ReactiveEvolutionTest(unittest.TestCase):
    def test_flat_two_from_first_hit(self):
        eff = _attack("Ultron", "Reaktive Evolution")["effects"][0]
        self.assertEqual(eff["amount"], 2)
        self.assertEqual(eff["max_stacks"], 1)  # kein Stapeln mehr
        ae = defaultdict(list)
        ae[1] = [{"type": "reactive_evolution", "amount": eff["amount"], "max_stacks": eff["max_stacks"], "stacks": 0}]
        reductions = [bot._apply_reactive_evolution_reduction(ae, 1, 20)[1] for _ in range(4)]
        self.assertEqual(reductions, [2, 2, 2, 2])  # bereits der 1. Treffer wird reduziert

    def test_stacking_card_ramps_from_first_hit(self):
        # Sicherheitsnetz: eine (hypothetische) stapelnde Karte reduziert ab Treffer 1.
        ae = defaultdict(list)
        ae[1] = [{"type": "reactive_evolution", "amount": 2, "max_stacks": 3, "stacks": 0}]
        reductions = [bot._apply_reactive_evolution_reduction(ae, 1, 50)[1] for _ in range(4)]
        self.assertEqual(reductions, [2, 4, 6, 6])


class BurnMultiplierLifecycleTest(unittest.TestCase):
    def test_burn_multiplier_not_turn_decayed(self):
        self.assertNotIn("burn_multiplier", effect_handler.TURN_END_DECAY_EFFECT_TYPES)

    def test_supernova_cooldown_lowered(self):
        self.assertEqual(_attack("Human Torch", "Supernova-Ladung")["cooldown_turns"], 4)


class ConditionalSelfHealTest(unittest.TestCase):
    def setUp(self):
        self.attack = _attack("Ultron", "System-Optimierung")

    def test_card_defines_heal(self):
        self.assertEqual(self.attack["heal_if_condition"], 15)
        self.assertEqual(self.attack["heal_if_self_hp_below_pct"], 0.5)

    def test_heals_when_below_threshold(self):
        view = FakeBattleView(hp={1: 60}, max_hp={1: 140})  # 60 <= 70
        healed = bot._apply_conditional_self_heal(view, 1, self.attack, view.events)
        self.assertEqual(healed, 15)
        self.assertEqual(view._hp[1], 75)

    def test_no_heal_above_threshold(self):
        view = FakeBattleView(hp={1: 100}, max_hp={1: 140})  # 100 > 70
        healed = bot._apply_conditional_self_heal(view, 1, self.attack, view.events)
        self.assertEqual(healed, 0)

    def test_blocked_when_healing_disabled(self):
        view = FakeBattleView(hp={1: 10}, max_hp={1: 140})
        healed = bot._apply_conditional_self_heal(view, 1, self.attack, view.events, healing_disabled=True)
        self.assertEqual(healed, 0)


class FlammenwandInterruptTest(unittest.TestCase):
    def test_card_data(self):
        atk = _attack("Human Torch", "Flammenwand")
        eff = atk["effects"][0]
        self.assertEqual(eff["type"], "interrupt_enemy_standard_or_heal_self")
        self.assertEqual(eff["damage"], 15)
        self.assertEqual(eff["heal"], 12)
        self.assertEqual(atk["cooldown_turns"], 4)

    def _view_with_interrupt(self, attacker_id, applier_id):
        view = FakeBattleView(hp={attacker_id: 100, applier_id: 100}, max_hp={attacker_id: 140, applier_id: 140})
        view.active_effects[attacker_id] = [
            {"type": "interrupt_enemy_standard_or_heal_self", "damage": 15, "heal": 12, "applier": applier_id, "turns": 1}
        ]
        return view

    def test_standard_attack_is_interrupted_and_damages_attacker(self):
        view = self._view_with_interrupt(attacker_id=0, applier_id=5)
        interrupted = bot._consume_interrupt_effect(view, 0, {"name": "Standard", "is_standard_attack": True}, view.events)
        self.assertTrue(interrupted)
        self.assertEqual(view._hp[0], 85)  # Angreifer erleidet 15
        self.assertEqual(view._hp[5], 100)  # keine Heilung
        self.assertEqual(view.active_effects[0], [])  # Effekt verbraucht

    def test_ability_heals_the_caster(self):
        view = self._view_with_interrupt(attacker_id=0, applier_id=5)
        interrupted = bot._consume_interrupt_effect(view, 0, {"name": "Fähigkeit"}, view.events)
        self.assertFalse(interrupted)  # Angriff läuft normal weiter
        self.assertEqual(view._hp[5], 112)  # Caster heilt 12
        self.assertEqual(view._hp[0], 100)  # Angreifer nimmt keinen Schaden
        self.assertEqual(view.active_effects[0], [])

    def test_no_effect_when_absent(self):
        view = FakeBattleView(hp={0: 100}, max_hp={0: 140})
        self.assertFalse(bot._consume_interrupt_effect(view, 0, {"is_standard_attack": True}, view.events))


class HexFluchHealComponentTest(unittest.TestCase):
    def test_damage_plus_heal_attack_is_recognized(self):
        # "Wir sind Venom": Schaden + Heilung -> muss als Heilquelle erkannt werden,
        # damit der Hex-Fluch die Heilung blockiert.
        self.assertTrue(bot._attack_has_heal_component(_attack("Venom", "Wir sind Venom")))

    def test_pure_damage_attack_has_no_heal(self):
        self.assertFalse(bot._attack_has_heal_component(_attack("Ultron", "Encephalon-Strahl")))


if __name__ == "__main__":
    unittest.main()
