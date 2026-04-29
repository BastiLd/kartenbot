from __future__ import annotations

import copy

from services.battle_types import AttackData, CardData
from simulation.config import SimulationMode


NON_PURE_DAMAGE_KEYS = {
    "effects",
    "heal",
    "self_damage",
    "lifesteal_ratio",
    "requires_reload",
    "reload_name",
    "add_absorbed_damage",
    "damage_breakdown",
    "conditional_enemy_hp_below_pct",
    "bonus_if_self_hp_below_pct",
    "damage_if_condition",
    "bonus_damage_if_condition",
    "guaranteed_hit_if_condition",
    "cooldown_overrides_by_final_damage",
    "multi_hit",
}


def is_pure_damage_attack(attack: AttackData) -> bool:
    damage = attack.get("damage")
    if isinstance(damage, list) and len(damage) == 2:
        if int(damage[1] or 0) <= 0:
            return False
    elif int(damage or 0) <= 0:
        return False
    for key in NON_PURE_DAMAGE_KEYS:
        value = attack.get(key)
        if key == "reload_name" and str(value or "").strip():
            return False
        if value not in (None, False, 0, 0.0, [], {}):
            return False
    return True


def _standard_attack_index(attacks: list[AttackData]) -> int:
    for index, attack in enumerate(attacks[:4]):
        if bool(attack.get("is_standard_attack")):
            return index
    return 0


def _apply_max_only_bonus(damage: object, bonus: int) -> object:
    amount = max(0, int(bonus or 0))
    if amount <= 0:
        return copy.deepcopy(damage)
    if isinstance(damage, list) and len(damage) == 2:
        base_min = int(damage[0] or 0)
        base_max = int(damage[1] or 0)
        return [base_min, max(base_min, base_max + amount)]
    base_value = int(damage or 0)
    return [base_value, base_value + amount]


def apply_mode_to_card(card: CardData, mode: SimulationMode) -> CardData:
    updated = copy.deepcopy(card)
    attacks = [copy.deepcopy(attack) for attack in updated.get("attacks", []) if isinstance(attack, dict)]
    updated["attacks"] = attacks
    if mode == SimulationMode.ORIGINAL:
        return updated
    updated["hp"] = 170 if mode == SimulationMode.LIGHT else 200
    standard_index = _standard_attack_index(attacks)
    for index, attack in enumerate(attacks):
        if not is_pure_damage_attack(attack):
            continue
        if mode == SimulationMode.LIGHT:
            attack["damage"] = _apply_max_only_bonus(attack.get("damage", [0, 0]), 15)
        else:
            attack["damage"] = _apply_max_only_bonus(attack.get("damage", [0, 0]), 8 if index == standard_index else 15)
    return updated


def apply_mode_to_cards(cards: list[CardData], mode: SimulationMode) -> list[CardData]:
    return [apply_mode_to_card(card, mode) for card in cards]
