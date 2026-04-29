from __future__ import annotations

import copy

from simulation.config import SimulationMode
from simulation.modes import apply_mode_to_card, is_pure_damage_attack


def _sample_card() -> dict:
    return {
        "name": "Test Hero",
        "hp": 140,
        "attacks": [
            {"name": "Punch", "damage": [10, 20], "is_standard_attack": True},
            {"name": "Heal", "damage": [0, 0], "heal": 15},
            {"name": "Burn", "damage": [5, 5], "effects": [{"type": "burning", "damage": 3, "duration": [2, 2]}]},
            {"name": "Volley", "damage": [0, 0], "multi_hit": {"hits": 3, "hit_chance": 1.0, "per_hit_damage": [4, 5]}},
        ],
    }


def test_original_mode_keeps_values_and_does_not_mutate_input() -> None:
    card = _sample_card()
    baseline = copy.deepcopy(card)

    transformed = apply_mode_to_card(card, SimulationMode.ORIGINAL)

    assert transformed == baseline
    assert card == baseline


def test_light_mode_sets_hp_and_only_buffs_pure_damage() -> None:
    transformed = apply_mode_to_card(_sample_card(), SimulationMode.LIGHT)

    assert transformed["hp"] == 170
    assert transformed["attacks"][0]["damage"] == [10, 35]
    assert transformed["attacks"][1]["damage"] == [0, 0]
    assert transformed["attacks"][2]["damage"] == [5, 5]
    assert transformed["attacks"][3]["damage"] == [0, 0]


def test_max_mode_sets_hp_and_uses_standard_vs_special_bonus() -> None:
    transformed = apply_mode_to_card(_sample_card(), SimulationMode.MAX)

    assert transformed["hp"] == 200
    assert transformed["attacks"][0]["damage"] == [10, 28]
    assert transformed["attacks"][1]["damage"] == [0, 0]


def test_is_pure_damage_attack_is_conservative() -> None:
    assert is_pure_damage_attack({"name": "Hit", "damage": [10, 20]}) is True
    assert is_pure_damage_attack({"name": "Reload", "damage": [10, 20], "requires_reload": True}) is False
    assert is_pure_damage_attack({"name": "Multi", "damage": [0, 0], "multi_hit": {"hits": 2}}) is False
