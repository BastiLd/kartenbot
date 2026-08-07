"""Kontrollkampf gegen die KI — die Website-Hälfte (Stufe 5, Schritt 10).

Hier wird **nicht gerechnet**, hier wird nur geprüft und ein Auftrag angelegt.
Gekämpft wird im Bot (``bot.py``, ``_run_ki_kontrollkampf``).

**Warum nicht hier**, obwohl der Zugang zum Sprachmodell hier liegt: Die
Kampf-Engine braucht über ``services/combat_runner.py`` das ganze ``bot.py``
und damit discord.py und aiosqlite. Beides ist im Backend-Abbild bewusst
nicht drin — es enthält nur die Module, die für die Kartenprüfung nötig sind.
Ein Kampf hier würde beim ersten Zug abstürzen.

Dazu kommt derselbe Grund wie beim Testlauf: Nur der Bot hat die Karten mit
allen Änderungen, die über diese Seite gemacht wurden.
"""
from __future__ import annotations

from . import cards, missionen, settings

GEGENSPIELER = {
    "optimal": "bestmöglich — jeder Zug so gut es geht",
    "average": "wie Menschen, mit Fehlern",
}


class KampfFehler(Exception):
    """Der Kontrollkampf kann so nicht laufen — Text ist für den Menschen."""


def _kennt(name: str) -> bool:
    gesucht = str(name or "").strip()
    if any(k.get("name") == gesucht for k in cards.catalog()):
        return True
    return gesucht in missionen.namen()


def moeglichkeiten() -> dict:
    """Was sich einstellen lässt — für die Oberfläche."""
    modell = settings.get("ollama.model_kampf") or settings.get("ollama.model")
    return {
        "gegenspieler": [{"wert": w, "text": t} for w, t in GEGENSPIELER.items()],
        "modell": modell,
        "ki_an": settings.get_bool("ai.enabled"),
        "bereit": bool(modell and settings.get_bool("ai.enabled")),
    }


def pruefe(karte: str, gegner: str, gegenspieler: str) -> dict:
    """Eingaben abnehmen, bevor ein Auftrag angelegt wird.

    Alles, was sich hier schon erkennen lässt, wird hier gemeldet — sonst
    stünde der Fehler erst in einem Auftrag, der Minuten später drankommt.
    """
    if not settings.get_bool("ai.enabled"):
        raise KampfFehler("Die KI-Auswertung ist ausgeschaltet. Der Schalter steht "
                          "in den Einstellungen unter „KI“.")
    if not (settings.get("ollama.model_kampf") or settings.get("ollama.model")):
        raise KampfFehler("Es ist kein Modell für den Kampf ausgewählt. Der "
                          "Modell-Finder in den Einstellungen sucht eines.")
    if gegenspieler not in GEGENSPIELER:
        raise KampfFehler(f"Als Gegenspieler geht: {', '.join(GEGENSPIELER)}.")

    name = str(karte or "").strip()
    if not _kennt(name):
        raise KampfFehler(f"„{name}“ gibt es weder bei den Helden noch bei den Schurken.")
    gegner_name = str(gegner or "").strip()
    if gegner_name and not _kennt(gegner_name):
        raise KampfFehler(f"„{gegner_name}“ gibt es weder bei den Helden "
                          f"noch bei den Schurken.")
    if gegner_name and gegner_name == name:
        raise KampfFehler("Eine Karte kann nicht gegen sich selbst antreten.")
    return {"karte": name, "gegner": gegner_name, "gegenspieler": gegenspieler}
