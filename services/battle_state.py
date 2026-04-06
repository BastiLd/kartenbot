import logging
from typing import TypeAlias, TypedDict

from services.battle import apply_outgoing_attack_modifier
from services.battle_types import DamageValue


BattleEntry: TypeAlias = dict[str, object]
BattleEffectsMap: TypeAlias = dict[int, list[BattleEntry]]
BattleCooldownsMap: TypeAlias = dict[int, dict[int, int]]
BattleReloadMap: TypeAlias = dict[int, dict[int, bool]]
BattleBoolMap: TypeAlias = dict[int, bool]
BattleIntMap: TypeAlias = dict[int, int]
BattleFloatMap: TypeAlias = dict[int, float]
BattlePendingLandingMap: TypeAlias = dict[int, BattleEntry | None]
BattleEntryMap: TypeAlias = dict[int, BattleEntry | None]
DamageInput = DamageValue


class BattleRuntimeMaps(TypedDict):
    cooldowns_by_player: BattleCooldownsMap
    active_effects: BattleEffectsMap
    confused_next_turn: BattleBoolMap
    manual_reload_needed: BattleReloadMap
    stunned_next_turn: BattleBoolMap
    special_lock_next_turn: BattleIntMap
    blind_next_attack: BattleFloatMap
    pending_flat_bonus: BattleIntMap
    pending_flat_bonus_uses: BattleIntMap
    pending_multiplier: BattleFloatMap
    pending_multiplier_uses: BattleIntMap
    force_max_next: BattleIntMap
    guaranteed_hit_next: BattleIntMap
    incoming_modifiers: BattleEffectsMap
    outgoing_attack_modifiers: BattleEffectsMap
    absorbed_damage: BattleIntMap
    delayed_defense_queue: BattleEffectsMap
    airborne_pending_landing: BattlePendingLandingMap
    last_special_attack: BattleEntryMap


def build_battle_runtime_maps(player_ids: tuple[int, int]) -> BattleRuntimeMaps:
    player_a, player_b = player_ids
    return {
        "cooldowns_by_player": {player_a: {}, player_b: {}},
        "active_effects": {player_a: [], player_b: []},
        "confused_next_turn": {player_a: False, player_b: False},
        "manual_reload_needed": {player_a: {}, player_b: {}},
        "stunned_next_turn": {player_a: False, player_b: False},
        "special_lock_next_turn": {player_a: 0, player_b: 0},
        "blind_next_attack": {player_a: 0.0, player_b: 0.0},
        "pending_flat_bonus": {player_a: 0, player_b: 0},
        "pending_flat_bonus_uses": {player_a: 0, player_b: 0},
        "pending_multiplier": {player_a: 1.0, player_b: 1.0},
        "pending_multiplier_uses": {player_a: 0, player_b: 0},
        "force_max_next": {player_a: 0, player_b: 0},
        "guaranteed_hit_next": {player_a: 0, player_b: 0},
        "incoming_modifiers": {player_a: [], player_b: []},
        "outgoing_attack_modifiers": {player_a: [], player_b: []},
        "absorbed_damage": {player_a: 0, player_b: 0},
        "delayed_defense_queue": {player_a: [], player_b: []},
        "airborne_pending_landing": {player_a: None, player_b: None},
        "last_special_attack": {player_a: None, player_b: None},
    }


def summarize_card_buffs(buffs) -> tuple[int, dict[int, int]]:
    total_health = 0
    damage_map: dict[int, int] = {}
    for buff_type, attack_number, buff_amount in buffs:
        if buff_type == "health" and int(attack_number or 0) == 0:
            total_health += int(buff_amount or 0)
        elif buff_type == "damage" and 1 <= int(attack_number or 0) <= 4:
            idx = int(attack_number or 0)
            damage_map[idx] = damage_map.get(idx, 0) + int(buff_amount or 0)
    return total_health, damage_map


def status_icons(active_effects: BattleEffectsMap, player_id: int) -> str:
    effects = active_effects.get(player_id, [])
    icons = []
    if any(effect.get("type") == "burning" for effect in effects):
        icons.append("\U0001f525")
    if any(effect.get("type") == "poison" for effect in effects):
        icons.append("☠️")
    if any(effect.get("type") == "bleeding" for effect in effects):
        icons.append("🩸")
    if any(effect.get("type") == "confusion" for effect in effects):
        icons.append("\U0001f300")
    if any(effect.get("type") == "stealth" for effect in effects):
        icons.append("\U0001f977")
    if any(effect.get("type") == "airborne" for effect in effects):
        icons.append("\u2708\ufe0f")
    if any(effect.get("type") == "shield" for effect in effects):
        icons.append("\U0001f6e1\ufe0f")
    return f" {' '.join(icons)}" if icons else ""


def append_effect_event(events: list[str], text: str) -> None:
    message = str(text).strip()
    if message:
        events.append(message)


def is_reload_needed(manual_reload_needed: dict[int, dict[int, bool]], player_id: int, attack_index: int) -> bool:
    return bool(manual_reload_needed.get(player_id, {}).get(attack_index, False))


def set_reload_needed(
    manual_reload_needed: dict[int, dict[int, bool]],
    player_id: int,
    attack_index: int,
    needed: bool,
) -> None:
    bucket = manual_reload_needed.setdefault(player_id, {})
    if needed:
        bucket[attack_index] = True
    else:
        bucket.pop(attack_index, None)


def find_effect(active_effects: BattleEffectsMap, player_id: int, effect_type: str) -> BattleEntry | None:
    for effect in active_effects.get(player_id, []):
        if effect.get("type") == effect_type:
            return effect
    return None


def has_effect(active_effects: BattleEffectsMap, player_id: int, effect_type: str) -> bool:
    return find_effect(active_effects, player_id, effect_type) is not None


def consume_effect(active_effects: BattleEffectsMap, player_id: int, effect_type: str) -> bool:
    effect = find_effect(active_effects, player_id, effect_type)
    if not effect:
        return False
    try:
        active_effects[player_id].remove(effect)
    except ValueError:
        pass
    return True


def grant_unique_effect(
    active_effects: BattleEffectsMap,
    player_id: int,
    effect_type: str,
    applier_id: int,
    *,
    duration: int = 1,
    extra_fields: BattleEntry | None = None,
) -> None:
    try:
        active_effects[player_id] = [
            effect for effect in active_effects.get(player_id, []) if effect.get("type") != effect_type
        ]
    except Exception:
        active_effects[player_id] = []
    effect_entry = {"type": effect_type, "duration": duration, "applier": applier_id}
    if extra_fields:
        effect_entry.update(extra_fields)
    active_effects[player_id].append(effect_entry)


def set_confusion(
    active_effects: BattleEffectsMap,
    confused_next_turn: BattleBoolMap,
    player_id: int,
    applier_id: int,
) -> None:
    confused_next_turn[player_id] = True
    grant_unique_effect(active_effects, player_id, "confusion", applier_id, duration=1)


def consume_confusion_if_any(
    active_effects: BattleEffectsMap,
    confused_next_turn: BattleBoolMap,
    player_id: int,
) -> None:
    if confused_next_turn.get(player_id, False):
        confused_next_turn[player_id] = False
        try:
            active_effects[player_id] = [
                effect for effect in active_effects.get(player_id, []) if effect.get("type") != "confusion"
            ]
        except Exception:
            logging.exception("Unexpected error")


def get_attack_max_damage(attack_damage, damage_buff: int = 0) -> int:
    if isinstance(attack_damage, list) and len(attack_damage) == 2:
        return int(attack_damage[1]) + int(damage_buff or 0)
    return int(attack_damage) + int(damage_buff or 0)


def get_attack_min_damage(attack_damage, damage_buff: int = 0) -> int:
    if isinstance(attack_damage, list) and len(attack_damage) == 2:
        return int(attack_damage[0]) + int(damage_buff or 0)
    return int(attack_damage) + int(damage_buff or 0)


def is_strong_attack(attack_damage, damage_buff: int = 0) -> bool:
    min_damage = get_attack_min_damage(attack_damage, damage_buff)
    max_damage = get_attack_max_damage(attack_damage, damage_buff)
    return min_damage > 90 and max_damage > 99


def is_attack_on_cooldown(cooldown_map: dict[int, int], attack_index: int) -> bool:
    return int(cooldown_map.get(attack_index, 0) or 0) > 0


def start_attack_cooldown(cooldown_map: dict[int, int], attack_index: int, turns: int = 2) -> None:
    cooldown_map[attack_index] = max(0, int(turns or 0))


def reduce_cooldowns(cooldown_map: dict[int, int]) -> None:
    for attack_index in list(cooldown_map.keys()):
        cooldown_map[attack_index] -= 1
        if cooldown_map[attack_index] <= 0:
            del cooldown_map[attack_index]


def queue_delayed_defense(
    delayed_defense_queue: BattleEffectsMap,
    player_id: int,
    defense: str,
    counter: int = 0,
    source: str | None = None,
) -> None:
    defense_mode = str(defense or "").strip().lower()
    if defense_mode not in {"evade", "stealth"}:
        return
    delayed_defense_queue[player_id].append(
        {
            "defense": defense_mode,
            "counter": max(0, int(counter)),
            "source": str(source or "").strip() or None,
        }
    )


def queue_incoming_modifier(
    incoming_modifiers: BattleEffectsMap,
    player_id: int,
    *,
    percent: float = 0.0,
    flat: int = 0,
    reflect: float = 0.0,
    store_ratio: float = 0.0,
    max_store: int | None = None,
    cap: int | str | None = None,
    evade: bool = False,
    counter: int = 0,
    turns: int = 1,
    source: str | None = None,
) -> None:
    if turns <= 0:
        turns = 1
    for _ in range(turns):
        incoming_modifiers[player_id].append(
            {
                "percent": max(0.0, float(percent)),
                "flat": max(0, int(flat)),
                "reflect": max(0.0, float(reflect)),
                "store_ratio": max(0.0, float(store_ratio)),
                "max_store": (max(0, int(max_store)) if max_store is not None else None),
                "cap": ("attack_min" if str(cap).strip().lower() == "attack_min" else (int(cap) if cap is not None else None)),
                "evade": bool(evade),
                "counter": max(0, int(counter)),
                "source": str(source or "").strip() or None,
            }
        )


def activate_delayed_defense_after_attack(
    delayed_defense_queue: BattleEffectsMap,
    active_effects: BattleEffectsMap,
    incoming_modifiers: BattleEffectsMap,
    player_id: int,
    effect_events: list[str],
    *,
    attack_landed: bool,
) -> None:
    queued = list(delayed_defense_queue.get(player_id, []))
    if not queued:
        return
    if not attack_landed:
        append_effect_event(effect_events, "Schutz bleibt vorbereitet: Kein Trefferschaden, Aktivierung verschoben.")
        return
    delayed_defense_queue[player_id] = []
    for entry in queued:
        defense_mode = entry.get("defense")
        source = str(entry.get("source") or "").strip()
        source_prefix = f"{source}: " if source else ""
        if defense_mode == "evade":
            counter = int(entry.get("counter", 0) or 0)
            queue_incoming_modifier(
                incoming_modifiers,
                player_id,
                evade=True,
                counter=counter,
                turns=1,
                source=source,
            )
            append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird ausgewichen.")
        elif defense_mode == "stealth":
            grant_unique_effect(active_effects, player_id, "stealth", player_id, duration=1)
            append_effect_event(effect_events, "Schutz aktiv: Der nächste gegnerische Angriff wird vollständig geblockt.")


def start_airborne_two_phase(
    active_effects: BattleEffectsMap,
    airborne_pending_landing: BattlePendingLandingMap,
    incoming_modifiers: BattleEffectsMap,
    player_id: int,
    landing_damage: DamageInput | object,
    effect_events: list[str],
    *,
    landing_attack: BattleEntry | None = None,
    source_attack_index: int | None = None,
    cooldown_turns: int = 0,
) -> None:
    if isinstance(landing_damage, list) and len(landing_damage) == 2:
        min_damage = int(landing_damage[0])
        max_damage = int(landing_damage[1])
    else:
        min_damage = 20
        max_damage = 40
    min_damage = max(0, min_damage)
    max_damage = max(min_damage, max_damage)
    pending_attack: BattleEntry = dict(landing_attack or {})
    pending_attack.setdefault("name", "Landungsschlag")
    pending_attack["damage"] = [min_damage, max_damage]
    pending_attack["cooldown_attack_index"] = int(source_attack_index) if source_attack_index is not None else None
    pending_attack["cooldown_turns"] = max(0, int(cooldown_turns or 0))
    pending_attack.setdefault("info", "Automatischer Folgetreffer aus der Flugphase.")
    airborne_pending_landing[player_id] = {
        "damage": [min_damage, max_damage],
        "attack": pending_attack,
    }
    queue_incoming_modifier(incoming_modifiers, player_id, evade=True, counter=0, turns=1, source="airborne")
    grant_unique_effect(active_effects, player_id, "airborne", player_id, duration=1)
    append_effect_event(effect_events, "Flugphase aktiv: Der nächste gegnerische Angriff verfehlt.")


def resolve_forced_landing_if_due(
    active_effects: BattleEffectsMap,
    airborne_pending_landing: BattlePendingLandingMap,
    player_id: int,
    effect_events: list[str],
) -> BattleEntry | None:
    pending = airborne_pending_landing.get(player_id)
    if not pending:
        return None
    airborne_pending_landing[player_id] = None
    try:
        active_effects[player_id] = [
            effect for effect in active_effects.get(player_id, []) if effect.get("type") != "airborne"
        ]
    except Exception:
        logging.exception("Unexpected error")
    append_effect_event(effect_events, "Landungsschlag wurde automatisch ausgelöst.")
    attack = pending.get("attack")
    if isinstance(attack, dict):
        damage = attack.get("damage", [20, 40])
        if isinstance(damage, list) and len(damage) == 2:
            attack["damage"] = [int(damage[0]), int(damage[1])]
        else:
            attack["damage"] = [20, 40]
        attack.setdefault("name", "Landungsschlag")
        attack.setdefault("info", "Automatischer Folgetreffer aus der Flugphase.")
        return attack
    damage = pending.get("damage", [20, 40])
    if isinstance(damage, list) and len(damage) == 2:
        damage_data = [int(damage[0]), int(damage[1])]
    else:
        damage_data = [20, 40]
    return {
        "name": "Landungsschlag",
        "damage": damage_data,
        "info": "Automatischer Folgetreffer aus der Flugphase.",
    }


def hp_for(hp_by_player: dict[int, int], player_id: int) -> int:
    return int(hp_by_player[player_id])


def set_hp_for(hp_by_player: dict[int, int], player_id: int, value: int) -> None:
    hp_by_player[player_id] = max(0, int(value))


def max_hp_for(max_hp_by_player: dict[int, int], player_id: int) -> int:
    return int(max_hp_by_player[player_id])


def heal_player(hp_by_player: dict[int, int], max_hp_by_player: dict[int, int], player_id: int, amount: int) -> int:
    if amount <= 0:
        return 0
    before = hp_for(hp_by_player, player_id)
    after = min(max_hp_for(max_hp_by_player, player_id), before + int(amount))
    set_hp_for(hp_by_player, player_id, after)
    return after - before


def apply_non_heal_damage(hp_by_player: dict[int, int], player_id: int, amount: int) -> int:
    damage = max(0, int(amount or 0))
    if damage <= 0:
        return 0
    before = hp_for(hp_by_player, player_id)
    after = max(0, before - damage)
    set_hp_for(hp_by_player, player_id, after)
    return before - after


def card_name_for(card_names_by_player: dict[int, str], player_id: int, fallback: str = "Spieler") -> str:
    return str(card_names_by_player.get(player_id) or fallback)


def apply_non_heal_damage_with_event(
    hp_by_player: dict[int, int],
    card_names_by_player: dict[int, str],
    events: list[str],
    player_id: int,
    amount: int,
    *,
    source: str,
    self_damage: bool,
) -> int:
    applied = apply_non_heal_damage(hp_by_player, player_id, amount)
    if applied <= 0:
        return 0
    suffix = "Selbstschaden" if self_damage else "Schaden"
    append_effect_event(
        events,
        f"{source}: {applied} {suffix}. {card_name_for(card_names_by_player, player_id)} hat jetzt noch {hp_for(hp_by_player, player_id)} Leben.",
    )
    return applied


def guard_non_heal_damage_result(
    hp_by_player: dict[int, int],
    defender_id: int,
    defender_hp_before: int,
    context: str,
) -> None:
    current_hp = hp_for(hp_by_player, defender_id)
    expected_max_hp = max(0, int(defender_hp_before))
    if current_hp > expected_max_hp:
        logging.error(
            "Non-heal damage guard triggered (%s): defender HP increased from %s to %s; clamping.",
            context,
            expected_max_hp,
            current_hp,
        )
        set_hp_for(hp_by_player, defender_id, expected_max_hp)


def queue_outgoing_attack_modifier(
    outgoing_attack_modifiers: BattleEffectsMap,
    player_id: int,
    *,
    percent: float = 0.0,
    flat: int = 0,
    turns: int = 1,
    source: str | None = None,
) -> None:
    if turns <= 0:
        turns = 1
    for _ in range(turns):
        outgoing_attack_modifiers[player_id].append(
            {
                "percent": max(0.0, float(percent)),
                "flat": max(0, int(flat)),
                "source": str(source or "").strip() or None,
            }
        )


def apply_outgoing_attack_modifiers(
    outgoing_attack_modifiers: BattleEffectsMap,
    attacker_id: int,
    raw_damage: int,
) -> tuple[int, int, BattleEntry | None]:
    if raw_damage <= 0 or not outgoing_attack_modifiers.get(attacker_id):
        return max(0, int(raw_damage)), 0, None
    modifier = outgoing_attack_modifiers[attacker_id].pop(0)
    final_damage, overflow = apply_outgoing_attack_modifier(
        raw_damage,
        percent=float(modifier.get("percent", 0.0) or 0.0),
        flat=int(modifier.get("flat", 0) or 0),
    )
    return final_damage, overflow, modifier


def consume_guaranteed_hit(guaranteed_hit_next: dict[int, int], player_id: int) -> bool:
    if guaranteed_hit_next.get(player_id, 0) <= 0:
        return False
    guaranteed_hit_next[player_id] -= 1
    if guaranteed_hit_next[player_id] < 0:
        guaranteed_hit_next[player_id] = 0
    return True


def resolve_incoming_modifiers(
    incoming_modifiers: dict[int, list[dict]],
    absorbed_damage: dict[int, int],
    defender_id: int,
    raw_damage: int,
    *,
    ignore_evade: bool = False,
    ignore_all_defense: bool = False,
    incoming_min_damage: int | None = None,
) -> tuple[int, int, bool, int, BattleEntry | None]:
    if raw_damage <= 0 or not incoming_modifiers.get(defender_id):
        return raw_damage, 0, False, 0, None
    modifier = incoming_modifiers[defender_id].pop(0)
    if ignore_all_defense:
        return max(0, int(raw_damage)), 0, False, 0, modifier
    if modifier.get("evade") and not ignore_evade:
        return 0, 0, True, int(modifier.get("counter", 0) or 0), modifier

    damage = max(0, int(raw_damage))
    prevented = 0

    percent = float(modifier.get("percent", 0.0) or 0.0)
    if percent > 0:
        cut = int(round(damage * percent))
        damage -= cut
        prevented += cut

    flat = int(modifier.get("flat", 0) or 0)
    if flat > 0:
        cut = min(flat, damage)
        damage -= cut
        prevented += cut

    cap = modifier.get("cap")
    if isinstance(cap, str) and cap.strip().lower() == "attack_min":
        cap_value = max(0, int(incoming_min_damage or 0))
    elif cap is not None:
        cap_value = max(0, int(cap))
    else:
        cap_value = None
    if cap_value is not None and damage > cap_value:
        cut = damage - cap_value
        damage = cap_value
        prevented += cut

    reflect_ratio = float(modifier.get("reflect", 0.0) or 0.0)
    reflected = int(round(prevented * reflect_ratio)) if reflect_ratio > 0 else 0
    if prevented > 0:
        reflected += max(0, int(modifier.get("reflect_flat", 0) or 0))

    store_ratio = float(modifier.get("store_ratio", 0.0) or 0.0)
    if store_ratio > 0 and prevented > 0:
        stored_amount = int(round(prevented * store_ratio))
        max_store = modifier.get("max_store")
        if max_store is not None:
            stored_amount = min(stored_amount, max(0, int(max_store)))
        absorbed_damage[defender_id] += stored_amount

    return max(0, damage), max(0, reflected), False, 0, modifier


def apply_regen_tick(
    active_effects: dict[int, list[dict]],
    hp_by_player: dict[int, int],
    max_hp_by_player: dict[int, int],
    player_id: int,
) -> int:
    total = 0
    remove: list[dict] = []
    for effect in active_effects.get(player_id, []):
        if effect.get("type") != "regen":
            continue
        heal = int(effect.get("heal", 0) or 0)
        total += heal_player(hp_by_player, max_hp_by_player, player_id, heal)
        effect["duration"] = int(effect.get("duration", 0) or 0) - 1
        if effect["duration"] <= 0:
            remove.append(effect)
    for effect in remove:
        try:
            active_effects[player_id].remove(effect)
        except ValueError:
            pass
    return total
