from __future__ import annotations

from copy import deepcopy
from typing import Any

DEADPOOL_IMAGE_URL = "https://i.imgur.com/4mxNv2c.png"


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
                "name": "Strahlen-Welle",
                "damage": [12, 14],
                "cooldown_turns": 3,
                "info": "12-14 Schaden plus 2 Runden lang 3 Giftschaden.",
                "effects": [{"type": "poison", "target": "enemy", "damage": [3, 3], "duration": [2, 2], "chance": 1.0}],
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
        "hp": 185,
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


OPERATION_TECHNISCHER_KOLLAPS_ENCOUNTERS: list[dict[str, Any]] = [
    {
        "name": "A.I.M.-Laborwache",
        "beschreibung": "Sichert den Eingang zum Rechenzentrum.",
        "bild": "https://i.imgur.com/05Hsl4i.png",
        "seltenheit": "Mission",
        "hp": 40,
        "attacks": [
            {"name": "Laser-Gewehr", "damage": [12, 12], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Betäubungsschuss",
                "damage": [8, 8],
                "cooldown_turns": 4,
                "info": "8 Schaden. Cooldowns des Spielers sinken diese Runde nicht.",
                "effects": [{"type": "cooldown_freeze", "target": "enemy", "turns": 1, "chance": 1.0}],
            },
            {
                "name": "Schild-Generator",
                "damage": [0, 0],
                "cooldown_turns": 3,
                "info": "Blockt den nächsten Angriff komplett.",
                "effects": [{"type": "cap_damage", "target": "self", "max_damage": 0, "chance": 1.0}],
            },
            {
                "name": "Ziel-Erfassung",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "info": "Nächster eigener Angriff verursacht +10 Schaden.",
                "effects": [{"type": "damage_boost", "target": "self", "amount": 10, "uses": 1, "chance": 1.0}],
            },
        ],
    },
    {
        "name": "A.I.M.-Wissenschaftler",
        "beschreibung": "Analysiert und schwächt den Gegner.",
        "bild": "https://i.imgur.com/cq5dcDL.png",
        "seltenheit": "Mission",
        "hp": 60,
        "attacks": [
            {"name": "Schall-Impuls", "damage": [14, 14], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Daten-Diebstahl",
                "damage": [10, 10],
                "cooldown_turns": 4,
                "heal": [10, 10],
                "info": "10 Schaden und heilt 10 HP.",
            },
            {
                "name": "Reparatur-Drohne",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "heal": [20, 20],
                "info": "Heilt 20 HP.",
            },
            {
                "name": "Naniten-Injektion",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "info": "3 Runden lang 5 Schaden.",
                "effects": [{"type": "bleeding", "target": "enemy", "duration": 3, "damage": 5, "chance": 1.0}],
            },
        ],
    },
    {
        "name": "Schwerer Kampf-Mech",
        "beschreibung": "Letzte Verteidigung vor M.O.D.O.K.",
        "bild": "https://i.imgur.com/ASXNdbC.png",
        "seltenheit": "Mission",
        "hp": 120,
        "attacks": [
            {"name": "Rammstoß", "damage": [18, 18], "is_standard_attack": True, "info": "Standardangriff."},
            {"name": "Gatling-Kanone", "damage": [28, 28], "cooldown_turns": 4, "info": "28 Schaden."},
            {
                "name": "Energieschild",
                "damage": [0, 0],
                "cooldown_turns": 6,
                "info": "Blockt den nächsten Angriff komplett.",
                "effects": [{"type": "cap_damage", "target": "self", "max_damage": 0, "chance": 1.0}],
            },
            {
                "name": "Selbstzerstörungs-Protokoll",
                "damage": [45, 45],
                "cooldown_turns": 6,
                "self_damage": 999,
                "conditional_self_hp_below_pct": 0.2,
                "info": "Nur unter 20% HP: 45 Schaden und zerstört sich selbst.",
            },
        ],
    },
    {
        "name": "M.O.D.O.K.",
        "beschreibung": "Meister der Berechnung und mentalen Kriegsführung.",
        "bild": "https://i.imgur.com/421cobH.png",
        "seltenheit": "Boss",
        "hp": 190,
        "mission_boss": "modok",
        "attacks": [
            {"name": "Gedankenstrahl", "damage": [20, 24], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "System-Hack",
                "damage": [15, 15],
                "cooldown_turns": 4,
                "info": "15 Schaden. Spezialangriffe des Spielers nächste Runde gesperrt.",
                "effects": [{"type": "special_lock", "target": "enemy", "turns": 1, "chance": 1.0}],
            },
            {
                "name": "Berechnete Heilung",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "heal": [30, 50],
                "info": "Heilt 30-50 HP.",
            },
            {"name": "Gehirn-Explosion", "damage": [40, 40], "cooldown_turns": 6, "info": "40 Schaden."},
        ],
    },
]


OPERATION_GRUENER_TERROR_ENCOUNTERS: list[dict[str, Any]] = [
    {
        "name": "Oscorp-Sicherheitsdrohne",
        "beschreibung": "Automatisierte Vorhut am Oscorp-Komplex.",
        "bild": "https://i.imgur.com/r8lv2my.png",
        "seltenheit": "Mission",
        "hp": 40,
        "attacks": [
            {"name": "Puls-Laser", "damage": [12, 12], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Ziel-Markierung",
                "damage": [0, 0],
                "cooldown_turns": 3,
                "info": "Nächster eigener Angriff +10 Schaden.",
                "effects": [{"type": "damage_boost", "target": "self", "amount": 10, "uses": 1, "chance": 1.0}],
            },
            {
                "name": "Ausweich-Manöver",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "info": "Der nächste Angriff auf die Drohne verfehlt.",
                "effects": [{"type": "evade", "target": "self", "uses": 1, "chance": 1.0}],
            },
            {
                "name": "Blend-Blitz",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "info": "Nächster Spielerangriff -10 Schaden.",
                "effects": [{"type": "next_attack_flat_penalty", "target": "enemy", "amount": 10, "turns": 1, "chance": 1.0}],
            },
        ],
    },
    {
        "name": "Goblin-Scherge",
        "beschreibung": "Chaos-Kämpfer mit Bomben und Fallen.",
        "bild": "https://i.imgur.com/ihUFbjb.png",
        "seltenheit": "Mission",
        "hp": 60,
        "attacks": [
            {"name": "Nagel-Keule", "damage": [14, 14], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Instabile Kürbisbombe",
                "damage": [10, 10],
                "cooldown_turns": 3,
                "info": "10 Sofortschaden und 5 Schaden in der nächsten Runde.",
                "effects": [{"type": "burn", "target": "enemy", "duration": 1, "damage": 5, "chance": 1.0}],
            },
            {
                "name": "Wahnsinniges Gelächter",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "heal": [15, 15],
                "info": "Heilt 15 HP.",
            },
            {"name": "Splittergranate", "damage": [22, 22], "cooldown_turns": 4, "info": "22 Schaden."},
        ],
    },
    {
        "name": "Prototyp-Kampfgleiter",
        "beschreibung": "Schwere Luftunterstützung für den Boss.",
        "bild": "https://i.imgur.com/ne9mFFp.png",
        "seltenheit": "Mission",
        "hp": 115,
        "attacks": [
            {"name": "MG-Sperrfeuer", "damage": [18, 18], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Turbinen-Schock",
                "damage": [15, 15],
                "cooldown_turns": 5,
                "info": "15 Schaden und sperrt Spezialangriffe nächste Runde.",
                "effects": [{"type": "special_lock", "target": "enemy", "turns": 1, "chance": 1.0}],
            },
            {"name": "Hitzesuchende Rakete", "damage": [30, 30], "cooldown_turns": 6, "info": "30 Schaden."},
            {
                "name": "Täuschkörper",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "info": "Blockt den nächsten Angriff komplett.",
                "effects": [{"type": "cap_damage", "target": "self", "max_damage": 0, "chance": 1.0}],
            },
        ],
    },
    {
        "name": "Green Goblin",
        "beschreibung": "Unberechenbar, schnell und tödlich.",
        "bild": "https://i.imgur.com/MsKkOLU.png",
        "seltenheit": "Boss",
        "hp": 190,
        "mission_boss": "green_goblin",
        "attacks": [
            {"name": "Goblin-Handschuh", "damage": [22, 22], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Gleiter-Ramme",
                "damage": [20, 20],
                "cooldown_turns": 4,
                "info": "20 Schaden und nächster Spieler-Spezialangriff verursacht 10 Rückstoß.",
                "effects": [{"type": "counter_flat", "target": "enemy", "damage": 10, "uses": 1, "chance": 1.0}],
            },
            {
                "name": "Halluzinogenes Gas",
                "damage": [10, 10],
                "cooldown_turns": 5,
                "info": "10 Schaden und 50% Verfehlchance beim nächsten Angriff des Spielers.",
                "effects": [{"type": "blind", "target": "enemy", "chance_value": 0.5, "chance": 1.0}],
            },
            {
                "name": "Kürbisbomben-Teppich",
                "damage": [0, 36],
                "cooldown_turns": 6,
                "multi_hit": {"hits": 3, "hit_chance": 1.0, "per_hit_damage": [12, 12]},
                "info": "3 Treffer mit je 12 Schaden.",
            },
        ],
    },
]


OPERATION_GOLDENER_KAEFIG_ENCOUNTERS: list[dict[str, Any]] = [
    {
        "name": "Fisk-Straßenschläger",
        "beschreibung": "Brutaler Vorposten von Fisk.",
        "bild": "https://i.imgur.com/3mfS7oT.png",
        "seltenheit": "Mission",
        "hp": 45,
        "attacks": [
            {"name": "Schlagring-Hieb", "damage": [13, 13], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Taschen-Sand",
                "damage": [5, 5],
                "cooldown_turns": 3,
                "info": "5 Schaden und 50% Verfehlchance auf den nächsten Angriff.",
                "effects": [{"type": "blind", "target": "enemy", "chance_value": 0.5, "chance": 1.0}],
            },
            {
                "name": "Schutzgeld-Erpressung",
                "damage": [10, 10],
                "cooldown_turns": 5,
                "heal": [10, 10],
                "info": "10 Schaden und heilt 10 HP.",
            },
            {
                "name": "Verstärkung rufen",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "info": "Nächster eigener Angriff +10 Schaden.",
                "effects": [{"type": "damage_boost", "target": "self", "amount": 10, "uses": 1, "chance": 1.0}],
            },
        ],
    },
    {
        "name": "Korrupte SWAT-Einheit",
        "beschreibung": "Schwer bewaffnete Kontrolleinheit.",
        "bild": "https://i.imgur.com/dPz9Mvz.png",
        "seltenheit": "Mission",
        "hp": 65,
        "attacks": [
            {"name": "Schlagstock", "damage": [15, 15], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Blendgranate",
                "damage": [10, 10],
                "cooldown_turns": 4,
                "info": "10 Schaden und sperrt Spezialangriffe nächste Runde.",
                "effects": [{"type": "special_lock", "target": "enemy", "turns": 1, "chance": 1.0}],
            },
            {
                "name": "Einsatzschild",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "info": "Blockt den nächsten Angriff komplett.",
                "effects": [{"type": "cap_damage", "target": "self", "max_damage": 0, "chance": 1.0}],
            },
            {
                "name": "Tränengas",
                "damage": [0, 0],
                "cooldown_turns": 4,
                "info": "3 Runden lang 6 Schaden.",
                "effects": [{"type": "bleeding", "target": "enemy", "duration": 3, "damage": 6, "chance": 1.0}],
            },
        ],
    },
    {
        "name": "Fisks Enforcer",
        "beschreibung": "Persönlicher Leibwächter mit hoher Ausdauer.",
        "bild": "https://i.imgur.com/s38CXtH.png",
        "seltenheit": "Mission",
        "hp": 115,
        "attacks": [
            {"name": "Schwerer Schwinger", "damage": [18, 18], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Bären-Umklammerung",
                "damage": [15, 15],
                "cooldown_turns": 5,
                "info": "15 Schaden und halbiert den nächsten Spielerangriff.",
                "effects": [{"type": "incoming_damage_multiplier", "target": "enemy", "multiplier": 0.5, "uses": 1, "chance": 1.0}],
            },
            {
                "name": "Kevlar-Weste",
                "damage": [0, 0],
                "cooldown_turns": 6,
                "info": "2 Runden lang -10 eingehender Schaden.",
                "effects": [{"type": "damage_reduction_flat", "target": "self", "amount": 10, "turns": 2, "chance": 1.0}],
            },
            {
                "name": "Kopfstoß",
                "damage": [25, 25],
                "cooldown_turns": 5,
                "conditional_enemy_hp_below_pct": 1.0,
                "info": "25 Schaden und kann betäuben.",
                "effects": [{"type": "stun", "target": "enemy", "chance": 0.5}],
            },
        ],
    },
    {
        "name": "Kingpin",
        "beschreibung": "Massiv, zäh und gnadenlos.",
        "bild": "https://i.imgur.com/vVZATee.png",
        "seltenheit": "Boss",
        "hp": 215,
        "mission_boss": "kingpin",
        "attacks": [
            {"name": "Stockhieb", "damage": [24, 24], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Sumo-Ansturm",
                "damage": [22, 22],
                "cooldown_turns": 4,
                "info": "22 Schaden und entfernt defensive Effekte.",
                "effects": [{"type": "clear_negative_effects", "target": "self", "chance": 1.0}],
            },
            {
                "name": "Bestechungs-Versuch",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "heal": [35, 60],
                "info": "Heilt 60 HP, wenn der Spieler im Zug davor 0 Schaden gemacht hat. Sonst heilt der Angriff 35 HP.",
            },
            {
                "name": "Zermalmender Griff",
                "damage": [40, 60],
                "cooldown_turns": 6,
                "conditional_enemy_hp_below_pct": 0.6,
                "bonus_damage_if_condition": 20,
                "info": "40 Schaden, gegen geschwächte Ziele deutlich mehr.",
            },
        ],
    },
]


OPERATION_HEXENFEUER_ENCOUNTERS: list[dict[str, Any]] = [
    {
        "name": "Verhexte Marionette",
        "beschreibung": "Unheimlicher Wächter des verzauberten Waldes.",
        "bild": "https://i.imgur.com/cebMvyC.png",
        "seltenheit": "Mission",
        "hp": 40,
        "attacks": [
            {"name": "Holz-Hieb", "damage": [12, 12], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Faden-Zug",
                "damage": [6, 6],
                "cooldown_turns": 3,
                "info": "6 Schaden. Nächster Spezialangriff verursacht 5 Rückstoß.",
                "effects": [{"type": "counter_flat", "target": "enemy", "damage": 5, "uses": 1, "chance": 1.0}],
            },
            {
                "name": "Starre",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "info": "Blockt den nächsten Angriff komplett.",
                "effects": [{"type": "cap_damage", "target": "self", "max_damage": 0, "chance": 1.0}],
            },
            {"name": "Gliedmaßen-Wurf", "damage": [20, 20], "cooldown_turns": 4, "heal": [5, 5], "info": "20 Schaden und heilt 5 HP."},
        ],
    },
    {
        "name": "Schatten-Dämon",
        "beschreibung": "Schnell, schwer greifbar und tödlich.",
        "bild": "https://i.imgur.com/8ZERg8r.png",
        "seltenheit": "Mission",
        "hp": 65,
        "attacks": [
            {"name": "Spektral-Kralle", "damage": [14, 14], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Furcht-Aura",
                "damage": [10, 10],
                "cooldown_turns": 4,
                "info": "10 Schaden und halbiert den nächsten Spielerangriff.",
                "effects": [{"type": "incoming_damage_multiplier", "target": "enemy", "multiplier": 0.5, "uses": 1, "chance": 1.0}],
            },
            {
                "name": "Phasen-Shift",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "info": "Der nächste Angriff auf den Dämon verfehlt.",
                "effects": [{"type": "evade", "target": "self", "uses": 1, "chance": 1.0}],
            },
            {"name": "Lebensentzug", "damage": [15, 15], "cooldown_turns": 4, "heal": [15, 15], "info": "15 Schaden und heilt um den Schaden."},
        ],
    },
    {
        "name": "Wächter des Dunkelbuchs",
        "beschreibung": "Runenmagier mit starkem Schutzschild.",
        "bild": "https://i.imgur.com/X7ltg61.png",
        "seltenheit": "Mission",
        "hp": 115,
        "attacks": [
            {"name": "Runen-Stab", "damage": [18, 18], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Chaos-Schild",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "info": "Blockt bis zu 25 Schaden und kontert mit 5 Schaden.",
                "effects": [{"type": "damage_reduction_flat", "target": "self", "amount": 25, "turns": 1, "chance": 1.0}],
            },
            {
                "name": "Fluch der Langsamkeit",
                "damage": [0, 0],
                "cooldown_turns": 6,
                "info": "Erhöht aktuelle Cooldowns des Spielers um +1.",
                "effects": [{"type": "cooldown_increase", "target": "enemy", "amount": 1, "chance": 1.0}],
            },
            {
                "name": "Höllenfeuer-Stoß",
                "damage": [30, 30],
                "cooldown_turns": 5,
                "info": "30 Schaden und zerstört den nächsten Defensivversuch.",
                "effects": [{"type": "special_lock", "target": "enemy", "turns": 1, "chance": 1.0}],
            },
        ],
    },
    {
        "name": "Agatha Harkness",
        "beschreibung": "Meisterin der dunklen Magie.",
        "bild": "https://i.imgur.com/M2QBK4O.png",
        "seltenheit": "Boss",
        "hp": 185,
        "mission_boss": "agatha_harkness",
        "attacks": [
            {"name": "Chaos-Energie-Ball", "damage": [22, 22], "is_standard_attack": True, "info": "Standardangriff."},
            {
                "name": "Darkhold-Fluch",
                "damage": [15, 15],
                "cooldown_turns": 4,
                "info": "15 Schaden und nächste Heilung des Spielers wird aufgehoben.",
                "effects": [{"type": "healing_block", "target": "enemy", "turns": 1, "chance": 1.0}],
            },
            {
                "name": "Lila Illusion",
                "damage": [0, 0],
                "cooldown_turns": 5,
                "info": "Nächster Spielerangriff verfehlt, Agatha kontert mit 15.",
                "effects": [
                    {"type": "evade", "target": "self", "uses": 1, "chance": 1.0},
                    {"type": "counter_flat", "target": "enemy", "damage": 15, "uses": 1, "chance": 1.0},
                ],
            },
            {
                "name": "Hexen-Sabbat",
                "damage": [35, 35],
                "cooldown_turns": 6,
                "info": "35 Schaden und legt Spezialangriffe kurz lahm.",
                "effects": [{"type": "special_lock", "target": "enemy", "turns": 1, "chance": 1.0}],
            },
        ],
    },
]


def get_operation_technischer_kollaps_encounters() -> list[dict[str, Any]]:
    return deepcopy(OPERATION_TECHNISCHER_KOLLAPS_ENCOUNTERS)


def get_operation_gruener_terror_encounters() -> list[dict[str, Any]]:
    return deepcopy(OPERATION_GRUENER_TERROR_ENCOUNTERS)


def get_operation_goldener_kaefig_encounters() -> list[dict[str, Any]]:
    return deepcopy(OPERATION_GOLDENER_KAEFIG_ENCOUNTERS)


def get_operation_hexenfeuer_encounters() -> list[dict[str, Any]]:
    return deepcopy(OPERATION_HEXENFEUER_ENCOUNTERS)
