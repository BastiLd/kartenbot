import random

import discord


def resolve_multi_hit_damage(
    multi_hit: dict,
    *,
    buff_amount: int = 0,
    attack_multiplier: float = 1.0,
    force_max: bool = False,
    guaranteed_hit: bool = False,
    return_details: bool = False,
):
    """Resolve multi-hit damage and return (damage, min_possible, max_possible[, details])."""
    hits = max(0, int(multi_hit.get("hits", 0) or 0))
    details = {
        "hits": hits,
        "landed_hits": 0,
        "per_hit_damages": [],
        "total_before_multiplier": 0,
        "total_damage": 0,
    }
    if hits <= 0:
        if return_details:
            return 0, 0, 0, details
        return 0, 0, 0

    per_hit = multi_hit.get("per_hit_damage", [0, 0])
    if isinstance(per_hit, list) and len(per_hit) == 2:
        hit_min = int(per_hit[0])
        hit_max = int(per_hit[1])
    else:
        hit_min = 0
        hit_max = 0
    hit_min = max(0, hit_min)
    hit_max = max(hit_min, hit_max)

    chance = float(multi_hit.get("hit_chance", 0.0) or 0.0)
    chance = max(0.0, min(1.0, chance))

    guaranteed_min_per_hit = hit_min
    if guaranteed_hit and not force_max:
        try:
            guaranteed_min_per_hit = max(hit_min, int(multi_hit.get("guaranteed_min_per_hit", hit_min) or hit_min))
        except Exception:
            guaranteed_min_per_hit = hit_min

    if force_max or guaranteed_hit:
        landed = hits
    else:
        landed = sum(1 for _ in range(hits) if random.random() < chance)

    total = 0
    per_hit_damages: list[int] = []
    if landed > 0:
        for _ in range(landed):
            if force_max:
                rolled = hit_max
            elif guaranteed_hit:
                rolled = random.randint(guaranteed_min_per_hit, hit_max)
            else:
                rolled = random.randint(hit_min, hit_max)
            per_hit_damages.append(int(rolled))
            total += int(rolled)
        total += int(buff_amount)

    min_possible = 0
    if force_max:
        min_possible = hits * hit_min + int(buff_amount)
    elif guaranteed_hit:
        min_possible = hits * guaranteed_min_per_hit + int(buff_amount)

    max_possible = hits * hit_max + int(buff_amount)

    if attack_multiplier != 1.0:
        total = int(round(total * attack_multiplier))
        min_possible = int(round(min_possible * attack_multiplier))
        max_possible = int(round(max_possible * attack_multiplier))

    final_total = max(0, total)
    details.update(
        {
            "hits": hits,
            "landed_hits": int(landed),
            "per_hit_damages": per_hit_damages,
            "total_before_multiplier": int(sum(per_hit_damages) + (int(buff_amount) if landed > 0 else 0)),
            "total_damage": int(final_total),
        }
    )
    if return_details:
        return final_total, max(0, min_possible), max(0, max_possible), details
    return final_total, max(0, min_possible), max(0, max_possible)


def apply_outgoing_attack_modifier(raw_damage: int, *, percent: float = 0.0, flat: int = 0) -> tuple[int, int]:
    """
    Apply outgoing attack reduction.
    Returns (final_damage, overflow_self_damage).
    """
    damage = max(0, int(raw_damage))
    if damage <= 0:
        return 0, 0

    reduction_pct = max(0.0, min(1.0, float(percent or 0.0)))
    if reduction_pct > 0:
        damage = max(0, int(round(damage * (1.0 - reduction_pct))))

    reduction_flat = max(0, int(flat or 0))
    overflow = 0
    if reduction_flat > 0:
        if damage >= reduction_flat:
            damage -= reduction_flat
        else:
            overflow = reduction_flat - damage
            damage = 0

    return max(0, damage), max(0, overflow)


def calculate_damage(attack_damage, buff_amount=0):
    """
    Calculate damage with right-skew distribution.
    attack_damage: [min, max] list or single value (backwards compatible)
    buff_amount: additional buff damage
    Returns: (actual_damage, is_critical, min_damage, max_damage)
    """
    if isinstance(attack_damage, list) and len(attack_damage) == 2:
        min_damage, max_damage = attack_damage
    else:
        min_damage = max_damage = attack_damage

    min_damage += buff_amount
    max_damage += buff_amount

    if max_damage <= 50:
        critical_chance = 0.12
    elif max_damage <= 100:
        critical_chance = 0.08
    else:
        critical_chance = 0.05

    if random.random() < critical_chance:
        actual_damage = max_damage
        is_critical = True
    else:
        damage_range = max_damage - min_damage
        if damage_range <= 0:
            actual_damage = min_damage
        else:
            skew_factor = random.random() * random.random()
            actual_damage = min_damage + int(skew_factor * damage_range)
        is_critical = False

    return actual_damage, is_critical, min_damage, max_damage


def create_battle_log_embed():
    embed = discord.Embed(
        title="Kampf-Log",
        description="*Der Kampf beginnt...*",
        color=0x2F3136,
    )
    return embed


def _trim_battle_log(text: str, max_len: int = 3800) -> str:
    if len(text) <= max_len:
        return text
    parts = text.split("\n\n**Runde ")
    if len(parts) <= 1:
        return text[-max_len:]
    header = parts[0]
    rounds = parts[1:]
    kept = []
    total_len = len(header)
    for part in reversed(rounds):
        segment = "\n\n**Runde " + part
        if total_len + len(segment) > max_len:
            break
        kept.append(segment)
        total_len += len(segment)
    trimmed = header + "".join(reversed(kept))
    return trimmed[-max_len:] if len(trimmed) > max_len else trimmed


def update_battle_log(
    existing_embed,
    attacker_name,
    defender_name,
    attack_name,
    actual_damage,
    is_critical,
    attacker_user,
    defender_user,
    round_number,
    defender_remaining_hp,
    pre_effect_damage: int = 0,
    confusion_applied: bool = False,
    self_hit_damage: int = 0,
    attacker_status_icons: str = "",
    defender_status_icons: str = "",
    effect_events: list[str] | None = None,
):
    critical_text = "\U0001f4a5 **VOLLTREFFER!**" if is_critical else ""

    current_desc = existing_embed.description or "*Der Kampf beginnt...*"

    attacker_display = attacker_user.display_name if isinstance(attacker_user, discord.Member) else "Bot"
    defender_display = defender_user.display_name if isinstance(defender_user, discord.Member) else "Bot"
    if attacker_status_icons:
        attacker_display = f"{attacker_display}{attacker_status_icons}"
    if defender_status_icons:
        defender_display = f"{defender_display}{defender_status_icons}"

    burn_suffix = f" (+{pre_effect_damage} \U0001f525)" if pre_effect_damage and pre_effect_damage > 0 else ""
    confusion_suffix = " (+Verwirrung)" if confusion_applied else ""
    self_hit_suffix = f" (Selbsttreffer: {self_hit_damage})" if (self_hit_damage and self_hit_damage > 0) else ""
    effect_text = ""
    if effect_events:
        lines = [str(event).strip() for event in effect_events if str(event).strip()]
        if lines:
            effect_text = "\n" + "\n".join(f"- {line}" for line in lines[:8])
    new_entry = (
        f"\n\n**Runde {round_number}:**\n"
        f"{critical_text}\n"
        f"**{attacker_display}s {attacker_name}** \u27a4 **{attack_name}** \u27a4 "
        f"**{actual_damage} Schaden{burn_suffix}{confusion_suffix}{self_hit_suffix}** an "
        f"**{defender_display}s {defender_name}**"
        f"{effect_text}\n"
        f"\U0001f6e1\ufe0f {defender_display} hat jetzt noch **{defender_remaining_hp} Leben**."
    )

    existing_embed.description = _trim_battle_log(current_desc + new_entry)
    existing_embed.color = 0xFF6B6B if is_critical else 0x4ECDC4

    return existing_embed


def create_battle_embed(
    player1_card,
    player2_card,
    player1_hp,
    player2_hp,
    current_turn,
    user1,
    user2,
    active_effects=None,
    current_attack_infos: list[str] | None = None,
):
    user1_name = user1.display_name if user1 else "Bot"
    user2_name = user2.display_name if user2 else "Bot"
    user1_mention = user1.mention if user1 else "Bot"
    user2_mention = user2.mention if user2 else "Bot"

    embed = discord.Embed(
        title="**1v1 Kampf beginnt!**",
        description=f"**{user1_mention} vs {user2_mention}**",
    )

    current_card = player1_card if current_turn == (user1.id if user1 else 0) else player2_card
    other_card = player2_card if current_turn == (user1.id if user1 else 0) else player1_card

    embed.set_image(url=current_card["bild"])
    embed.set_thumbnail(url=other_card["bild"])

    player1_id = user1.id if hasattr(user1, "id") else 0
    player2_id = user2.id if hasattr(user2, "id") else 0
    player1_burning = active_effects and any(e["type"] == "burning" for e in active_effects.get(player1_id, []))
    player2_burning = active_effects and any(e["type"] == "burning" for e in active_effects.get(player2_id, []))
    player1_confused = active_effects and any(e["type"] == "confusion" for e in active_effects.get(player1_id, []))
    player2_confused = active_effects and any(e["type"] == "confusion" for e in active_effects.get(player2_id, []))
    player1_stealth = active_effects and any(e["type"] == "stealth" for e in active_effects.get(player1_id, []))
    player2_stealth = active_effects and any(e["type"] == "stealth" for e in active_effects.get(player2_id, []))
    player1_airborne = active_effects and any(e["type"] == "airborne" for e in active_effects.get(player1_id, []))
    player2_airborne = active_effects and any(e["type"] == "airborne" for e in active_effects.get(player2_id, []))

    if current_turn == (user1.id if user1 else 0):
        player1_label = (
            f"**\U0001f7e5 {user1_name}s Karte"
            f"{'\U0001f525' if player1_burning else ''}"
            f"{' \U0001f300' if player1_confused else ''}"
            f"{' \U0001f977' if player1_stealth else ''}"
            f"{' \u2708\ufe0f' if player1_airborne else ''}**"
        )
        player2_label = (
            f"\U0001f7e6 {user2_name}s Karte"
            f"{'\U0001f525' if player2_burning else ''}"
            f"{' \U0001f300' if player2_confused else ''}"
            f"{' \U0001f977' if player2_stealth else ''}"
            f"{' \u2708\ufe0f' if player2_airborne else ''}"
        )
    else:
        player1_label = (
            f"\U0001f7e5 {user1_name}s Karte"
            f"{'\U0001f525' if player1_burning else ''}"
            f"{' \U0001f300' if player1_confused else ''}"
            f"{' \U0001f977' if player1_stealth else ''}"
            f"{' \u2708\ufe0f' if player1_airborne else ''}"
        )
        player2_label = (
            f"**\U0001f7e6 {user2_name}s Karte"
            f"{'\U0001f525' if player2_burning else ''}"
            f"{' \U0001f300' if player2_confused else ''}"
            f"{' \U0001f977' if player2_stealth else ''}"
            f"{' \u2708\ufe0f' if player2_airborne else ''}**"
        )

    embed.add_field(name=player1_label, value=f"{player1_card['name']}\nHP: {player1_hp}", inline=True)
    embed.add_field(name=player2_label, value=f"{player2_card['name']}\nHP: {player2_hp}", inline=True)
    embed.add_field(
        name="\u2694\ufe0f",
        value=f"**{user1_mention if current_turn == (user1.id if user1 else 0) else user2_mention} ist an der Reihe**",
        inline=False,
    )
    if current_attack_infos:
        info_value = "\n".join(current_attack_infos[:4])
        if len(info_value) > 1024:
            info_value = info_value[:1021] + "..."
        embed.add_field(name="Fähigkeiten", value=info_value, inline=False)
    return embed


STATUS_PRIORITY_MAP = {
    "green": 0,
    "orange": 1,
    "red": 2,
    "black": 3,
}
STATUS_CIRCLE_MAP = {
    "green": "\U0001f7e2",
    "orange": "\U0001f7e0",
    "red": "\U0001f534",
    "black": "\u26ab",
}


def _presence_to_color(member: discord.Member) -> str:
    try:
        status = member.status
        if status == discord.Status.online:
            return "green"
        if status == discord.Status.idle:
            return "orange"
        if status == discord.Status.dnd:
            return "red"
        return "black"
    except Exception:
        return "black"
