# Beispiel für eine Karte mit Fähigkeiten, HP und Attacken:
# {
#     "name": "Drache",
#     "beschreibung": "Ein mächtiger Feuerdrache.",
#     "bild": "https://deinbildlink1.png",
#     "seltenheit": "legendär",
#     "hp": 180,
#     "attacks": [
#         {"name": "Feuerodem", "damage": [40, 60]},
#         {"name": "Kralle", "damage": [20, 40]}
#     ]
# }
karten = [
    {
        "name": "Marvel Helden",
        "beschreibung": "Die legendären Marvel-Helden vereint auf einer Karte!",
        "bild": "https://i.imgur.com/zommN1S.png",
        "seltenheit": "legendär",
        "hp": 200,
        "attacks": [
            {"name": "Helden-Schlag", "damage": [30, 50]},
            {"name": "Teamwork", "damage": [50, 70]},
            {"name": "Ultimate Combo", "damage": [65, 95]},
            {"name": "Avengers Assemble", "damage": [80, 120]}
        ]
    },
    {
        "name": "Deadpool",
        "beschreibung": "Deadpool, der legendäre Söldner mit Humor und Schwert.",
        "bild": "https://i.imgur.com/4mxNv2c.png",
        "seltenheit": "episch",
        "hp": 150,
        "attacks": [
            {"name": "Katana-Hieb", "damage": [25, 45]},
            {"name": "Pew Pew", "damage": [40, 60]},
            {"name": "Fourth Wall Break", "damage": [55, 85]},
            {"name": "Maximum Effort", "damage": [70, 110]}
        ]
    },
    {
        "name": "Adam Warlock (Leafbound)",
        "beschreibung": "Kosmischer Held mit unglaublichen Kräften.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FqF0jnHh.png&width=768&dpr=1&quality=100&sign=576f8f5c&sv=2",
        "seltenheit": "legendär",
        "hp": 180,
        "power": 54,
        "fusions": 2,
        "rating": 4,
        "serial": "AWL001",
        "realname": "Adam Warlock",
        "codename": "Adam Warlock",
        "art_edition": "Leafbound Frame",
        "frame": "Leafbound",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Kosmischer Strahl", "damage": [40, 60], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Seelenstein", "damage": [30, 50]},
            {"name": "Reality Warp", "damage": [55, 85], "effects": [{"type": "confusion", "chance": 1}]},
            {"name": "Infinity Gauntlet", "damage": [80, 120]}
        ]
    },
    {
        "name": "Adam Warlock (Crimson)",
        "beschreibung": "Kosmischer Held mit unglaublichen Kräften.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FwLctT3d.png&width=768&dpr=1&quality=100&sign=80c4f19a&sv=2",
        "seltenheit": "legendär",
        "hp": 180,
        "power": 54,
        "fusions": 2,
        "rating": 4,
        "serial": "AWL002",
        "realname": "Adam Warlock",
        "codename": "Adam Warlock",
        "art_edition": "Crimson Leaf Frame",
        "frame": "Crimson Leaf",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Kosmischer Strahl", "damage": [40, 60], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Seelenstein", "damage": [30, 50]},
            {"name": "Reality Warp", "damage": [55, 85]},
            {"name": "Infinity Gauntlet", "damage": [80, 120]}
        ]
    },
    {
        "name": "Hawkeye (Galaxy)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2F8rsSt7b.png&width=768&dpr=1&quality=100&sign=fbdfae2c&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK001",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Galaxy Window Frame",
        "frame": "Galaxy Window",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Sparking Gold)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2F8O49tG3.png&width=768&dpr=4&quality=100&sign=1a369b1b&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK002",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Sparking Gold Frame",
        "frame": "Sparking Gold",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Cosmic Repose)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FqWBsO9B.png&width=768&dpr=4&quality=100&sign=30dba2ce&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK003",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Cosmic Repose Frame",
        "frame": "Cosmic Repose",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Unfruited Repose)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FipLmbBI.png&width=768&dpr=4&quality=100&sign=bb88e92c&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK004",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Unfruited Repose Frame",
        "frame": "Unfruited Repose",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Black Liquid)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FG1TDxSk.png&width=768&dpr=4&quality=100&sign=92a168ed&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK005",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Black Liquid Frame",
        "frame": "Black Liquid",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Fantastic Blue)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FYrjVSWj.png&width=768&dpr=4&quality=100&sign=b9a0598c&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK006",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Fantastic Blue Frame",
        "frame": "Fantastic Blue",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Golden Rust)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FJ9PLQUF.png&width=768&dpr=4&quality=100&sign=8bb8dc2e&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK007",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Golden Rust Frame",
        "frame": "Golden Rust",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Vibranium Tech)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FIbJy09Y.png&width=768&dpr=4&quality=100&sign=a682de8b&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK008",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Vibranium Tech Frame",
        "frame": "Vibranium Tech",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Dimensional Ripple)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FnBJMS2v.png&width=768&dpr=4&quality=100&sign=32d2ac51&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK009",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Dimensional Ripple Frame",
        "frame": "Dimensional Ripple",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75], "effects": [{"type": "confusion", "chance": 0.65}]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Resonator Black)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FFsgTXgQ.png&width=768&dpr=4&quality=100&sign=c6c4431b&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK010",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Resonator Black Frame",
        "frame": "Resonator Black",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Resonator Orkibod)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FwUoQSAv.png&width=768&dpr=4&quality=100&sign=afc0f311&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK011",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Resonator Orkibod Frame",
        "frame": "Resonator Orkibod",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Hawkeye (Cobblestone)",
        "beschreibung": "Der beste Bogenschütze der Welt.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FdIZKni3.png&width=768&dpr=4&quality=100&sign=756fa3f1&sv=2",
        "seltenheit": "episch",
        "hp": 140,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "HWK012",
        "realname": "Clint Barton",
        "codename": "Hawkeye",
        "art_edition": "Cobblestone Frame",
        "frame": "Cobblestone",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Pfeilhagel", "damage": [25, 45]},
            {"name": "Trickpfeil", "damage": [35, 55], "effects": [{"type": "burning", "chance": 0.8, "duration": [2, 4], "damage": 15}]},
            {"name": "Bullseye Shot", "damage": [45, 75]},
            {"name": "Arrow Storm", "damage": [60, 100]}
        ]
    },
    {
        "name": "Captain America (Red Stripe)",
        "beschreibung": "Der erste Avenger mit unzerstörbarem Schild.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FmdQgfas.png&width=768&dpr=1&quality=100&sign=f90edc00&sv=2",
        "seltenheit": "episch",
        "hp": 160,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "CAP001",
        "realname": "Steve Rogers",
        "codename": "Captain America",
        "art_edition": "Red Stripe Frame",
        "frame": "Red Stripe",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Schildwurf", "damage": [30, 50]}, 
            {"name": "Patriotischer Schlag", "damage": [20, 40], "effects": [{"type": "confusion", "chance": 0.65}]} 
        ]
    },
    {
        "name": "Captain America (Red Stripe 2)",
        "beschreibung": "Der erste Avenger mit unzerstörbarem Schild.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2F4jcLuXt.png&width=768&dpr=1&quality=100&sign=bca802df&sv=2",
        "seltenheit": "episch",
        "hp": 160,
        "power": 30,
        "fusions": 1,
        "rating": 3,
        "serial": "CAP002",
        "realname": "Steve Rogers",
        "codename": "Captain America",
        "art_edition": "Red Stripe Frame",
        "frame": "Red Stripe",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Schildwurf", "damage": [30, 50]},
            {"name": "Patriotischer Schlag", "damage": [20, 40], "effects": [{"type": "confusion", "chance": 0.65}]} 
        ]
    },
    {
        "name": "Black Widow",
        "beschreibung": "Spionin mit Praezision, Taser und Tarnung.",
        "bild": "https://i.imgur.com/0u0GRC9.png",
        "seltenheit": "episch",
        "hp": 150,
        "attacks": [
            {"name": "Treten", "damage": [10, 15]},
            {"name": "Taser", "damage": [5, 25]},
            {"name": "Präzisionsschuss", "damage": [20, 35], "requires_reload": True, "reload_name": "Nachladen"},
            {"name": "Tarnung", "damage": [0, 0], "cooldown_turns": 2, "effects": [{"type": "stealth", "target": "self", "chance": 1.0}]}
        ]
    },
    {
        "name": "Storm (Kitty)",
        "beschreibung": "Herrscherin über Blitz und Donner.",
        "bild": "https://helicord.gitbook.io/helicord/~gitbook/image?url=https%3A%2F%2Fi.imgur.com%2FAT6Mnvx.png&width=768&dpr=1&quality=100&sign=f29034ef&sv=2",
        "seltenheit": "legendär",
        "hp": 170,
        "power": 71,
        "fusions": 1,
        "rating": 4,
        "serial": "STM001",
        "realname": "Ororo Munroe",
        "codename": "Storm",
        "art_edition": "Kitty Frame",
        "frame": "Kitty",
        "edition_source": "Marvel Snap",
        "artists": ["Marvel"],
        "attacks": [
            {"name": "Blitzschlag", "damage": [40, 70]},
            {"name": "Sturmwind", "damage": [25, 45], "effects": [{"type": "confusion", "chance": 0.65}]}
        ]
    }
] 
