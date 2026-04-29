from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from services.combat_runner import CombatRunner


CONTROL_EFFECTS = {"stun", "confusion", "special_lock", "standard_lock", "blind", "heal_curse"}
DEFENSE_EFFECTS = {
    "damage_reduction",
    "damage_reduction_sequence",
    "damage_reduction_flat",
    "enemy_next_attack_reduction_percent",
    "enemy_next_attack_reduction_flat",
    "reflect",
    "absorb_store",
    "cap_damage",
    "evade",
    "stealth",
    "shield",
    "airborne_two_phase",
    "status_immunity",
    "disable_enemy_evade_and_block",
    "reactive_evolution",
}
SETUP_EFFECTS = {"damage_boost", "damage_multiplier", "force_max", "guaranteed_hit", "burn_multiplier", "incoming_damage_bonus"}
DOT_EFFECTS = {"burning", "poison", "bleeding"}


class Strategy(Protocol):
    def select_attack_index(self, runner: CombatRunner, player_id: int) -> int: ...


@dataclass(slots=True)
class MoveScore:
    attack_index: int
    score: float
    min_damage: int
    max_damage: int


def _effect_types(attack: dict) -> set[str]:
    return {
        str(effect.get("type") or "").strip().lower()
        for effect in attack.get("effects", [])
        if isinstance(effect, dict)
    }


def evaluate_move(runner: CombatRunner, player_id: int, attack_index: int) -> MoveScore:
    selection = runner.preview_attack_selection(player_id, attack_index)
    attack = selection.attack
    defender_id = selection.defender_id
    min_damage, max_damage = runner.estimate_attack_range(player_id, attack_index)
    expected_damage = (min_damage + max_damage) / 2.0
    defender_hp = runner._hp_for(defender_id)
    attacker_hp = runner._hp_for(player_id)
    attacker_max_hp = max(1, runner._max_hp_for(player_id))
    hp_missing = max(0, runner._max_hp_for(player_id) - attacker_hp)
    effect_types = _effect_types(attack)

    score = expected_damage * 3.0 + max_damage * 0.4
    if min_damage >= defender_hp and min_damage > 0:
        score += 10000
    elif max_damage >= defender_hp and max_damage > 0:
        score += 6000

    heal_amount = 0
    raw_heal = attack.get("heal")
    if isinstance(raw_heal, list) and len(raw_heal) == 2:
        heal_amount += int(raw_heal[1] or 0)
    else:
        heal_amount += int(raw_heal or 0)
    heal_amount += int(round(max_damage * float(attack.get("lifesteal_ratio", 0.0) or 0.0)))
    if heal_amount > 0:
        score += min(hp_missing, heal_amount) * 2.2
        if attacker_hp / attacker_max_hp < 0.45:
            score += min(hp_missing, heal_amount) * 1.3

    score += len(effect_types & CONTROL_EFFECTS) * 140.0
    score += len(effect_types & DEFENSE_EFFECTS) * 110.0
    score += len(effect_types & SETUP_EFFECTS) * 85.0
    score += len(effect_types & DOT_EFFECTS) * 90.0

    if selection.is_reload_action:
        score -= 180.0
    if bool(attack.get("self_damage")):
        self_damage = attack.get("self_damage")
        if isinstance(self_damage, list) and len(self_damage) == 2:
            score -= float(int(self_damage[1] or 0)) * 3.0
        else:
            score -= float(int(self_damage or 0)) * 3.0
    if attack.get("requires_reload") and not selection.is_reload_action:
        score -= 8.0
    if attack.get("cooldown_turns"):
        score -= float(int(attack.get("cooldown_turns", 0) or 0)) * 2.0
    if "copy_last_enemy_special" in effect_types and selection.last_enemy_special_entry is None:
        score -= 120.0
    if "reset_own_cooldown" in effect_types and selection.reset_cooldown_index is None:
        score -= 100.0
    if runner.has_stealth(defender_id) and max_damage > 0 and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable")):
        score -= 240.0

    return MoveScore(attack_index=attack_index, score=score, min_damage=min_damage, max_damage=max_damage)


def score_legal_moves(runner: CombatRunner, player_id: int) -> list[MoveScore]:
    legal = runner.legal_attack_indices(player_id)
    moves = [evaluate_move(runner, player_id, attack_index) for attack_index in legal]
    moves.sort(key=lambda move: (-move.score, -move.max_damage, -move.min_damage, move.attack_index))
    return moves


class OptimalStrategy:
    def select_attack_index(self, runner: CombatRunner, player_id: int) -> int:
        return score_legal_moves(runner, player_id)[0].attack_index


class AverageStrategy:
    def __init__(self, rng: random.Random, mistake_rate: float) -> None:
        self.rng = rng
        self.mistake_rate = max(0.0, min(1.0, float(mistake_rate)))

    def select_attack_index(self, runner: CombatRunner, player_id: int) -> int:
        moves = score_legal_moves(runner, player_id)
        if self.mistake_rate <= 0.0 or len(moves) == 1:
            return moves[0].attack_index
        max_score = moves[0].score
        scale = max(30.0, abs(max_score) * 0.08)
        temperature = 0.18 + self.mistake_rate * 1.8
        weights: list[float] = []
        for move in moves:
            weight = math.exp((move.score - max_score) / (scale * temperature))
            weights.append(max(weight, 1e-9))
        return self.rng.choices([move.attack_index for move in moves], weights=weights, k=1)[0]


def build_strategy(name: str, *, rng: random.Random, average_mistake_rate: float = 0.35) -> Strategy:
    normalized = str(name or "").strip().lower()
    if normalized == "optimal":
        return OptimalStrategy()
    if normalized == "average":
        return AverageStrategy(rng=rng, mistake_rate=average_mistake_rate)
    raise ValueError(f"Unsupported strategy: {name}")
