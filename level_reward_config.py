"""
Belohnungen für MEE6-Level und für Einladungen („Werbt einen Freund“).

Das ist die EINZIGE Stelle, an der diese Belohnungen stehen. Wer etwas ändern
will, ändert es hier — sonst nirgends.

So liest man die Einträge:
- Design("Black Widow", 2)  = Design 2 der Karte „Black Widow“ wird freigeschaltet.
                              Ein Design ist nur ein anderes Aussehen, keine
                              neue Karte. Die Karte muss genau so heißen wie in
                              karten.py.
- Staub(5)                  = 5 Infinitydust.

Ein Test prüft, dass jede Karte hier auch wirklich existiert.
"""
from __future__ import annotations

from typing import NamedTuple


class Design(NamedTuple):
    karte: str
    nummer: int = 2


class Staub(NamedTuple):
    menge: int


# ---------------------------------------------------------------------------
# MEE6-Level
# ---------------------------------------------------------------------------
# Level -> Titel. Der Titel ist zugleich der Name der MEE6-Rolle: /level-einrichten
# sucht auf dem Server nach Rollen mit genau diesen Namen (Groß/klein egal).
LEVEL_STUFEN = {
    1: "Einwohner von New York",
    5: "Stark Industries Praktikant",
    10: "S.H.I.E.L.D. Agenten",
    15: "Howling Commandos",
    20: "Defenders von Hell's Kitchen",
    25: "Meister der Mystischen Künste",
    30: "Wakandische Dora Milaje",
    35: "Asgardische Krieger",
    40: "Guardians of the Galaxy",
    45: "Inhumans Royal Guard",
    50: "Avengers Initiative",
}

# Level -> was der Bot dafür vergibt. Die Belohnungen zählen zusammen: Wer
# Level 35 hat, bekommt alles bis einschließlich Level 35.
# Level ohne Eintrag bringen im Bot nichts (nur in Discord, siehe unten).
LEVEL_BELOHNUNGEN = {
    5: [Design("Black Widow", 2)],
    15: [Design("Rocket", 2)],
    20: [Design("The Thing", 2)],
    35: [Design("Doctor Strange", 2)],
    40: [Design("Groot", 2)],
    45: [Design("Captain Marvel", 2)],
    50: [Design("Loki", 2)],
}

# Was eine Stufe AUSSERHALB des Bots bringt. Das richtet ihr in Discord bzw.
# MEE6 ein — der Bot zeigt es in /level nur an.
LEVEL_HINWEISE = {
    1: "Einstieg",
    10: "Bilder und Links im Chat",
    25: "Zugang zum Kanal „Sanctum Sanctorum“",
    30: "Kanal „Archiv“ mit exklusiven Einblicken",
}

# ---------------------------------------------------------------------------
# Einladungen („Werbt einen Freund“)
# ---------------------------------------------------------------------------
# Anzahl bestätigter Einladungen -> was der EINLADER dafür bekommt.
EINLADUNG_STUFEN = {
    1: [Design("Captain America", 2)],
    5: [Design("Spider-Man", 2), Staub(5)],
    10: [Design("Scarlet Witch", 2), Design("Namor", 2), Staub(10)],
}

# Jede Einladung OHNE eigene Stufe (also 2, 3, 4, 6, 7, 8, 9, 11, 12, …)
# bringt dem Einlader diesen Staub.
EINLADUNG_STAUB_SONST = 5

# Der EINGELADENE bekommt bei jeder bestätigten Einladung diesen Staub.
EINGELADENER_STAUB = 5
