from __future__ import annotations

from typing import NotRequired, TypeAlias, TypedDict


DamageValue: TypeAlias = int | list[int]


class AttackEffect(TypedDict, total=False):
    type: str
    value: int | float | str
    duration: int
    chance: float
    target: str


class MultiHitConfig(TypedDict, total=False):
    hits: int
    per_hit_damage: list[int]
    hit_chance: float
    guaranteed_min_per_hit: int


class MultiHitRollDetails(TypedDict):
    hits: int
    landed_hits: int
    per_hit_damages: list[int]
    total_before_multiplier: int
    total_damage: int


class AttackData(TypedDict, total=False):
    name: str
    info: str
    damage: DamageValue
    multi_hit: MultiHitConfig
    effects: list[AttackEffect]
    requires_reload: bool
    cooldown: int
    bonus_if_self_hp_below_pct: float
    bonus_damage_if_condition: int
    conditional_enemy_hp_below_pct: float
    damage_if_condition: DamageValue
    add_absorbed_damage: bool


class CardData(TypedDict, total=False):
    name: str
    beschreibung: str
    bild: str
    hp: int
    raritaet: str
    angriffe: list[AttackData]

