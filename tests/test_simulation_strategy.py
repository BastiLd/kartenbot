from __future__ import annotations

import random

from services.combat_runner import CombatRunner
from simulation.config import Playstyle
from simulation.engine import simulate_playstyle_batch
from simulation.strategy import AverageStrategy, OptimalStrategy, build_strategy, score_legal_moves


def _cards() -> tuple[dict, dict]:
    hero_a = {
        "name": "Hero A",
        "hp": 140,
        "attacks": [
            {"name": "Quick Hit", "damage": [18, 22], "is_standard_attack": True},
            {"name": "Big Swing", "damage": [28, 40], "cooldown_turns": 4},
            {"name": "Recover", "damage": [0, 0], "heal": 25, "cooldown_turns": 4},
            {"name": "Blind", "damage": [5, 10], "effects": [{"type": "blind", "target": "enemy", "miss_chance": 1.0}]},
        ],
    }
    hero_b = {
        "name": "Hero B",
        "hp": 140,
        "attacks": [
            {"name": "Slash", "damage": [12, 18], "is_standard_attack": True},
            {"name": "Pierce", "damage": [20, 26]},
            {"name": "Guard", "damage": [0, 0], "effects": [{"type": "damage_reduction", "target": "self", "percent": 0.5}]},
            {"name": "Sting", "damage": [16, 24]},
        ],
    }
    return hero_a, hero_b


def test_optimal_and_average_strategies_are_buildable() -> None:
    assert isinstance(build_strategy("optimal", rng=random.Random(1)), OptimalStrategy)
    assert isinstance(build_strategy("average", rng=random.Random(1)), AverageStrategy)


def test_average_uses_same_legal_move_pool_as_optimal() -> None:
    hero_a, hero_b = _cards()
    runner = CombatRunner(hero_a, hero_b)

    legal = runner.legal_attack_indices(runner.current_turn)
    scored = score_legal_moves(runner, runner.current_turn)

    assert [move.attack_index for move in scored] == sorted(legal, key=lambda idx: [m.attack_index for m in scored].index(idx))


def test_average_is_reproducible_with_same_seed() -> None:
    hero_a, hero_b = _cards()
    runner = CombatRunner(hero_a, hero_b)

    first = build_strategy("average", rng=random.Random(7), average_mistake_rate=0.45).select_attack_index(runner, runner.current_turn)
    second = build_strategy("average", rng=random.Random(7), average_mistake_rate=0.45).select_attack_index(runner, runner.current_turn)

    assert first == second


def test_average_mistake_rate_changes_selection_behavior() -> None:
    hero_a, hero_b = _cards()
    runner = CombatRunner(hero_a, hero_b)

    optimal_choice = build_strategy("optimal", rng=random.Random(9)).select_attack_index(runner, runner.current_turn)
    low_mistake_choice = build_strategy("average", rng=random.Random(9), average_mistake_rate=0.0).select_attack_index(runner, runner.current_turn)
    high_mistake_choice = build_strategy("average", rng=random.Random(9), average_mistake_rate=0.95).select_attack_index(runner, runner.current_turn)

    assert low_mistake_choice == optimal_choice
    assert high_mistake_choice in runner.legal_attack_indices(runner.current_turn)


def test_playstyle_both_returns_two_separate_results() -> None:
    hero_a, hero_b = _cards()
    batch = simulate_playstyle_batch([hero_a, hero_b], [], 1, playstyle=Playstyle.BOTH, seed=1, average_mistake_rate=0.35)

    assert set(batch.results) == {"optimal", "average"}
