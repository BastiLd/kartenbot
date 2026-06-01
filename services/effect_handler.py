"""Pure Helfer für Status-/DoT-Effekte im Kampf.

Dieser Cluster wurde aus bot.py ausgelagert (Audit D5). Die Funktionen sind
zustandslos: sie operieren ausschließlich auf den übergebenen ``active_effects``-
Dicts bzw. Effekt-Dicts und hängen sonst nur an den Coercion-Utils und den
DoT-Defaults aus der Kartendefinition. Dadurch sind sie unabhängig von bot.py
und Discord testbar. bot.py re-importiert alle Namen, damit bestehende Aufrufe
(inkl. ``bot_module.<name>`` aus services/combat_runner.py) unverändert
funktionieren.
"""

from __future__ import annotations

from typing import Callable

from karten import DOT_TYPE_DEFAULTS
from services.coercion import _maybe_int, _random_int_from_range, _range_pair


def _effect_int(effect: dict[str, object], key: str, default: int = 0) -> int:
    return _maybe_int(effect.get(key, default)) or default


def _effect_amount(effect: dict[str, object], key: str, default: int = 0) -> int:
    return _random_int_from_range(effect.get(key, default), default=default)


def _effect_amount_label(value: object, default: int = 0) -> str:
    min_value, max_value = _range_pair(value, default_min=default, default_max=default)
    if min_value == max_value:
        return str(min_value)
    return f"{min_value}-{max_value}"


def _is_dot_effect_type(effect_type: object) -> bool:
    return str(effect_type or "").strip().lower() in DOT_TYPE_DEFAULTS


def _dot_label(effect_type: object) -> str:
    key = str(effect_type or "").strip().lower()
    return str(DOT_TYPE_DEFAULTS.get(key, {}).get("label") or "Effekt")


def _dot_icon(effect_type: object) -> str:
    key = str(effect_type or "").strip().lower()
    return str(DOT_TYPE_DEFAULTS.get(key, {}).get("icon") or "")


def _resolve_dot_damage(effect_type: object, raw_damage: object) -> int:
    key = str(effect_type or "").strip().lower()
    configured_cap = int(DOT_TYPE_DEFAULTS.get(key, {}).get("max_damage") or 0)
    damage = max(0, _random_int_from_range(raw_damage, default=0))
    if configured_cap > 0:
        damage = min(damage, configured_cap)
    return max(0, damage)


def _append_dot_effect(
    active_effects: dict[int, list[dict[str, object]]],
    *,
    target_id: int,
    attacker_id: int,
    effect_type: object,
    duration: object,
    damage: object,
    damage_multiplier: float = 1.0,
) -> tuple[int, int]:
    resolved_type = str(effect_type or "").strip().lower()
    resolved_duration = max(1, _random_int_from_range(duration, default=1))
    resolved_damage = _resolve_dot_damage(resolved_type, damage)
    multiplier = max(0.0, float(damage_multiplier or 1.0))
    if abs(multiplier - 1.0) > 1e-9:
        resolved_damage = max(0, int(round(resolved_damage * multiplier)))
    active_effects[target_id].append(
        {
            "type": resolved_type,
            "duration": resolved_duration,
            "damage": resolved_damage,
            "applier": attacker_id,
        }
    )
    return resolved_duration, resolved_damage


def _apply_dot_ticks_for_applier(
    active_effects: dict[int, list[dict[str, object]]],
    *,
    target_id: int,
    applier_id: int,
    damage_callback: Callable[[int], object],
) -> tuple[int, list[str]]:
    remove: list[dict[str, object]] = []
    total_damage = 0
    events: list[str] = []
    for effect in active_effects[target_id]:
        effect_type = str(effect.get("type") or "").strip().lower()
        if effect.get("applier") != applier_id or not _is_dot_effect_type(effect_type):
            continue
        damage = _effect_int(effect, "damage")
        if damage > 0:
            damage_callback(damage)
            total_damage += damage
            events.append(f"{_dot_label(effect_type)}: {damage} Schaden.")
        remaining_duration = _effect_int(effect, "duration") - 1
        effect["duration"] = remaining_duration
        if remaining_duration <= 0:
            remove.append(effect)
    for effect in remove:
        active_effects[target_id].remove(effect)
    return total_damage, events


NEGATIVE_STATUS_EFFECT_TYPES = frozenset(
    {
        "blind",
        "bleeding",
        "burning",
        "confusion",
        "disable_enemy_evade_and_block",
        "disable_enemy_heal_if_bleeding",
        "enemy_attack_self_damage",
        "enemy_force_min_damage",
        "enemy_next_special_self_damage",
        "enemy_special_self_damage",
        "heal_curse",
        "incoming_damage_bonus",
        "incoming_damage_multiplier",
        "poison",
        "special_lock",
        "standard_lock",
        "stun",
    }
)
TURN_END_DECAY_EFFECT_TYPES = frozenset(
    {
        "burn_multiplier",
        "disable_enemy_evade_and_block",
        "disable_enemy_heal_if_bleeding",
        "enemy_attack_self_damage",
        "enemy_force_min_damage",
        "enemy_next_special_self_damage",
        "enemy_special_self_damage",
        "heal_curse",
        "incoming_damage_bonus",
        "next_attack_flat_penalty",
        "standard_lock",
        "status_immunity",
        "interrupt_enemy_standard_or_heal_self",
    }
)


def _active_effect_entries(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect_type: str,
) -> list[dict[str, object]]:
    effect_key = str(effect_type or "").strip().lower()
    return [
        effect
        for effect in active_effects.get(player_id, [])
        if str(effect.get("type") or "").strip().lower() == effect_key
    ]


def _find_active_effect(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect_type: str,
) -> dict[str, object] | None:
    entries = _active_effect_entries(active_effects, player_id, effect_type)
    return entries[0] if entries else None


def _append_active_effect(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect_type: str,
    applier_id: int,
    **fields: object,
) -> dict[str, object]:
    entry: dict[str, object] = {"type": str(effect_type or "").strip().lower(), "applier": applier_id}
    entry.update(fields)
    active_effects.setdefault(player_id, []).append(entry)
    return entry


def _remove_active_effect(
    active_effects: dict[int, list[dict[str, object]]],
    player_id: int,
    effect: dict[str, object] | None,
) -> None:
    if effect is None:
        return
    try:
        active_effects.get(player_id, []).remove(effect)
    except ValueError:
        pass


def _label_key(value: object) -> str:
    return str(value or "").strip().casefold()
