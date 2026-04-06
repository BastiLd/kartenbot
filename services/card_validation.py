ALLOWED_EFFECT_TYPES = frozenset(
    {
        "absorb_store",
        "airborne_two_phase",
        "blind",
        "burning",
        "bleeding",
        "cap_damage",
        "damage_boost",
        "damage_reduction",
        "damage_reduction_flat",
        "damage_multiplier",
        "damage_reduction_sequence",
        "delayed_defense_after_next_attack",
        "enemy_next_attack_reduction_flat",
        "enemy_next_attack_reduction_percent",
        "evade",
        "force_max",
        "guaranteed_hit",
        "heal",
        "heal_curse",
        "heal_from_target_dot",
        "increase_last_enemy_special_cooldown",
        "increase_random_enemy_cooldown",
        "incoming_damage_bonus",
        "interrupt_enemy_standard_or_heal_self",
        "mix_heal_or_max",
        "next_attack_flat_penalty",
        "reset_own_cooldown",
        "enemy_force_min_damage",
        "enemy_next_special_self_damage",
        "enemy_special_self_damage",
        "enemy_attack_self_damage",
        "shield",
        "standard_lock",
        "status_immunity",
        "disable_enemy_evade_and_block",
        "burn_multiplier",
        "copy_last_enemy_special",
        "reactive_evolution",
        "finisher_below_hp",
        "disable_enemy_heal_if_bleeding",
        "poison",
        "reflect",
        "regen",
        "special_lock",
        "stun",
    }
)
ALLOWED_RARITY_KEYS = frozenset({"common", "rare", "epic", "legendary"})
ALLOWED_TARGETS = frozenset({"self", "enemy"})
ALLOWED_DEFENSES = frozenset({"stealth", "evade"})
ALLOWED_BUTTON_STYLES = frozenset({"primary", "secondary", "success", "danger"})
ALLOWED_CAP_DAMAGE_TOKENS = frozenset({"attack_min"})

RARITY_ALIASES = {
    "common": "common",
    "normal": "common",
    "gewöhnlich": "common",
    "gewoehnlich": "common",
    "gewohnlich": "common",
    "rare": "rare",
    "selten": "rare",
    "epic": "epic",
    "episch": "epic",
    "legendary": "legendary",
    "legendär": "legendary",
    "legendaer": "legendary",
    "legendar": "legendary",
}


def _normalize_label(value) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def normalize_rarity_key(value) -> str:
    normalized = _normalize_label(value)
    return RARITY_ALIASES.get(normalized, normalized)


def summarize_validation_issues(issues: list[str], *, max_items: int | None = 20) -> str:
    if not issues:
        return ""
    if max_items is None:
        items = issues
    else:
        limit = max(0, int(max_items))
        items = issues[:limit]
    preview = "\n".join(items)
    more = len(issues) - len(items)
    if more > 0:
        preview += f"\n... +{more} weitere"
    return preview


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)


def _is_non_empty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_int(value, path: str, label: str, issues: list[str], *, minimum: int | None = None) -> bool:
    if not _is_int(value):
        issues.append(f"{path}: {label} ist kein int")
        return False
    if minimum is not None and int(value) < minimum:
        issues.append(f"{path}: {label} ist kleiner als {minimum}")
        return False
    return True


def _validate_number(
    value,
    path: str,
    label: str,
    issues: list[str],
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> bool:
    if not _is_number(value):
        issues.append(f"{path}: {label} ist keine Zahl")
        return False
    numeric = float(value)
    if minimum is not None and numeric < minimum:
        issues.append(f"{path}: {label} ist kleiner als {minimum}")
        return False
    if maximum is not None and numeric > maximum:
        issues.append(f"{path}: {label} ist groesser als {maximum}")
        return False
    return True


def _validate_probability(value, path: str, label: str, issues: list[str]) -> bool:
    return _validate_number(value, path, label, issues, minimum=0.0, maximum=1.0)


def _validate_amount_range(value, path: str, label: str, issues: list[str], *, minimum: int = 0) -> bool:
    if _is_int(value):
        if int(value) < minimum:
            issues.append(f"{path}: {label} ist kleiner als {minimum}")
            return False
        return True
    if not isinstance(value, list) or len(value) != 2 or not all(_is_int(part) for part in value):
        issues.append(f"{path}: {label} ist kein int oder [min, max]")
        return False
    start, end = int(value[0]), int(value[1])
    if start < minimum or end < minimum or start > end:
        issues.append(f"{path}: {label} range ist ungueltig")
        return False
    return True


def _validate_non_empty_string(value, path: str, label: str, issues: list[str]) -> bool:
    if not _is_non_empty_string(value):
        issues.append(f"{path}: fehlt {label}")
        return False
    return True


def _validate_effect(effect, path: str, issues: list[str]) -> None:
    if not isinstance(effect, dict):
        issues.append(f"{path}: effect ist kein dict")
        return

    effect_type = str(effect.get("type") or "").strip()
    if not effect_type:
        issues.append(f"{path}: effect type fehlt")
        return
    if effect_type not in ALLOWED_EFFECT_TYPES:
        issues.append(f"{path}: unbekannter effect type '{effect_type}'")
        return

    target = effect.get("target")
    if target is not None and target not in ALLOWED_TARGETS:
        issues.append(f"{path}: target '{target}' ist ungueltig")

    chance = effect.get("chance")
    if chance is not None:
        _validate_probability(chance, path, "chance", issues)

    if effect_type in {"burning", "poison", "bleeding"}:
        _validate_amount_range(effect.get("duration"), path, "duration", issues, minimum=1)
        _validate_int(effect.get("damage"), path, "damage", issues, minimum=1)
    elif effect_type == "damage_multiplier":
        _validate_number(effect.get("multiplier"), path, "multiplier", issues, minimum=0.0)
        _validate_int(effect.get("uses"), path, "uses", issues, minimum=1)
    elif effect_type == "damage_boost":
        _validate_int(effect.get("amount"), path, "amount", issues, minimum=1)
        _validate_int(effect.get("uses"), path, "uses", issues, minimum=1)
    elif effect_type == "damage_reduction_sequence":
        sequence = effect.get("sequence")
        if not isinstance(sequence, list) or not sequence:
            issues.append(f"{path}: sequence ist keine nicht-leere Liste")
        else:
            for index, value in enumerate(sequence, start=1):
                _validate_probability(value, f"{path}.sequence[{index}]", "value", issues)
    elif effect_type == "delayed_defense_after_next_attack":
        defense = effect.get("defense")
        if defense not in ALLOWED_DEFENSES:
            issues.append(f"{path}: defense '{defense}' ist ungueltig")
    elif effect_type == "enemy_next_attack_reduction_flat":
        _validate_int(effect.get("amount"), path, "amount", issues, minimum=0)
        _validate_int(effect.get("turns"), path, "turns", issues, minimum=1)
    elif effect_type == "enemy_next_attack_reduction_percent":
        _validate_probability(effect.get("percent"), path, "percent", issues)
        _validate_int(effect.get("turns"), path, "turns", issues, minimum=1)
    elif effect_type == "evade":
        _validate_int(effect.get("counter"), path, "counter", issues, minimum=0)
    elif effect_type == "guaranteed_hit":
        _validate_int(effect.get("uses"), path, "uses", issues, minimum=1)
    elif effect_type == "mix_heal_or_max":
        _validate_amount_range(effect.get("heal"), path, "heal", issues, minimum=1)
    elif effect_type == "reflect":
        _validate_probability(effect.get("reduce_percent"), path, "reduce_percent", issues)
        _validate_number(effect.get("reflect_ratio"), path, "reflect_ratio", issues, minimum=0.0)
    elif effect_type == "regen":
        _validate_int(effect.get("turns"), path, "turns", issues, minimum=1)
        _validate_amount_range(effect.get("heal"), path, "heal", issues, minimum=1)
    elif effect_type == "blind":
        _validate_probability(effect.get("miss_chance"), path, "miss_chance", issues)
    elif effect_type == "absorb_store":
        _validate_probability(effect.get("percent"), path, "percent", issues)
    elif effect_type == "cap_damage":
        cap_value = effect.get("max_damage")
        if isinstance(cap_value, str):
            if cap_value not in ALLOWED_CAP_DAMAGE_TOKENS:
                issues.append(f"{path}: max_damage token '{cap_value}' ist ungueltig")
        elif cap_value is not None:
            _validate_int(cap_value, path, "max_damage", issues, minimum=0)
        else:
            issues.append(f"{path}: max_damage fehlt")
    elif effect_type == "airborne_two_phase":
        _validate_amount_range(effect.get("landing_damage"), path, "landing_damage", issues, minimum=0)
    elif effect_type == "shield":
        _validate_int(effect.get("hp"), path, "hp", issues, minimum=1)
        break_counter = effect.get("break_counter")
        if break_counter is not None:
            _validate_int(break_counter, path, "break_counter", issues, minimum=0)
    elif effect_type in {"enemy_attack_self_damage", "enemy_special_self_damage", "enemy_next_special_self_damage", "heal_curse"}:
        _validate_int(effect.get("amount") or effect.get("damage"), path, "amount", issues, minimum=0)
        turns = effect.get("turns")
        if turns is not None:
            _validate_int(turns, path, "turns", issues, minimum=1)
    elif effect_type in {"increase_last_enemy_special_cooldown", "increase_random_enemy_cooldown"}:
        _validate_int(effect.get("amount"), path, "amount", issues, minimum=1)
    elif effect_type == "incoming_damage_bonus":
        _validate_int(effect.get("amount"), path, "amount", issues, minimum=1)
        _validate_int(effect.get("turns"), path, "turns", issues, minimum=1)
    elif effect_type == "next_attack_flat_penalty":
        _validate_int(effect.get("amount"), path, "amount", issues, minimum=1)
    elif effect_type == "burn_multiplier":
        _validate_number(effect.get("multiplier"), path, "multiplier", issues, minimum=1.0)
        _validate_int(effect.get("uses"), path, "uses", issues, minimum=1)
    elif effect_type == "finisher_below_hp":
        _validate_int(effect.get("threshold"), path, "threshold", issues, minimum=0)
    elif effect_type == "heal_from_target_dot":
        if str(effect.get("dot_type") or "").strip() not in {"burning", "poison", "bleeding"}:
            issues.append(f"{path}: dot_type ist ungueltig")


def _validate_attack(attack, path: str, issues: list[str], seen_attack_names: dict[str, str]) -> None:
    if not isinstance(attack, dict):
        issues.append(f"{path}: attack ist kein dict")
        return

    attack_name = attack.get("name")
    if _validate_non_empty_string(attack_name, path, "attack.name", issues):
        normalized_name = _normalize_label(attack_name)
        previous_path = seen_attack_names.get(normalized_name)
        if previous_path is not None:
            issues.append(f"{path}: doppelter Attackenname '{attack_name}' (bereits {previous_path})")
        else:
            seen_attack_names[normalized_name] = path

    _validate_amount_range(attack.get("damage"), path, "damage", issues, minimum=0)
    _validate_non_empty_string(attack.get("info"), path, "info", issues)

    cooldown_turns = attack.get("cooldown_turns")
    if cooldown_turns is not None:
        _validate_int(cooldown_turns, path, "cooldown_turns", issues, minimum=1)

    cooldown_from_burning_plus = attack.get("cooldown_from_burning_plus")
    if cooldown_from_burning_plus is not None:
        _validate_int(cooldown_from_burning_plus, path, "cooldown_from_burning_plus", issues, minimum=0)

    self_damage = attack.get("self_damage")
    if self_damage is not None:
        _validate_amount_range(self_damage, path, "self_damage", issues, minimum=0)

    heal = attack.get("heal")
    if heal is not None:
        _validate_amount_range(heal, path, "heal", issues, minimum=0)

    conditional_enemy_hp_below_pct = attack.get("conditional_enemy_hp_below_pct")
    if conditional_enemy_hp_below_pct is not None:
        _validate_probability(conditional_enemy_hp_below_pct, path, "conditional_enemy_hp_below_pct", issues)

    bonus_if_self_hp_below_pct = attack.get("bonus_if_self_hp_below_pct")
    if bonus_if_self_hp_below_pct is not None:
        _validate_probability(bonus_if_self_hp_below_pct, path, "bonus_if_self_hp_below_pct", issues)

    damage_if_condition = attack.get("damage_if_condition")
    if damage_if_condition is not None:
        _validate_amount_range(damage_if_condition, path, "damage_if_condition", issues, minimum=0)

    bonus_damage_if_condition = attack.get("bonus_damage_if_condition")
    if bonus_damage_if_condition is not None:
        _validate_int(bonus_damage_if_condition, path, "bonus_damage_if_condition", issues, minimum=0)

    guaranteed_hit_if_condition = attack.get("guaranteed_hit_if_condition")
    if guaranteed_hit_if_condition is not None and not isinstance(guaranteed_hit_if_condition, bool):
        issues.append(f"{path}: guaranteed_hit_if_condition ist kein bool")

    requires_reload = attack.get("requires_reload")
    if requires_reload is not None and not isinstance(requires_reload, bool):
        issues.append(f"{path}: requires_reload ist kein bool")
    if requires_reload:
        _validate_non_empty_string(attack.get("reload_name"), path, "reload_name", issues)
    elif attack.get("reload_name") is not None and not _is_non_empty_string(attack.get("reload_name")):
        issues.append(f"{path}: reload_name ist leer")

    add_absorbed_damage = attack.get("add_absorbed_damage")
    if add_absorbed_damage is not None and not isinstance(add_absorbed_damage, bool):
        issues.append(f"{path}: add_absorbed_damage ist kein bool")

    lifesteal_ratio = attack.get("lifesteal_ratio")
    if lifesteal_ratio is not None:
        _validate_probability(lifesteal_ratio, path, "lifesteal_ratio", issues)

    button_style = attack.get("button_style")
    if button_style is not None:
        if not _is_non_empty_string(button_style):
            issues.append(f"{path}: button_style fehlt")
        elif str(button_style).strip() not in ALLOWED_BUTTON_STYLES:
            issues.append(f"{path}: button_style '{button_style}' ist ungueltig")

    is_standard_attack = attack.get("is_standard_attack")
    if is_standard_attack is not None and not isinstance(is_standard_attack, bool):
        issues.append(f"{path}: is_standard_attack ist kein bool")

    damage_breakdown = attack.get("damage_breakdown")
    if damage_breakdown is not None:
        if not isinstance(damage_breakdown, dict):
            issues.append(f"{path}: damage_breakdown ist kein dict")
        else:
            _validate_int(damage_breakdown.get("start_damage"), path, "damage_breakdown.start_damage", issues, minimum=0)
            _validate_int(
                damage_breakdown.get("burn_damage_per_round"),
                path,
                "damage_breakdown.burn_damage_per_round",
                issues,
                minimum=0,
            )
            _validate_int(
                damage_breakdown.get("burn_duration_rounds"),
                path,
                "damage_breakdown.burn_duration_rounds",
                issues,
                minimum=0,
            )

    multi_hit = attack.get("multi_hit")
    if multi_hit is not None:
        if not isinstance(multi_hit, dict):
            issues.append(f"{path}: multi_hit ist kein dict")
        else:
            _validate_int(multi_hit.get("hits"), path, "multi_hit.hits", issues, minimum=1)
            _validate_probability(multi_hit.get("hit_chance"), path, "multi_hit.hit_chance", issues)
            if _validate_amount_range(multi_hit.get("per_hit_damage"), path, "multi_hit.per_hit_damage", issues, minimum=0):
                guaranteed_min_per_hit = multi_hit.get("guaranteed_min_per_hit")
                if guaranteed_min_per_hit is not None and _validate_int(
                    guaranteed_min_per_hit,
                    path,
                    "multi_hit.guaranteed_min_per_hit",
                    issues,
                    minimum=0,
                ):
                    per_hit_damage = multi_hit.get("per_hit_damage")
                    if isinstance(per_hit_damage, list) and len(per_hit_damage) == 2:
                        if int(guaranteed_min_per_hit) > int(per_hit_damage[1]):
                            issues.append(f"{path}: multi_hit.guaranteed_min_per_hit ist groesser als per_hit_damage max")

    effects = attack.get("effects")
    if effects is not None:
        if not isinstance(effects, list):
            issues.append(f"{path}: effects ist keine Liste")
        else:
            for effect_index, effect in enumerate(effects, start=1):
                _validate_effect(effect, f"{path}.effect[{effect_index}]", issues)


def validate_cards(cards) -> list[str]:
    if not isinstance(cards, list):
        return ["karten ist keine Liste"]

    issues: list[str] = []
    seen_card_names: dict[str, str] = {}
    for card_index, card in enumerate(cards, start=1):
        path = str(card_index)
        if not isinstance(card, dict):
            issues.append(f"{path}: karte ist kein dict")
            continue

        card_name = card.get("name")
        if _validate_non_empty_string(card_name, path, "name", issues):
            normalized_name = _normalize_label(card_name)
            previous_path = seen_card_names.get(normalized_name)
            if previous_path is not None:
                issues.append(f"{path}: doppelter Kartenname '{card_name}' (bereits {previous_path})")
            else:
                seen_card_names[normalized_name] = path

        _validate_non_empty_string(card.get("beschreibung"), path, "beschreibung", issues)

        image = card.get("bild")
        if not _is_non_empty_string(image):
            issues.append(f"{path}: fehlt bild")
        else:
            image_text = str(image).strip().lower()
            if not image_text.startswith("http://") and not image_text.startswith("https://"):
                issues.append(f"{path}: bild ist ungueltig")

        rarity = card.get("seltenheit")
        if not _is_non_empty_string(rarity):
            issues.append(f"{path}: fehlt seltenheit")
        else:
            rarity_key = normalize_rarity_key(rarity)
            if rarity_key not in ALLOWED_RARITY_KEYS:
                issues.append(f"{path}: ungueltige seltenheit '{rarity}'")

        _validate_int(card.get("hp"), path, "hp", issues, minimum=1)

        attacks = card.get("attacks")
        if not isinstance(attacks, list):
            issues.append(f"{path}: attacks ist keine Liste")
            continue
        if not attacks:
            issues.append(f"{path}: attacks ist leer")
            continue

        seen_attack_names: dict[str, str] = {}
        for attack_index, attack in enumerate(attacks, start=1):
            _validate_attack(attack, f"{path}.{attack_index}", issues, seen_attack_names)

    return issues
