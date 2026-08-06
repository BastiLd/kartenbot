"""Gegner-Versionen verwalten — die Website-Hälfte.

Gegenstück zu ``services/bot_versions.py`` im Bot: Beide arbeiten auf
denselben Tabellen, hier steht die schreibende Seite. Gleiche Aufteilung wie
beim Karten-Editor, und aus demselben Grund — der Bot nutzt aiosqlite, die
Website das eingebaute sqlite3.

„Standard" ist fest eingebaut und steht nicht in der Tabelle. Er lässt sich
deshalb weder ändern noch löschen; das ist Absicht: Es muss immer einen Weg
zurück zu „spielt wie immer" geben.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import database

STANDARD_NAME = "Standard"
ALLE = "*"

# Über dieser Fehlerquote wird der Gegner sinnlos: Er würfelt dann mehr, als
# er spielt. 0,8 heisst schon, dass er in vier von fünf Zügen absichtlich
# danebengreift.
MAX_FEHLERQUOTE = 0.8


class VersionFehler(Exception):
    """Eingabe nicht brauchbar — Text ist für den Menschen gedacht."""


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def standard() -> dict:
    return {"id": 0, "name": STANDARD_NAME,
            "beschreibung": "Spielt so gut er kann — der Bot, wie es ihn immer gab.",
            "fehlerquote": 0.0, "gewichte": {}, "ist_standard": True,
            "loeschbar": False}


def _zeile(row) -> dict:
    def _laden(roh):
        try:
            wert = json.loads(roh or "{}")
            return wert if isinstance(wert, dict) else {}
        except (TypeError, ValueError):
            return {}

    return {
        "id": row["id"], "name": row["name"], "beschreibung": row["beschreibung"],
        "fehlerquote": float(row["fehlerquote"] or 0.0),
        "gewichte": _laden(row["gewichte_json"]),
        "lernstand": _laden(row["lernstand_json"]),
        "erstellt_am": row["erstellt_am"], "geaendert_am": row["geaendert_am"],
        "ist_standard": False, "loeschbar": True,
    }


def alle() -> list[dict]:
    with database.read_connection() as con:
        zeilen = database.fetch_all(
            con, "SELECT * FROM bot_versions ORDER BY name")
    return [standard()] + [_zeile(z) for z in zeilen]


def aktive_zuordnung() -> dict[str, int | None]:
    """Welche Version wo gilt. Schlüssel ist die Server-ID oder ``*``."""
    with database.read_connection() as con:
        zeilen = database.fetch_all(con, "SELECT guild_id, version_id FROM bot_version_aktiv")
    return {str(z["guild_id"]): z["version_id"] for z in zeilen}


def _pruefe(name: str, beschreibung: str, fehlerquote) -> tuple[str, str, float]:
    sauber = str(name or "").strip()
    if not sauber:
        raise VersionFehler("Die Version braucht einen Namen.")
    if len(sauber) > 60:
        raise VersionFehler("Der Name darf höchstens 60 Zeichen haben.")
    if sauber.lower() == STANDARD_NAME.lower():
        raise VersionFehler(f"„{STANDARD_NAME}“ ist fest eingebaut — "
                            f"nimm einen anderen Namen.")
    text = str(beschreibung or "").strip()
    if len(text) > 300:
        raise VersionFehler("Die Beschreibung darf höchstens 300 Zeichen haben. "
                            "Sie steht im Discord in der Auswahlliste.")
    try:
        quote = float(fehlerquote)
    except (TypeError, ValueError):
        raise VersionFehler("Die Fehlerquote muss eine Zahl sein.") from None
    if not 0 <= quote <= MAX_FEHLERQUOTE:
        raise VersionFehler(f"Die Fehlerquote muss zwischen 0 und "
                            f"{MAX_FEHLERQUOTE} liegen. 0 = spielt immer den "
                            f"besten Zug, 0,3 = greift in rund jedem dritten "
                            f"Zug daneben.")
    return sauber, text, round(quote, 3)


def anlegen(name: str, beschreibung: str = "", fehlerquote: float = 0.0) -> dict:
    sauber, text, quote = _pruefe(name, beschreibung, fehlerquote)
    with database.write_connection() as con:
        try:
            cursor = con.execute(
                "INSERT INTO bot_versions (name, beschreibung, fehlerquote, erstellt_am, "
                "geaendert_am) VALUES (?, ?, ?, ?, ?)",
                (sauber, text, quote, _jetzt(), _jetzt()))
        except sqlite3.IntegrityError:
            raise VersionFehler(f"Eine Version namens „{sauber}“ gibt es schon.") from None
        neue_id = cursor.lastrowid
    return next(v for v in alle() if v["id"] == neue_id)


def aendern(version_id: int, name: str, beschreibung: str, fehlerquote: float) -> dict:
    if not version_id:
        raise VersionFehler(f"„{STANDARD_NAME}“ lässt sich nicht ändern. "
                            f"Lege eine Kopie an und ändere die.")
    sauber, text, quote = _pruefe(name, beschreibung, fehlerquote)
    with database.write_connection() as con:
        try:
            cursor = con.execute(
                "UPDATE bot_versions SET name = ?, beschreibung = ?, fehlerquote = ?, "
                "geaendert_am = ? WHERE id = ?",
                (sauber, text, quote, _jetzt(), int(version_id)))
        except sqlite3.IntegrityError:
            raise VersionFehler(f"Eine Version namens „{sauber}“ gibt es schon.") from None
        if not cursor.rowcount:
            raise VersionFehler("Diese Version gibt es nicht (mehr).")
    return next(v for v in alle() if v["id"] == int(version_id))


def kopieren(version_id: int, neuer_name: str) -> dict:
    vorlage = next((v for v in alle() if v["id"] == int(version_id)), None)
    if vorlage is None:
        raise VersionFehler("Diese Version gibt es nicht (mehr).")
    return anlegen(neuer_name, vorlage["beschreibung"], vorlage["fehlerquote"])


def loeschen(version_id: int) -> bool:
    if not version_id:
        raise VersionFehler(f"„{STANDARD_NAME}“ lässt sich nicht löschen — "
                            f"es muss immer einen Weg zurück geben.")
    with database.write_connection() as con:
        # Wo diese Version aktiv war, gilt ab jetzt wieder Standard.
        con.execute("DELETE FROM bot_version_aktiv WHERE version_id = ?", (int(version_id),))
        cursor = con.execute("DELETE FROM bot_versions WHERE id = ?", (int(version_id),))
        return cursor.rowcount > 0


def setze_aktiv(guild_id: str | None, version_id: int | None) -> dict:
    """Welche Version wo gilt. ``guild_id`` leer heisst: für alle Server."""
    schluessel = str(guild_id or ALLE)
    if version_id and not any(v["id"] == int(version_id) for v in alle()):
        raise VersionFehler("Diese Version gibt es nicht (mehr).")
    with database.write_connection() as con:
        con.execute(
            "INSERT INTO bot_version_aktiv (guild_id, version_id, gesetzt_am) "
            "VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "version_id = excluded.version_id, gesetzt_am = excluded.gesetzt_am",
            (schluessel, int(version_id) if version_id else None, _jetzt()))
    return {"guild_id": schluessel, "version_id": int(version_id) if version_id else 0}
