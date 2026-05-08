from __future__ import annotations

from copy import deepcopy
from typing import Any


OPERATION_BROKEN_TIMELINE_ENCOUNTERS: list[dict[str, Any]] = [
    {
        "name": "Ödland-Plünderer",
        "beschreibung": "Schwächt und nervt Gegner auf dem Weg zur Festung.",
        "bild": "https://i.imgur.com/YPnvbdW.png",
        "seltenheit": "Mission",
        "hp": 40,
        "attacks": [
            {"name": "Schild-Splitter", "damage": [12, 12], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Netz-Falle",
                "damage": [5, 5],
                "cooldown_turns": 3,
                "bot_priority": 30,
                "info": "5 Schaden. Nächste Runde sind alle Spezialangriffe gesperrt.",
                "effects": [{"type": "special_lock", "target": "enemy", "turns": 1, "chance": 1.0}],
            },
            {
                "name": "Pfeil-Hagel",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "bot_priority": 28,
                "info": "3 Runden je 6 Schaden.",
                "effects": [{"type": "bleeding", "target": "enemy", "duration": 3, "damage": 6, "chance": 1.0}],
            },
            {
                "name": "Plünder-Glück",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "bot_priority": 20,
                "heal": [15, 15],
                "info": "Heilt 15 HP.",
            },
        ],
    },
    {
        "name": "Gamma-Mutant",
        "beschreibung": "Ein radioaktiver Mutant mit stapelnder Kraft.",
        "bild": "https://i.imgur.com/sJKKeeG.png",
        "seltenheit": "Mission",
        "hp": 60,
        "passives": [{"type": "on_hit_recoil", "damage": 4, "source": "Radioaktive Aura"}],
        "attacks": [
            {"name": "Gamma-Pranke", "damage": [15, 15], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Zell-Mutation",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "bot_priority": 26,
                "info": "Erhöht den eigenen Schaden dauerhaft um +3, stapelbar.",
                "effects": [{"type": "permanent_damage_boost", "target": "self", "amount": 3, "chance": 1.0}],
            },
            {
                "name": "Instabiler Kollaps",
                "damage": [25, 25],
                "cooldown_turns": 6,
                "self_damage": 15,
                "info": "25 Schaden, erleidet selbst 15 Schaden.",
            },
        ],
    },
    {
        "name": "Umprogrammierter Hulkbuster",
        "beschreibung": "Defensive Kampfmaschine vor Maestros Thronsaal.",
        "bild": "https://i.imgur.com/PvK2BHp.png",
        "seltenheit": "Mission",
        "hp": 110,
        "attacks": [
            {"name": "Hydraulik-Hammer", "damage": [18, 18], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Schubdüsen-Ramme",
                "damage": [25, 25],
                "cooldown_turns": 5,
                "bot_priority": 27,
                "info": "25 Schaden. Hulkbuster nimmt beim nächsten Treffer 100% mehr Schaden.",
                "effects": [{"type": "incoming_damage_multiplier", "target": "self", "multiplier": 2.0, "uses": 1, "chance": 1.0}],
            },
            {
                "name": "Reparatur-Naniten",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "bot_priority": 30,
                "heal": [10, 10],
                "info": "Blockt den nächsten Angriff komplett und heilt 10 HP.",
                "effects": [{"type": "cap_damage", "target": "self", "max_damage": 0, "chance": 1.0}],
            },
            {
                "name": "Schwere Salve",
                "damage": [0, 24],
                "cooldown_turns": 6,
                "bot_priority": 28,
                "multi_hit": {"hits": 4, "hit_chance": 1.0, "per_hit_damage": [6, 6]},
                "info": "4 Raketen mit je 6 Schaden.",
            },
        ],
    },
    {
        "name": "Maestro",
        "beschreibung": "Bruce Banner aus einer gebrochenen Zeitlinie: intelligent, grausam und brutal.",
        "bild": "https://i.imgur.com/FnpMS1O.png",
        "seltenheit": "Boss",
        "hp": 200,
        "mission_boss": "maestro",
        "attacks": [
            {"name": "Tyrannen-Schlag", "damage": [20, 20], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Trophäensaal-Raub",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "bot_priority": 35,
                "info": "Zufällig: Schild blockt 20 oder Tyrannen-Schlag verursacht +15 Schaden.",
                "effects": [{"type": "maestro_artifact", "target": "self", "chance": 1.0, "bonus_attack_name": "Tyrannen-Schlag", "amount": 15, "uses": 1}],
            },
            {
                "name": "Maestros Hohn",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "bot_priority": 45,
                "info": "Reduziert den nächsten Spielerangriff auf 0 Schaden.",
                "effects": [{"type": "next_attack_damage_override", "target": "enemy", "damage": 0, "uses": 1, "chance": 1.0}],
            },
            {"name": "Gamma-Eruption", "damage": [40, 40], "cooldown_turns": 6, "info": "40 Schaden."},
        ],
    },
]


def get_operation_broken_timeline_encounters() -> list[dict[str, Any]]:
    return deepcopy(OPERATION_BROKEN_TIMELINE_ENCOUNTERS)
