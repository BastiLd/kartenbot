from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Any

import bot as bot_module
from services import battle_state
from services.battle_types import CardData


PLAYER_ONE_ID = 1
PLAYER_TWO_ID = 2


@dataclass(slots=True)
class CombatStepResult:
    actor_id: int
    defender_id: int
    attack_index: int | None
    attack_name: str
    damage: int
    attacker_hp: int
    defender_hp: int
    winner_id: int | None = None
    loser_id: int | None = None
    skipped: bool = False
    events: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AttackSelection:
    attack_index: int
    attack: dict[str, Any]
    base_damage: object
    attack_name: str
    is_reload_action: bool
    is_forced_landing: bool
    standard_index: int
    defender_id: int
    last_enemy_special_entry: dict[str, object] | None
    reset_cooldown_index: int | None


class CombatRunner:
    def __init__(
        self,
        player_one_card: CardData,
        player_two_card: CardData,
        *,
        starter_id: int = PLAYER_ONE_ID,
        debug: bool = False,
    ) -> None:
        self.player1_id = PLAYER_ONE_ID
        self.player2_id = PLAYER_TWO_ID
        self.player1_card: CardData = copy.deepcopy(player_one_card)
        self.player2_card: CardData = copy.deepcopy(player_two_card)
        self.current_turn = self.player1_id if starter_id != self.player2_id else self.player2_id
        self.debug = bool(debug)

        base_hp_one = int(self.player1_card.get("hp", 100) or 100)
        base_hp_two = int(self.player2_card.get("hp", 100) or 100)
        self._hp_by_player = {
            self.player1_id: base_hp_one,
            self.player2_id: base_hp_two,
        }
        self._max_hp_by_player = {
            self.player1_id: base_hp_one,
            self.player2_id: base_hp_two,
        }
        self._card_names_by_player = {
            self.player1_id: str(self.player1_card.get("name") or "Spieler 1"),
            self.player2_id: str(self.player2_card.get("name") or "Spieler 2"),
        }

        runtime_maps = battle_state.build_battle_runtime_maps((self.player1_id, self.player2_id))
        self.attack_cooldowns = runtime_maps["cooldowns_by_player"]
        self.active_effects = runtime_maps["active_effects"]
        self.confused_next_turn = runtime_maps["confused_next_turn"]
        self.manual_reload_needed = runtime_maps["manual_reload_needed"]
        self.stunned_next_turn = runtime_maps["stunned_next_turn"]
        self.special_lock_next_turn = runtime_maps["special_lock_next_turn"]
        self.blind_next_attack = runtime_maps["blind_next_attack"]
        self.pending_flat_bonus = runtime_maps["pending_flat_bonus"]
        self.pending_flat_bonus_uses = runtime_maps["pending_flat_bonus_uses"]
        self.pending_multiplier = runtime_maps["pending_multiplier"]
        self.pending_multiplier_uses = runtime_maps["pending_multiplier_uses"]
        self.force_max_next = runtime_maps["force_max_next"]
        self.guaranteed_hit_next = runtime_maps["guaranteed_hit_next"]
        self.incoming_modifiers = runtime_maps["incoming_modifiers"]
        self.outgoing_attack_modifiers = runtime_maps["outgoing_attack_modifiers"]
        self.absorbed_damage = runtime_maps["absorbed_damage"]
        self.delayed_defense_queue = runtime_maps["delayed_defense_queue"]
        self.airborne_pending_landing = runtime_maps["airborne_pending_landing"]
        self.last_special_attack = runtime_maps["last_special_attack"]
        self.round_counter = 0
        self._last_damage_roll_meta: dict[str, object] | None = None

    def other_player(self, player_id: int) -> int:
        return self.player2_id if player_id == self.player1_id else self.player1_id

    def card_for(self, player_id: int) -> CardData:
        return self.player1_card if player_id == self.player1_id else self.player2_card

    def attacks_for(self, player_id: int) -> list[dict[str, Any]]:
        attacks = self.card_for(player_id).get("attacks", [])
        return [copy.deepcopy(attack) for attack in attacks[:4] if isinstance(attack, dict)]

    def _append_effect_event(self, events: list[str], text: str) -> None:
        battle_state.append_effect_event(events, text)

    def has_stealth(self, player_id: int) -> bool:
        return battle_state.has_effect(self.active_effects, player_id, "stealth")

    def has_airborne(self, player_id: int) -> bool:
        return battle_state.has_effect(self.active_effects, player_id, "airborne")

    def consume_stealth(self, player_id: int) -> bool:
        return battle_state.consume_effect(self.active_effects, player_id, "stealth")

    def grant_stealth(self, player_id: int) -> None:
        battle_state.grant_unique_effect(self.active_effects, player_id, "stealth", player_id, duration=1)

    def consume_confusion_if_any(self, player_id: int) -> None:
        battle_state.consume_confusion_if_any(self.active_effects, self.confused_next_turn, player_id)

    def set_confusion(self, player_id: int, applier_id: int) -> None:
        battle_state.set_confusion(self.active_effects, self.confused_next_turn, player_id, applier_id)

    def is_reload_needed(self, player_id: int, attack_index: int) -> bool:
        return battle_state.is_reload_needed(self.manual_reload_needed, player_id, attack_index)

    def set_reload_needed(self, player_id: int, attack_index: int, needed: bool) -> None:
        battle_state.set_reload_needed(self.manual_reload_needed, player_id, attack_index, needed)

    def is_attack_on_cooldown(self, player_id: int, attack_index: int) -> bool:
        return battle_state.is_attack_on_cooldown(self.attack_cooldowns[player_id], attack_index)

    def start_attack_cooldown(self, player_id: int, attack_index: int, turns: int = 2) -> None:
        battle_state.start_attack_cooldown(self.attack_cooldowns[player_id], attack_index, turns=turns)

    def reduce_cooldowns(self, player_id: int) -> None:
        battle_state.reduce_cooldowns(self.attack_cooldowns[player_id])

    def _hp_for(self, player_id: int) -> int:
        return battle_state.hp_for(self._hp_by_player, player_id)

    def _max_hp_for(self, player_id: int) -> int:
        return battle_state.max_hp_for(self._max_hp_by_player, player_id)

    def heal_player(self, player_id: int, amount: int) -> int:
        return battle_state.heal_player(self._hp_by_player, self._max_hp_by_player, player_id, amount)

    def _apply_non_heal_damage(self, player_id: int, amount: int) -> int:
        return battle_state.apply_non_heal_damage(self._hp_by_player, player_id, amount)

    def _apply_non_heal_damage_with_event(
        self,
        events: list[str],
        player_id: int,
        amount: int,
        *,
        source: str,
        self_damage: bool,
    ) -> int:
        return battle_state.apply_non_heal_damage_with_event(
            self._hp_by_player,
            self._card_names_by_player,
            events,
            player_id,
            amount,
            source=source,
            self_damage=self_damage,
        )

    def _guard_non_heal_damage_result(self, defender_id: int, defender_hp_before: int, context: str) -> None:
        battle_state.guard_non_heal_damage_result(self._hp_by_player, defender_id, defender_hp_before, context)

    def apply_regen_tick(self, player_id: int) -> int:
        return battle_state.apply_regen_tick(self.active_effects, self._hp_by_player, self._max_hp_by_player, player_id)

    def queue_delayed_defense(
        self,
        player_id: int,
        defense: str,
        *,
        counter: int = 0,
        source: str | None = None,
    ) -> None:
        battle_state.queue_delayed_defense(
            self.delayed_defense_queue,
            player_id,
            defense,
            counter=counter,
            source=source,
        )

    def activate_delayed_defense_after_attack(
        self,
        player_id: int,
        effect_events: list[str],
        *,
        attack_landed: bool,
    ) -> None:
        battle_state.activate_delayed_defense_after_attack(
            self.delayed_defense_queue,
            self.active_effects,
            self.incoming_modifiers,
            player_id,
            effect_events,
            attack_landed=attack_landed,
        )

    def start_airborne_two_phase(
        self,
        player_id: int,
        landing_damage: object,
        effect_events: list[str],
        *,
        landing_attack: dict[str, object] | None = None,
        source_attack_index: int | None = None,
        cooldown_turns: int = 0,
    ) -> None:
        battle_state.start_airborne_two_phase(
            self.active_effects,
            self.airborne_pending_landing,
            self.incoming_modifiers,
            player_id,
            landing_damage,
            effect_events,
            landing_attack=landing_attack,
            source_attack_index=source_attack_index,
            cooldown_turns=cooldown_turns,
        )

    def resolve_forced_landing_if_due(self, player_id: int, effect_events: list[str]) -> dict[str, object] | None:
        return battle_state.resolve_forced_landing_if_due(
            self.active_effects,
            self.airborne_pending_landing,
            player_id,
            effect_events,
        )

    def queue_incoming_modifier(
        self,
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
        battle_state.queue_incoming_modifier(
            self.incoming_modifiers,
            player_id,
            percent=percent,
            flat=flat,
            reflect=reflect,
            store_ratio=store_ratio,
            max_store=max_store,
            cap=cap,
            evade=evade,
            counter=counter,
            turns=turns,
            source=source,
        )

    def queue_outgoing_attack_modifier(
        self,
        player_id: int,
        *,
        percent: float = 0.0,
        flat: int = 0,
        turns: int = 1,
        source: str | None = None,
    ) -> None:
        battle_state.queue_outgoing_attack_modifier(
            self.outgoing_attack_modifiers,
            player_id,
            percent=percent,
            flat=flat,
            turns=turns,
            source=source,
        )

    def apply_outgoing_attack_modifiers(
        self,
        attacker_id: int,
        raw_damage: int,
    ) -> tuple[int, int, dict[str, object] | None]:
        return battle_state.apply_outgoing_attack_modifiers(
            self.outgoing_attack_modifiers,
            attacker_id,
            raw_damage,
        )

    def consume_guaranteed_hit(self, player_id: int) -> bool:
        return battle_state.consume_guaranteed_hit(self.guaranteed_hit_next, player_id)

    def get_attack_max_damage(self, attack_damage: object, damage_buff: int = 0) -> int:
        return battle_state.get_attack_max_damage(attack_damage, damage_buff)

    def get_attack_min_damage(self, attack_damage: object, damage_buff: int = 0) -> int:
        return battle_state.get_attack_min_damage(attack_damage, damage_buff)

    def is_strong_attack(self, attack_damage: object, damage_buff: int = 0) -> bool:
        return battle_state.is_strong_attack(attack_damage, damage_buff)

    def resolve_incoming_modifiers(
        self,
        defender_id: int,
        raw_damage: int,
        *,
        ignore_evade: bool = False,
        ignore_all_defense: bool = False,
        incoming_min_damage: int | None = None,
    ) -> tuple[int, int, bool, int, dict[str, object] | None]:
        return battle_state.resolve_incoming_modifiers(
            self.incoming_modifiers,
            self.absorbed_damage,
            defender_id,
            raw_damage,
            ignore_evade=ignore_evade,
            ignore_all_defense=ignore_all_defense,
            incoming_min_damage=incoming_min_damage,
        )

    def _consume_airborne_evade_marker(self, player_id: int) -> bool:
        modifiers = self.incoming_modifiers.get(player_id) or []
        for index, modifier in enumerate(modifiers):
            if not isinstance(modifier, dict):
                continue
            if not bool(modifier.get("evade")):
                continue
            if str(modifier.get("source") or "").strip().lower() != "airborne":
                continue
            modifiers.pop(index)
            return True
        return False

    def _append_multi_hit_roll_event(self, effect_events: list[str]) -> None:
        meta = self._last_damage_roll_meta or {}
        if meta.get("kind") != "multi_hit":
            return
        details = meta.get("details")
        if not isinstance(details, dict):
            return
        hits = int(details.get("hits", 0) or 0)
        landed = int(details.get("landed_hits", 0) or 0)
        per_hit = details.get("per_hit_damages", [])
        per_hit_numbers = [int(value) for value in per_hit] if isinstance(per_hit, list) else []
        per_hit_text = ", ".join(str(value) for value in per_hit_numbers) if per_hit_numbers else "-"
        total_damage = int(details.get("total_damage", 0) or 0)
        self._append_effect_event(
            effect_events,
            f"Treffer: {landed}/{hits} | Schaden pro Treffer: {per_hit_text} | Gesamt: {total_damage}.",
        )

    def roll_attack_damage(
        self,
        attack: dict[str, Any],
        base_damage: object,
        damage_buff: int,
        attack_multiplier: float,
        force_max_damage: bool,
        guaranteed_hit: bool,
    ) -> tuple[int, bool, int, int]:
        cap = bot_module.MAX_ATTACK_DAMAGE_PER_HIT
        multi_hit = attack.get("multi_hit")
        if isinstance(multi_hit, dict):
            actual_damage, min_damage, max_damage, details = bot_module._resolve_multi_hit_damage_details(
                multi_hit,
                buff_amount=damage_buff,
                attack_multiplier=attack_multiplier,
                force_max=force_max_damage,
                guaranteed_hit=guaranteed_hit,
            )
            actual_damage = min(cap, max(0, int(actual_damage)))
            min_damage = min(cap, max(0, int(min_damage)))
            max_damage = min(cap, max(min_damage, int(max_damage)))
            if isinstance(details, dict):
                details["total_damage"] = actual_damage
            self._last_damage_roll_meta = {"kind": "multi_hit", "details": details}
            is_critical = bool(force_max_damage and actual_damage >= max_damage and max_damage > 0)
            return actual_damage, is_critical, min_damage, max_damage

        self._last_damage_roll_meta = {"kind": "single_hit"}
        actual_damage, is_critical, min_damage, max_damage = bot_module.calculate_damage(base_damage, damage_buff)
        if attack_multiplier != 1.0:
            actual_damage = int(round(actual_damage * attack_multiplier))
            max_damage = int(round(max_damage * attack_multiplier))
            min_damage = int(round(min_damage * attack_multiplier))
        if force_max_damage:
            actual_damage = max_damage
            is_critical = max_damage > 0
        min_damage = min(cap, max(0, int(min_damage)))
        max_damage = min(cap, max(min_damage, int(max_damage)))
        actual_damage = min(cap, max(0, int(actual_damage)))
        return actual_damage, is_critical, min_damage, max_damage

    def preview_attack_selection(self, player_id: int, attack_index: int) -> AttackSelection:
        defender_id = self.other_player(player_id)
        attacks = self.attacks_for(player_id)
        pending_landing = self.airborne_pending_landing.get(player_id)
        forced_landing_attack: dict[str, object] | None = None
        if isinstance(pending_landing, dict):
            raw_attack = pending_landing.get("attack")
            if isinstance(raw_attack, dict):
                forced_landing_attack = copy.deepcopy(raw_attack)
        standard_idx = bot_module._standard_attack_index(attacks)

        if forced_landing_attack is not None:
            attack = forced_landing_attack
            base_damage = attack.get("damage", [20, 40])
            is_reload_action = False
            attack_name = str(attack.get("name") or "Landungsschlag")
            is_forced_landing = True
        else:
            if attack_index < 0 or attack_index >= len(attacks):
                raise IndexError(f"Invalid attack index {attack_index} for player {player_id}")
            attack = copy.deepcopy(attacks[attack_index])
            base_damage = attack.get("damage", [0, 0])
            is_reload_action = bool(attack.get("requires_reload") and self.is_reload_needed(player_id, attack_index))
            attack_name = str(attack.get("reload_name") or "Nachladen") if is_reload_action else str(attack.get("name") or "")
            is_forced_landing = False

        last_enemy_special_entry = self.last_special_attack.get(defender_id)
        attack_effect_types = {
            str(effect.get("type") or "").strip().lower()
            for effect in attack.get("effects", [])
            if isinstance(effect, dict)
        }

        reset_cooldown_index: int | None = None
        if (not is_forced_landing) and (not is_reload_action):
            if "reset_own_cooldown" in attack_effect_types:
                reset_cooldown_index = bot_module._pick_resettable_cooldown_index(
                    self.attack_cooldowns[player_id],
                    exclude_index=attack_index,
                )
            if "copy_last_enemy_special" in attack_effect_types and isinstance(last_enemy_special_entry, dict):
                copied_attack = bot_module._copied_attack_from_history(last_enemy_special_entry)
                if copied_attack is not None:
                    attack = copied_attack
                    base_damage = copied_attack.get("damage", base_damage)

        return AttackSelection(
            attack_index=attack_index,
            attack=attack,
            base_damage=base_damage,
            attack_name=attack_name,
            is_reload_action=is_reload_action,
            is_forced_landing=is_forced_landing,
            standard_index=standard_idx,
            defender_id=defender_id,
            last_enemy_special_entry=copy.deepcopy(last_enemy_special_entry) if isinstance(last_enemy_special_entry, dict) else None,
            reset_cooldown_index=reset_cooldown_index,
        )

    def legal_attack_indices(self, player_id: int) -> list[int]:
        attacks = self.attacks_for(player_id)
        if not attacks:
            return []
        pending_landing = self.airborne_pending_landing.get(player_id)
        if isinstance(pending_landing, dict):
            return [bot_module._pending_landing_slot_index(pending_landing)]

        standard_idx = bot_module._standard_attack_index(attacks)
        legal: list[int] = []
        for attack_index, _attack in enumerate(attacks[:4]):
            if self.special_lock_next_turn.get(player_id, 0) > 0 and attack_index != standard_idx:
                continue
            if attack_index == standard_idx and bot_module._find_active_effect(self.active_effects, player_id, "standard_lock"):
                continue
            if self.is_attack_on_cooldown(player_id, attack_index):
                continue
            legal.append(attack_index)

        if legal:
            return legal

        fallback: list[int] = []
        for attack_index, _attack in enumerate(attacks[:4]):
            if self.special_lock_next_turn.get(player_id, 0) > 0 and attack_index != standard_idx:
                continue
            if attack_index == standard_idx and bot_module._find_active_effect(self.active_effects, player_id, "standard_lock"):
                continue
            fallback.append(attack_index)
        return fallback or [0]

    def estimate_attack_max_damage(self, player_id: int, attack_index: int) -> int:
        _min_damage, max_damage = self.estimate_attack_range(player_id, attack_index)
        return max(0, int(max_damage))

    def estimate_attack_range(self, player_id: int, attack_index: int) -> tuple[int, int]:
        selection = self.preview_attack_selection(player_id, attack_index)
        if selection.is_reload_action:
            return 0, 0

        attack_for_estimate = dict(selection.attack)
        damage_buff = 0
        attacker_hp = self._hp_for(player_id)
        attacker_max_hp = self._max_hp_for(player_id)
        defender_hp = self._hp_for(selection.defender_id)
        defender_max_hp = self._max_hp_for(selection.defender_id)

        conditional_self_pct = bot_module._maybe_float(selection.attack.get("bonus_if_self_hp_below_pct"))
        conditional_self_bonus = bot_module._maybe_int(selection.attack.get("bonus_damage_if_condition", 0)) or 0
        if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * conditional_self_pct):
            damage_buff += conditional_self_bonus

        conditional_enemy_pct = bot_module._maybe_float(selection.attack.get("conditional_enemy_hp_below_pct"))
        if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * conditional_enemy_pct):
            attack_for_estimate["damage"] = bot_module._coerce_damage_input(selection.attack.get("damage_if_condition"), default=0)
        else:
            attack_for_estimate["damage"] = selection.base_damage

        if selection.attack.get("add_absorbed_damage"):
            damage_buff += int(self.absorbed_damage.get(player_id, 0) or 0)

        min_damage, max_damage = bot_module._attack_total_damage_range(
            attack_for_estimate,
            max_only_bonus=0,
            flat_bonus=damage_buff,
        )
        if max_damage > 0 and self.pending_flat_bonus_uses.get(player_id, 0) > 0:
            flat_bonus = int(self.pending_flat_bonus.get(player_id, 0) or 0)
            min_damage += flat_bonus
            max_damage += flat_bonus
        if max_damage > 0 and self.pending_multiplier_uses.get(player_id, 0) > 0:
            multiplier = float(self.pending_multiplier.get(player_id, 1.0) or 1.0)
            min_damage = int(round(min_damage * multiplier))
            max_damage = int(round(max_damage * multiplier))
        if bot_module._force_min_damage_active(self.active_effects, player_id):
            max_damage = max(0, int(min_damage))
        return max(0, int(min_damage)), max(0, int(max_damage))

    def skip_stunned_turn_if_needed(self) -> CombatStepResult | None:
        actor_id = self.current_turn
        if not self.stunned_next_turn.get(actor_id, False):
            return None
        self.stunned_next_turn[actor_id] = False
        airborne_owner_id = self.other_player(actor_id)
        if self.airborne_pending_landing.get(airborne_owner_id):
            self._consume_airborne_evade_marker(airborne_owner_id)
        self.current_turn = airborne_owner_id
        self.reduce_cooldowns(self.current_turn)
        return CombatStepResult(
            actor_id=actor_id,
            defender_id=airborne_owner_id,
            attack_index=None,
            attack_name="Zug ausgelassen",
            damage=0,
            attacker_hp=self._hp_for(actor_id),
            defender_hp=self._hp_for(airborne_owner_id),
            skipped=True,
        )

    def is_finished(self) -> bool:
        return self._hp_for(self.player1_id) <= 0 or self._hp_for(self.player2_id) <= 0

    def is_draw(self) -> bool:
        return self._hp_for(self.player1_id) <= 0 and self._hp_for(self.player2_id) <= 0

    def winner_id(self) -> int | None:
        if self.is_draw():
            return None
        if self._hp_for(self.player1_id) <= 0:
            return self.player2_id
        if self._hp_for(self.player2_id) <= 0:
            return self.player1_id
        return None

    def _apply_attack_effects(
        self,
        *,
        actor_id: int,
        defender_id: int,
        attack_index: int,
        attack: dict[str, Any],
        attack_name: str,
        effect_events: list[str],
        attack_hits_enemy: bool,
        is_forced_landing: bool,
        last_enemy_special_entry: dict[str, object] | None,
        reset_cooldown_index: int | None,
    ) -> int | None:
        raw_effects = attack.get("effects", [])
        effects = raw_effects if isinstance(raw_effects, list) else []
        burning_duration_for_dynamic_cooldown: int | None = None
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            chance = 0.7 if effect.get("type") == "confusion" else (bot_module._maybe_float(effect.get("chance", 1.0)) or 1.0)
            if random.random() >= chance:
                continue
            target = effect.get("target", "enemy")
            target_id = actor_id if target == "self" else defender_id
            eff_type = str(effect.get("type") or "").strip().lower()
            if target != "self" and not attack_hits_enemy and eff_type not in {"stun"}:
                continue
            if target != "self" and bot_module._should_block_negative_effect(self.active_effects, target_id, eff_type):
                if bot_module._consume_status_immunity(self.active_effects, target_id):
                    self._append_effect_event(effect_events, "Status-Immunitaet verhindert den negativen Effekt.")
                continue
            if eff_type == "stun" and bot_module._shield_has_stun_immunity(self.active_effects, target_id):
                self._append_effect_event(effect_events, "Betaeubung abgewehrt: Schild schuetzt vor Stun.")
                continue
            if bot_module._apply_word_runtime_effect(self, effect_events, eff_type=eff_type, target_id=target_id, attack_name=attack_name):
                continue
            if eff_type == "stealth":
                self.grant_stealth(target_id)
            elif bot_module._is_dot_effect_type(eff_type):
                dot_multiplier = bot_module._consume_burn_multiplier(self.active_effects, actor_id) if eff_type == "burning" else 1.0
                duration, _damage = bot_module._append_dot_effect(
                    self.active_effects,
                    target_id=target_id,
                    attacker_id=actor_id,
                    effect_type=eff_type,
                    duration=effect.get("duration"),
                    damage=effect.get("damage"),
                    damage_multiplier=dot_multiplier,
                )
                if attack.get("cooldown_from_burning_plus") is not None:
                    prev_duration = burning_duration_for_dynamic_cooldown or 0
                    burning_duration_for_dynamic_cooldown = max(prev_duration, duration)
            elif eff_type == "confusion":
                self.set_confusion(target_id, actor_id)
            elif eff_type == "stun":
                self.stunned_next_turn[target_id] = True
            elif eff_type == "damage_boost":
                amount = bot_module._effect_amount(effect, "amount", 0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_flat_bonus[target_id] = max(self.pending_flat_bonus.get(target_id, 0), amount)
                self.pending_flat_bonus_uses[target_id] = max(self.pending_flat_bonus_uses.get(target_id, 0), uses)
            elif eff_type == "attack_heal":
                uses = int(effect.get("uses", 1) or 1)
                bot_module._append_active_effect(self.active_effects, target_id, "attack_heal", actor_id, amount=effect.get("amount", 0), uses=uses, source=attack_name)
            elif eff_type == "damage_multiplier":
                mult = float(effect.get("multiplier", 1.0) or 1.0)
                uses = int(effect.get("uses", 1) or 1)
                self.pending_multiplier[target_id] = max(self.pending_multiplier.get(target_id, 1.0), mult)
                self.pending_multiplier_uses[target_id] = max(self.pending_multiplier_uses.get(target_id, 0), uses)
            elif eff_type == "capped_damage_multiplier":
                bot_module._append_active_effect(
                    self.active_effects,
                    target_id,
                    "capped_damage_multiplier",
                    actor_id,
                    multiplier=max(1.0, float(effect.get("multiplier", 1.0) or 1.0)),
                    max_bonus=effect.get("max_bonus", 0),
                    uses=max(1, int(effect.get("uses", 1) or 1)),
                    source=attack_name,
                )
            elif eff_type == "next_standard_damage_override":
                bot_module._append_active_effect(
                    self.active_effects,
                    target_id,
                    "next_standard_damage_override",
                    actor_id,
                    turns=max(1, int(effect.get("turns", 1) or 1)),
                    damage=effect.get("damage", 0),
                    source=attack_name,
                )
            elif eff_type == "force_max":
                uses = int(effect.get("uses", 1) or 1)
                self.force_max_next[target_id] = max(self.force_max_next.get(target_id, 0), uses)
            elif eff_type == "guaranteed_hit":
                uses = int(effect.get("uses", 1) or 1)
                self.guaranteed_hit_next[target_id] = max(self.guaranteed_hit_next.get(target_id, 0), uses)
            elif eff_type == "standard_lock":
                turns = max(1, int(effect.get("turns", 1) or 1))
                bot_module._append_active_effect(self.active_effects, target_id, "standard_lock", actor_id, turns=turns, source=attack_name)
            elif eff_type == "status_immunity":
                turns = max(1, int(effect.get("turns", 1) or 1))
                bot_module._append_active_effect(self.active_effects, target_id, "status_immunity", actor_id, turns=turns, source=attack_name)
            elif eff_type in {"enemy_attack_self_damage", "enemy_special_self_damage", "enemy_next_special_self_damage"}:
                turns = max(1, int(effect.get("turns", 1) or 1))
                amount = max(0, int(effect.get("amount", 0) or 0))
                bot_module._append_active_effect(self.active_effects, target_id, eff_type, actor_id, turns=turns, amount=amount, source=attack_name)
            elif eff_type == "disable_enemy_evade_and_block":
                turns = max(1, int(effect.get("turns", 1) or 1))
                bot_module._append_active_effect(self.active_effects, target_id, "disable_enemy_evade_and_block", actor_id, turns=turns, source=attack_name)
            elif eff_type == "shield":
                shield_hp = max(1, bot_module._effect_amount(effect, "hp", 1))
                existing_shield = bot_module._shield_entry(self.active_effects, target_id)
                if existing_shield is not None:
                    bot_module._remove_active_effect(self.active_effects, target_id, existing_shield)
                shield_fields: dict[str, object] = {"hp": shield_hp, "source": attack_name}
                if effect.get("break_counter") is not None:
                    shield_fields["break_counter"] = int(effect.get("break_counter", 0) or 0)
                if effect.get("stun_immunity") is not None:
                    shield_fields["stun_immunity"] = bool(effect.get("stun_immunity"))
                if effect.get("max_hits") is not None:
                    shield_fields["max_hits"] = int(effect.get("max_hits", 0) or 0)
                bot_module._append_active_effect(self.active_effects, target_id, "shield", actor_id, **shield_fields)
            elif eff_type == "increase_random_enemy_cooldown":
                target_attacks = self.attacks_for(target_id)
                chosen_idx, new_cd = bot_module._apply_random_enemy_cooldown_increase(
                    target_attacks,
                    self.attack_cooldowns[target_id],
                    amount=int(effect.get("amount", 1) or 1),
                )
                if chosen_idx is not None:
                    self.attack_cooldowns[target_id][chosen_idx] = new_cd
            elif eff_type == "increase_last_enemy_special_cooldown" and isinstance(last_enemy_special_entry, dict):
                last_index = bot_module._maybe_int(last_enemy_special_entry.get("attack_index", -1)) or -1
                if last_index >= 0:
                    bonus = max(1, bot_module._maybe_int(effect.get("amount", 1)) or 1)
                    self.attack_cooldowns[defender_id][last_index] = max(0, int(self.attack_cooldowns[defender_id].get(last_index, 0) or 0)) + bonus
            elif eff_type == "incoming_damage_bonus":
                turns = max(1, int(effect.get("turns", 1) or 1))
                amount = max(0, bot_module._effect_amount(effect, "amount", 0))
                bot_module._append_active_effect(self.active_effects, target_id, "incoming_damage_bonus", actor_id, turns=turns, amount=amount, source=attack_name)
            elif eff_type == "interrupt_enemy_standard_or_heal_self":
                turns = max(1, int(effect.get("turns", 1) or 1))
                bot_module._append_active_effect(self.active_effects, target_id, "interrupt_enemy_standard_or_heal_self", actor_id, turns=turns, damage=int(effect.get("damage", 0) or 0), heal=int(effect.get("heal", 0) or 0), source=attack_name)
            elif eff_type == "burn_multiplier":
                bot_module._append_active_effect(self.active_effects, target_id, "burn_multiplier", actor_id, uses=max(1, int(effect.get("uses", 1) or 1)), multiplier=max(1.0, float(effect.get("multiplier", 1.0) or 1.0)), turns=1, source=attack_name)
            elif eff_type == "reset_own_cooldown" and reset_cooldown_index is not None:
                self.attack_cooldowns[actor_id].pop(reset_cooldown_index, None)
            elif eff_type == "heal_curse":
                turns = max(1, int(effect.get("turns", 1) or 1))
                bot_module._append_active_effect(self.active_effects, target_id, "heal_curse", actor_id, turns=turns, damage=int(effect.get("damage", effect.get("amount", 0)) or 0), source=attack_name)
            elif eff_type == "next_attack_flat_penalty":
                turns = max(1, int(effect.get("turns", 1) or 1))
                bot_module._append_active_effect(self.active_effects, target_id, "next_attack_flat_penalty", actor_id, turns=turns, amount=effect.get("amount", 0), source=attack_name)
            elif eff_type == "enemy_force_min_damage":
                turns = max(1, int(effect.get("turns", 1) or 1))
                bot_module._append_active_effect(self.active_effects, target_id, "enemy_force_min_damage", actor_id, turns=turns, source=attack_name)
            elif eff_type == "reactive_evolution":
                if bot_module._find_active_effect(self.active_effects, target_id, "reactive_evolution") is None:
                    bot_module._append_active_effect(self.active_effects, target_id, "reactive_evolution", actor_id, amount=int(effect.get("amount", 0) or 0), max_stacks=int(effect.get("max_stacks", 1) or 1), stacks=0, source=attack_name)
            elif eff_type == "disable_enemy_heal_if_bleeding":
                turns = max(1, int(effect.get("turns", 1) or 1))
                bot_module._append_active_effect(self.active_effects, target_id, "disable_enemy_heal_if_bleeding", actor_id, turns=turns, source=attack_name)
            elif eff_type == "heal_from_target_dot":
                dot_type = str(effect.get("dot_type") or "bleeding").strip().lower()
                heal_amount = bot_module._sum_target_dot_damage(self.active_effects, target_id, dot_type)
                self.heal_player(actor_id, heal_amount)
            elif eff_type == "damage_reduction":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, percent=percent, turns=turns, source=attack_name)
            elif eff_type == "damage_reduction_sequence":
                sequence = effect.get("sequence", [])
                if isinstance(sequence, list):
                    for pct in sequence:
                        self.queue_incoming_modifier(target_id, percent=float(pct or 0.0), turns=1, source=attack_name)
            elif eff_type == "damage_reduction_flat":
                amount = effect.get("amount", 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_incoming_modifier(target_id, flat=amount, turns=turns, source=attack_name)
            elif eff_type == "enemy_next_attack_reduction_percent":
                percent = float(effect.get("percent", 0.0) or 0.0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, percent=percent, turns=turns, source=attack_name)
            elif eff_type == "enemy_next_attack_reduction_flat":
                amount = effect.get("amount", 0)
                turns = int(effect.get("turns", 1) or 1)
                self.queue_outgoing_attack_modifier(target_id, flat=amount, turns=turns, source=attack_name)
            elif eff_type == "reflect":
                reduce_percent = float(effect.get("reduce_percent", 0.0) or 0.0)
                reflect_ratio = float(effect.get("reflect_ratio", 0.0) or 0.0)
                reflect_flat = effect.get("flat", 0)
                self.queue_incoming_modifier(target_id, percent=reduce_percent, reflect=reflect_ratio, flat=0, turns=1, source=attack_name)
                if self.incoming_modifiers.get(target_id):
                    self.incoming_modifiers[target_id][-1]["reflect_flat"] = reflect_flat
            elif eff_type == "absorb_store":
                percent = float(effect.get("percent", 0.0) or 0.0)
                max_store = effect.get("max_store")
                self.queue_incoming_modifier(target_id, percent=percent, store_ratio=1.0, max_store=(int(max_store) if max_store is not None else None), turns=1, source=attack_name)
            elif eff_type == "cap_damage":
                cap_setting = effect.get("max_damage", 0)
                if str(cap_setting).strip().lower() == "attack_min":
                    self.queue_incoming_modifier(target_id, cap="attack_min", turns=1, source=attack_name)
                else:
                    self.queue_incoming_modifier(target_id, cap=cap_setting, turns=1, source=attack_name)
            elif eff_type == "evade":
                counter = effect.get("counter", 0)
                self.queue_incoming_modifier(target_id, evade=True, counter=counter, turns=1, source=attack_name)
            elif eff_type == "special_lock":
                turns = max(1, int(effect.get("turns", 1) or 1))
                self.special_lock_next_turn[target_id] = max(self.special_lock_next_turn.get(target_id, 0), turns)
            elif eff_type == "blind":
                miss_chance = float(effect.get("miss_chance", 0.5) or 0.5)
                self.blind_next_attack[target_id] = max(self.blind_next_attack.get(target_id, 0.0), miss_chance)
            elif eff_type == "regen":
                turns = int(effect.get("turns", 1) or 1)
                heal = effect.get("heal", 0)
                self.active_effects[target_id].append({"type": "regen", "duration": turns, "heal": heal, "applier": actor_id})
            elif eff_type == "heal":
                heal_data_effect = effect.get("amount", 0)
                heal_amount = bot_module._random_int_from_range(heal_data_effect)
                self.heal_player(target_id, heal_amount)
            elif eff_type == "mix_heal_or_max":
                bot_module._apply_mix_heal_or_max_effect(self, target_id, effect, effect_events)
            elif eff_type == "delayed_defense_after_next_attack":
                defense_mode = str(effect.get("defense", "")).strip().lower()
                counter = effect.get("counter", 0)
                self.queue_delayed_defense(target_id, defense_mode, counter=counter, source=attack_name)
            elif eff_type == "airborne_two_phase":
                self.start_airborne_two_phase(target_id, effect.get("landing_damage", [20, 40]), effect_events, landing_attack=(effect.get("landing_attack") if isinstance(effect.get("landing_attack"), dict) else None), source_attack_index=attack_index if not is_forced_landing else None, cooldown_turns=bot_module._maybe_int(attack.get("cooldown_turns", 0)) or 0)

        if attack_hits_enemy:
            for effect in effects:
                if str(effect.get("type") or "").strip().lower() != "finisher_below_hp":
                    continue
                threshold = max(0, int(effect.get("threshold", 0) or 0))
                if self._hp_for(defender_id) <= threshold:
                    self._hp_by_player[defender_id] = 0
                    break
        return burning_duration_for_dynamic_cooldown

    def perform_turn(self, attack_index: int) -> CombatStepResult:
        if self.is_finished():
            winner_id = self.winner_id()
            loser_id = self.other_player(winner_id) if winner_id is not None else None
            return CombatStepResult(
                actor_id=self.current_turn,
                defender_id=self.other_player(self.current_turn),
                attack_index=None,
                attack_name="Kampf beendet",
                damage=0,
                attacker_hp=self._hp_for(self.current_turn),
                defender_hp=self._hp_for(self.other_player(self.current_turn)),
                winner_id=winner_id,
                loser_id=loser_id,
            )

        skipped = self.skip_stunned_turn_if_needed()
        if skipped is not None:
            return skipped

        actor_id = self.current_turn
        defender_id = self.other_player(actor_id)
        effect_events: list[str] = []
        legal = self.legal_attack_indices(actor_id)
        if attack_index not in legal:
            raise ValueError(f"Illegal attack index {attack_index} for player {actor_id}; legal={legal}")

        selection = self.preview_attack_selection(actor_id, attack_index)
        if selection.is_forced_landing:
            self.resolve_forced_landing_if_due(actor_id, effect_events)

        current_card = self.card_for(actor_id)
        attacks = self.attacks_for(actor_id)
        standard_idx = selection.standard_index
        attack = selection.attack
        base_damage = selection.base_damage
        attack_name = selection.attack_name
        is_reload_action = selection.is_reload_action
        is_forced_landing = selection.is_forced_landing

        self.apply_regen_tick(actor_id)
        _pre_burn_total, dot_tick_events = bot_module._apply_dot_ticks_for_applier(
            self.active_effects,
            target_id=defender_id,
            applier_id=actor_id,
            damage_callback=(lambda amount: self._apply_non_heal_damage(defender_id, amount)),
        )
        for event_text in dot_tick_events:
            self._append_effect_event(effect_events, event_text)

        damage_buff = 0
        attacker_hp = self._hp_for(actor_id)
        attacker_max_hp = self._max_hp_for(actor_id)
        defender_hp = self._hp_for(defender_id)
        defender_max_hp = self._max_hp_for(defender_id)

        conditional_self_pct = bot_module._maybe_float(attack.get("bonus_if_self_hp_below_pct"))
        conditional_self_bonus = bot_module._maybe_int(attack.get("bonus_damage_if_condition", 0)) or 0
        if conditional_self_pct is not None and attacker_hp <= int(attacker_max_hp * conditional_self_pct):
            damage_buff += conditional_self_bonus

        conditional_enemy_triggered = False
        conditional_enemy_pct = bot_module._maybe_float(attack.get("conditional_enemy_hp_below_pct"))
        if conditional_enemy_pct is not None and defender_hp <= int(defender_max_hp * conditional_enemy_pct):
            conditional_enemy_triggered = True
            base_damage = bot_module._coerce_damage_input(attack.get("damage_if_condition"), default=0)

        if attack.get("add_absorbed_damage"):
            absorbed_bonus = int(self.absorbed_damage.get(actor_id, 0) or 0)
            damage_buff += absorbed_bonus
            self.absorbed_damage[actor_id] = 0

        attack_penalty = bot_module._consume_attack_penalty(self.active_effects, actor_id)
        if attack_penalty > 0:
            damage_buff -= attack_penalty

        effective_attack = dict(attack)
        effective_attack["damage"] = base_damage
        is_damaging_attack = bot_module._attack_has_direct_damage(effective_attack)
        attack_multiplier = 1.0
        applied_flat_bonus_now = 0
        force_max_damage = False
        if is_damaging_attack:
            if self.pending_flat_bonus_uses.get(actor_id, 0) > 0:
                flat_bonus_now = int(self.pending_flat_bonus.get(actor_id, 0) or 0)
                damage_buff += flat_bonus_now
                applied_flat_bonus_now = max(0, flat_bonus_now)
                self.pending_flat_bonus_uses[actor_id] -= 1
                if self.pending_flat_bonus_uses[actor_id] <= 0:
                    self.pending_flat_bonus[actor_id] = 0
            if self.pending_multiplier_uses.get(actor_id, 0) > 0:
                attack_multiplier = float(self.pending_multiplier.get(actor_id, 1.0) or 1.0)
                self.pending_multiplier_uses[actor_id] -= 1
                if self.pending_multiplier_uses[actor_id] <= 0:
                    self.pending_multiplier[actor_id] = 1.0
            if self.force_max_next.get(actor_id, 0) > 0:
                force_max_damage = True
                self.force_max_next[actor_id] -= 1

        guaranteed_hit = bool(attack.get("guaranteed_hit_if_condition") and conditional_enemy_triggered)
        force_min_damage = bot_module._force_min_damage_active(self.active_effects, actor_id)
        attack_cancelled_by_heal_curse = False
        heal_curse_effect = bot_module._find_active_effect(self.active_effects, actor_id, "heal_curse")
        if (not is_reload_action) and heal_curse_effect is not None and bot_module._attack_has_heal_component(attack):
            attack_cancelled_by_heal_curse = True
            curse_damage = max(0, bot_module._effect_int(heal_curse_effect, "damage", 0))
            turns_left = max(0, bot_module._effect_int(heal_curse_effect, "turns", 1) - 1)
            heal_curse_effect["turns"] = turns_left
            if turns_left <= 0:
                bot_module._remove_active_effect(self.active_effects, actor_id, heal_curse_effect)
            if curse_damage > 0:
                self._apply_non_heal_damage_with_event(effect_events, actor_id, curse_damage, source=str(heal_curse_effect.get("source") or "Hex-Fluch"), self_damage=True)

        _action_type = bot_module._attack_kind_label(
            attack,
            attacks=attacks,
            attack_index=attack_index,
            is_reload_action=is_reload_action,
            is_forced_landing=is_forced_landing,
        )
        miss_reason: str | None = None
        attack_hits_enemy = True
        confusion_self_damage = 0
        actual_damage = 0
        is_critical = False
        min_damage = 0

        if is_reload_action:
            attack_hits_enemy = False
            self.set_reload_needed(actor_id, attack_index, False)
        elif attack_cancelled_by_heal_curse:
            attack_hits_enemy = False
        else:
            defender_has_stealth = self.has_stealth(defender_id)
            guaranteed_hit = guaranteed_hit or self.consume_guaranteed_hit(actor_id)
            if guaranteed_hit:
                self.blind_next_attack[actor_id] = 0.0
                self.consume_confusion_if_any(actor_id)

            current_attack_profile = dict(attack)
            current_attack_profile["damage"] = base_damage
            _min_threshold_damage, max_damage_threshold = bot_module._attack_total_damage_range(current_attack_profile, max_only_bonus=0, flat_bonus=damage_buff)
            blind_chance = float(self.blind_next_attack.get(actor_id, 0.0) or 0.0)
            blind_miss = False
            if blind_chance > 0:
                self.blind_next_attack[actor_id] = 0.0
                blind_miss = random.random() < blind_chance

            if blind_miss:
                miss_reason = f"durch Blendung ({int(round(blind_chance * 100))}% Verfehlchance)"
                attack_hits_enemy = False
                if self.confused_next_turn.get(actor_id, False):
                    self.consume_confusion_if_any(actor_id)
            elif self.confused_next_turn.get(actor_id, False):
                if random.random() < 0.77:
                    confusion_self_damage = random.randint(15, 20) if max_damage_threshold <= 100 else random.randint(40, 60)
                    self._apply_non_heal_damage_with_event(effect_events, actor_id, confusion_self_damage, source="Verwirrung", self_damage=True)
                    miss_reason = "durch Verwirrung, stattdessen Selbsttreffer"
                    attack_hits_enemy = False
                else:
                    actual_damage, is_critical, min_damage, _max_damage = self.roll_attack_damage(attack, base_damage, damage_buff, attack_multiplier, force_max_damage, guaranteed_hit)
                    if force_min_damage:
                        actual_damage = min_damage
                        is_critical = False
                        bot_module._consume_force_min_damage(self.active_effects, actor_id)
                    self._append_multi_hit_roll_event(effect_events)
                    if defender_has_stealth and not guaranteed_hit and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable")):
                        actual_damage = 0
                        is_critical = False
                        attack_hits_enemy = False
                        miss_reason = "durch Tarnung"
                        self.consume_stealth(defender_id)
                    elif defender_has_stealth and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable")):
                        self.consume_stealth(defender_id)
                self.consume_confusion_if_any(actor_id)
            else:
                actual_damage, is_critical, min_damage, _max_damage = self.roll_attack_damage(attack, base_damage, damage_buff, attack_multiplier, force_max_damage, guaranteed_hit)
                if force_min_damage:
                    actual_damage = min_damage
                    is_critical = False
                    bot_module._consume_force_min_damage(self.active_effects, actor_id)
                self._append_multi_hit_roll_event(effect_events)
                if defender_has_stealth and not guaranteed_hit and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable")):
                    actual_damage = 0
                    is_critical = False
                    attack_hits_enemy = False
                    miss_reason = "durch Tarnung"
                    self.consume_stealth(defender_id)
                elif defender_has_stealth and not bool(attack.get("ignore_defense") or attack.get("ignore_shield") or attack.get("unblockable")):
                    self.consume_stealth(defender_id)

            if attack_hits_enemy and actual_damage > 0:
                before_override = int(actual_damage)
                actual_damage, override_effect = bot_module._consume_next_standard_damage_override(
                    self.active_effects,
                    actor_id,
                    attack_index=attack_index,
                    standard_index=standard_idx,
                    current_damage=actual_damage,
                )
                if override_effect is not None and actual_damage != before_override:
                    source = str(override_effect.get("source") or "Effekt")
                    self._append_effect_event(effect_events, f"{source}: Standardangriff {before_override} -> {actual_damage} Schaden.")
                before_capped = int(actual_damage)
                actual_damage, capped_bonus, capped_effect = bot_module._consume_capped_damage_multiplier(self.active_effects, actor_id, actual_damage)
                if capped_effect is not None and capped_bonus > 0:
                    source = str(capped_effect.get("source") or "Geheimakte")
                    self._append_effect_event(effect_events, f"{source}: Schaden {before_capped} -> {actual_damage} (+{capped_bonus}, max. +{bot_module._effect_amount_label(capped_effect.get('max_bonus', 0))}).")
                boost_text = bot_module._boosted_damage_effect_text(actual_damage, attack_multiplier, applied_flat_bonus_now)
                if boost_text:
                    self._append_effect_event(effect_events, boost_text)
                defender_hp_before = self._hp_for(defender_id)
                reduced_damage, overflow_self_damage, outgoing_modifier = self.apply_outgoing_attack_modifiers(actor_id, actual_damage)
                if reduced_damage != actual_damage:
                    modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                    self._append_effect_event(effect_events, bot_module._outgoing_reduction_effect_text(int(actual_damage), int(reduced_damage), source=modifier_source or None))
                    actual_damage = reduced_damage
                if overflow_self_damage > 0:
                    modifier_source = str((outgoing_modifier or {}).get("source") or "").strip()
                    self._apply_non_heal_damage_with_event(effect_events, actor_id, overflow_self_damage, source=bot_module._overflow_recoil_source(modifier_source or None), self_damage=True)
                if actual_damage <= 0:
                    is_critical = False
                incoming_bonus = bot_module._incoming_damage_bonus(self.active_effects, defender_id)
                if incoming_bonus > 0 and actual_damage > 0:
                    actual_damage += incoming_bonus

                bypass_all_defense = bool(
                    attack.get("ignore_defense")
                    or attack.get("ignore_shield")
                    or attack.get("unblockable")
                    or bot_module._find_active_effect(self.active_effects, defender_id, "disable_enemy_evade_and_block")
                )
                final_damage, reflected_damage, dodged, counter_damage, _incoming_modifier = self.resolve_incoming_modifiers(
                    defender_id,
                    actual_damage,
                    ignore_evade=(guaranteed_hit and not self.has_airborne(defender_id)),
                    ignore_all_defense=bypass_all_defense,
                    incoming_min_damage=min_damage,
                )
                if dodged:
                    miss_reason = "durch Ausweichen"
                    actual_damage = 0
                    attack_hits_enemy = False
                    is_critical = False
                else:
                    actual_damage = max(0, int(final_damage))
                    actual_damage, _reactive_reduction = bot_module._apply_reactive_evolution_reduction(self.active_effects, defender_id, actual_damage)
                    shield_break_counter = 0
                    if actual_damage > 0 and not bypass_all_defense:
                        actual_damage, shield_break_counter = bot_module._consume_shield_damage(self.active_effects, defender_id, actual_damage)
                    if actual_damage > 0:
                        self._apply_non_heal_damage(defender_id, actual_damage)
                    else:
                        is_critical = False
                    if shield_break_counter > 0:
                        self._apply_non_heal_damage_with_event(effect_events, actor_id, shield_break_counter, source="Schildbruch", self_damage=False)
                if reflected_damage > 0:
                    self._apply_non_heal_damage_with_event(effect_events, actor_id, reflected_damage, source="Reflexions-Rueckschaden", self_damage=False)
                if counter_damage > 0:
                    self._apply_non_heal_damage_with_event(effect_events, actor_id, counter_damage, source="Konter-Rueckschaden", self_damage=False)
                self._guard_non_heal_damage_result(defender_id, defender_hp_before, "headless_player_attack")
                hit_heal, heal_effect = bot_module._consume_attack_heal(self.active_effects, actor_id)
                if hit_heal > 0:
                    healed_now = self.heal_player(actor_id, hit_heal)
                    if healed_now > 0:
                        self._append_effect_event(effect_events, f"{str((heal_effect or {}).get('source') or 'Trefferheilung')}: Treffer heilt {healed_now} HP.")
            if not attack_hits_enemy or int(actual_damage or 0) <= 0:
                is_critical = False

        self_damage_value = bot_module._resolve_self_damage_value(attack.get("self_damage", 0))
        if self_damage_value > 0:
            self._apply_non_heal_damage_with_event(effect_events, actor_id, self_damage_value, source=f"{attack_name} / Rueckstoss", self_damage=True)

        trap_self_damage = bot_module._consume_attack_self_damage_effect(
            self.active_effects,
            actor_id,
            special_attack=bool((not is_forced_landing) and attack_index != standard_idx),
        )
        if trap_self_damage > 0:
            self._apply_non_heal_damage_with_event(effect_events, actor_id, trap_self_damage, source="Vorbereiteter Gegeneffekt", self_damage=True)

        heal_data = attack.get("heal")
        healing_disabled = bool(bot_module._find_active_effect(self.active_effects, actor_id, "disable_enemy_heal_if_bleeding")) and bot_module._sum_target_dot_damage(self.active_effects, actor_id, "bleeding") > 0
        if heal_data is not None and not healing_disabled:
            heal_chance = bot_module._maybe_float(attack.get("heal_chance", 1.0)) or 1.0
            if random.random() <= heal_chance:
                heal_amount = bot_module._random_int_from_range(heal_data)
                self.heal_player(actor_id, heal_amount)

        lifesteal_ratio = bot_module._maybe_float(attack.get("lifesteal_ratio", 0.0)) or 0.0
        if lifesteal_ratio > 0 and attack_hits_enemy and actual_damage > 0 and not healing_disabled:
            self.heal_player(actor_id, int(round(actual_damage * lifesteal_ratio)))

        self.round_counter += 1
        if not is_reload_action:
            self.activate_delayed_defense_after_attack(actor_id, effect_events, attack_landed=bool(attack_hits_enemy and int(actual_damage or 0) > 0))

        burning_duration_for_dynamic_cooldown = self._apply_attack_effects(
            actor_id=actor_id,
            defender_id=defender_id,
            attack_index=attack_index,
            attack=attack,
            attack_name=attack_name,
            effect_events=effect_events,
            attack_hits_enemy=attack_hits_enemy and int(actual_damage or 0) > 0,
            is_forced_landing=is_forced_landing,
            last_enemy_special_entry=selection.last_enemy_special_entry,
            reset_cooldown_index=selection.reset_cooldown_index,
        )

        bot_module._record_last_special_attack(
            self.last_special_attack,
            actor_id=actor_id,
            attack_index=attack_index,
            attacks=attacks,
            attack=attack,
            card_name=str(current_card.get("name") or self._card_names_by_player[actor_id]),
            attack_name=str(attack_name),
            is_reload_action=is_reload_action or attack_cancelled_by_heal_curse,
            is_forced_landing=is_forced_landing,
        )
        if self.special_lock_next_turn.get(actor_id, 0) > 0:
            self.special_lock_next_turn[actor_id] = max(0, self.special_lock_next_turn.get(actor_id, 0) - 1)

        if (not is_forced_landing) and (not attack_cancelled_by_heal_curse):
            if not is_reload_action and attack.get("requires_reload"):
                self.set_reload_needed(actor_id, attack_index, True)
            dynamic_cooldown_turns = bot_module._resolve_dynamic_cooldown_from_burning(attack, burning_duration_for_dynamic_cooldown)
            custom_cooldown_turns = bot_module._resolve_final_damage_cooldown_turns(attack, actual_damage)
            starts_after_landing = bot_module._starts_cooldown_after_landing(attack)
            if dynamic_cooldown_turns > 0:
                current_cd = self.attack_cooldowns[actor_id].get(attack_index, 0)
                self.attack_cooldowns[actor_id][attack_index] = max(current_cd, dynamic_cooldown_turns)
            elif (not starts_after_landing) and custom_cooldown_turns > 0:
                current_cd = self.attack_cooldowns[actor_id].get(attack_index, 0)
                self.attack_cooldowns[actor_id][attack_index] = max(current_cd, custom_cooldown_turns)
            elif self.is_strong_attack(base_damage, damage_buff):
                self.start_attack_cooldown(actor_id, attack_index)
        else:
            landing_cd_index = bot_module._maybe_int(attack.get("cooldown_attack_index"))
            landing_cd_turns = bot_module._maybe_int(attack.get("cooldown_turns", 0)) or 0
            if landing_cd_index is not None and landing_cd_index >= 0 and landing_cd_turns > 0:
                current_cd = self.attack_cooldowns[actor_id].get(landing_cd_index, 0)
                self.attack_cooldowns[actor_id][landing_cd_index] = max(current_cd, landing_cd_turns)

        if self.airborne_pending_landing.get(defender_id):
            self._consume_airborne_evade_marker(defender_id)

        winner_id = self.winner_id()
        loser_id = self.other_player(winner_id) if winner_id is not None else None
        if not self.is_finished():
            bot_module._consume_turn_end_decay_effects(self.active_effects, actor_id)
            self.current_turn = defender_id
            self.reduce_cooldowns(self.current_turn)

        return CombatStepResult(
            actor_id=actor_id,
            defender_id=defender_id,
            attack_index=attack_index,
            attack_name=attack_name,
            damage=max(0, int(actual_damage or 0)),
            attacker_hp=self._hp_for(actor_id),
            defender_hp=self._hp_for(defender_id),
            winner_id=winner_id,
            loser_id=loser_id,
            events=effect_events if self.debug else [],
        )
