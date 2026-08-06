"""Karten von der Website aus ändern.

Das Gegenstück zu services/card_store.py auf der Bot-Seite. Beide arbeiten
auf denselben Tabellen; hier steht die schreibende Hälfte, dort die, die die
Änderungen auf die laufenden Karten legt.

Bewusst eigener Code statt Wiederverwendung: Der Bot arbeitet mit aiosqlite,
die Website mit dem eingebauten sqlite3. Die paar Zeilen doppelt zu haben ist
weniger Ärger, als eine weitere Abhängigkeit in den Container zu ziehen.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import cards, database

# Muss zur Liste in services/card_store.py passen. Angriffe fehlen absichtlich:
# die haben eine verschachtelte Form mit Wirkungen und Abklingzeiten, und ein
# falscher Wert waere im Kampf sofort spuerbar.
AENDERBAR = ("seltenheit", "hp", "beschreibung", "bild")

GRENZEN = {
    "hp": (1, 10000),
    "beschreibung": (0, 500),
    "bild": (0, 500),
}


class EditorFehler(Exception):
    """Eingabe nicht brauchbar — Text ist für den Menschen gedacht."""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS card_overrides (
            karten_name TEXT PRIMARY KEY,
            daten_json  TEXT NOT NULL,
            geaendert_am TEXT NOT NULL,
            geaendert_von TEXT NOT NULL DEFAULT ''
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS card_override_history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            karten_name  TEXT NOT NULL,
            daten_json   TEXT NOT NULL,
            geaendert_am TEXT NOT NULL,
            geaendert_von TEXT NOT NULL DEFAULT ''
        )
    """)


def pruefe(aenderungen: dict) -> dict:
    """Eingaben prüfen, bevor irgendetwas gespeichert wird.

    Lieber hier ablehnen als spaeter im Kampf eine Karte mit -5 Lebenspunkten
    zu haben.
    """
    sauber = {}
    for feld, wert in (aenderungen or {}).items():
        if feld not in AENDERBAR:
            raise EditorFehler(f"Das Feld „{feld}“ lässt sich nicht ändern.")
        if feld == "hp":
            try:
                zahl = int(wert)
            except (TypeError, ValueError):
                raise EditorFehler("Die Lebenspunkte müssen eine ganze Zahl sein.") from None
            unten, oben = GRENZEN["hp"]
            if not unten <= zahl <= oben:
                raise EditorFehler(f"Lebenspunkte müssen zwischen {unten} und {oben} liegen.")
            sauber[feld] = zahl
            continue
        text = str(wert or "").strip()
        oben = GRENZEN.get(feld, (0, 500))[1]
        if len(text) > oben:
            raise EditorFehler(f"„{feld}“ darf höchstens {oben} Zeichen haben.")
        if feld == "bild" and text and not text.startswith(("http://", "https://")):
            raise EditorFehler("Die Bildadresse muss mit http:// oder https:// beginnen.")
        if feld == "seltenheit" and text and text not in _seltenheiten():
            raise EditorFehler(f"Die Seltenheit „{text}“ gibt es nicht. "
                               f"Möglich: {', '.join(sorted(_seltenheiten()))}")
        sauber[feld] = text
    if not sauber:
        raise EditorFehler("Es wurde nichts angegeben, was sich ändern ließe.")
    return sauber


def _seltenheiten() -> set:
    return {k.get("seltenheit") for k in cards.catalog() if k.get("seltenheit")}


def alle() -> dict:
    with database.read_connection() as con:
        try:
            zeilen = database.fetch_all(
                con, "SELECT karten_name, daten_json, geaendert_am, geaendert_von "
                     "FROM card_overrides")
        except sqlite3.OperationalError:
            return {}
    out = {}
    for z in zeilen:
        try:
            out[z["karten_name"]] = {
                "aenderungen": json.loads(z["daten_json"]),
                "geaendert_am": z["geaendert_am"],
                "geaendert_von": z["geaendert_von"],
            }
        except (ValueError, TypeError):
            continue
    return out


def setze(name: str, aenderungen: dict, *, von: str = "") -> dict:
    if not any(k.get("name") == name for k in cards.catalog()):
        raise EditorFehler(f"Die Karte „{name}“ gibt es nicht.")
    sauber = pruefe(aenderungen)
    with database.write_connection() as con:
        _schema(con)
        vorher = con.execute(
            "SELECT daten_json FROM card_overrides WHERE karten_name = ?", (name,)).fetchone()
        if vorher:
            con.execute(
                "INSERT INTO card_override_history (karten_name, daten_json, geaendert_am, "
                "geaendert_von) VALUES (?, ?, ?, ?)",
                (name, vorher["daten_json"], _jetzt(), von))
        con.execute(
            "INSERT INTO card_overrides (karten_name, daten_json, geaendert_am, geaendert_von) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(karten_name) DO UPDATE SET "
            "daten_json = excluded.daten_json, geaendert_am = excluded.geaendert_am, "
            "geaendert_von = excluded.geaendert_von",
            (name, json.dumps(sauber, ensure_ascii=False), _jetzt(), von))
    return sauber


def zuruecksetzen(name: str, *, von: str = "") -> bool:
    with database.write_connection() as con:
        _schema(con)
        vorher = con.execute(
            "SELECT daten_json FROM card_overrides WHERE karten_name = ?", (name,)).fetchone()
        if not vorher:
            return False
        con.execute(
            "INSERT INTO card_override_history (karten_name, daten_json, geaendert_am, "
            "geaendert_von) VALUES (?, ?, ?, ?)", (name, vorher["daten_json"], _jetzt(), von))
        con.execute("DELETE FROM card_overrides WHERE karten_name = ?", (name,))
    return True


def verlauf(name: str, limit: int = 20) -> list[dict]:
    with database.read_connection() as con:
        try:
            zeilen = database.fetch_all(
                con, "SELECT daten_json, geaendert_am, geaendert_von "
                     "FROM card_override_history WHERE karten_name = ? "
                     "ORDER BY id DESC LIMIT ?", (name, limit))
        except sqlite3.OperationalError:
            return []
    out = []
    for z in zeilen:
        try:
            out.append({"aenderungen": json.loads(z["daten_json"]),
                        "geaendert_am": z["geaendert_am"],
                        "geaendert_von": z["geaendert_von"]})
        except (ValueError, TypeError):
            continue
    return out
