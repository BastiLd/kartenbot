from __future__ import annotations


# Hier kannst du einstellen, ob Cooldowns von einem Kampf in den naechsten
# uebernommen werden.
# - normal: /kampf (PvP und Bot)
# - mission: Missions-Wellen/Folgekaempfe
# - story: Story-Kaempfe (wenn aktiv)
COOLDOWN_CARRYOVER: dict[str, bool] = {
    "normal": False,
    "mission": True,
    "story": False,
}


def should_carry_cooldowns(mode: str) -> bool:
    key = str(mode or "").strip().lower()
    aliases = {
        "fight": "normal",
        "kampf": "normal",
        "pvp": "normal",
    }
    key = aliases.get(key, key)
    return bool(COOLDOWN_CARRYOVER.get(key, False))
