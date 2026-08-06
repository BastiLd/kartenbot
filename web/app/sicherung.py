"""Sichern, Papierkorb und Wiederherstellen.

Drei Dinge, die zusammengehören: Was gelöscht wird, soll nicht sofort weg
sein. Was in großer Zahl geändert wird, soll sich zurückholen lassen. Und
von allem soll man eine Kopie ziehen können, bevor man etwas Größeres tut.

Grundgedanke: Bevor Zeilen verschwinden oder sich ändern, werden sie als
Text festgehalten. Zurückholen heißt dann: dieselben Zeilen wieder
einsetzen. Das ist unspektakulär, aber es funktioniert auch dann noch, wenn
niemand mehr weiß, was damals eigentlich passiert ist.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, database

# So lange bleibt Gelöschtes zurückholbar. Danach räumt die Ablage sich selbst
# auf — sonst wüchse sie ewig.
AUFBEWAHRUNG_TAGE = 30


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS web_papierkorb (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            erstellt_am TEXT NOT NULL,
            art         TEXT NOT NULL,      -- 'spieler-geloescht', 'massenaktion'
            titel       TEXT NOT NULL,      -- was ein Mensch darunter versteht
            betrifft    TEXT NOT NULL DEFAULT '',
            daten_json  TEXT NOT NULL,      -- die gesicherten Zeilen
            actor       TEXT NOT NULL DEFAULT '',
            zurueckgeholt_am TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_papierkorb_zeit "
                "ON web_papierkorb (erstellt_am DESC)")


def _aufraeumen(con: sqlite3.Connection) -> int:
    """Alte Einträge wegwerfen. Wird bei jedem Schreiben nebenbei erledigt."""
    grenze = (_jetzt() - timedelta(days=AUFBEWAHRUNG_TAGE)).isoformat(timespec="seconds")
    cursor = con.execute("DELETE FROM web_papierkorb WHERE erstellt_am < ?", (grenze,))
    return cursor.rowcount or 0


def lege_ab(*, art: str, titel: str, daten: list[dict], betrifft: str = "",
            actor: str = "") -> int | None:
    """Etwas in den Papierkorb legen. Gibt die Nummer zurück, unter der es liegt."""
    if not daten:
        return None
    with database.write_connection() as con:
        ensure_schema(con)
        _aufraeumen(con)
        cursor = con.execute(
            "INSERT INTO web_papierkorb (erstellt_am, art, titel, betrifft, daten_json, actor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_jetzt().isoformat(timespec="seconds"), art, titel, betrifft,
             json.dumps(daten, ensure_ascii=False), actor))
        return cursor.lastrowid


def sammle_zeilen(con: sqlite3.Connection, tabellen: tuple, uid: int) -> list[dict]:
    """Alles zu einer Person zusammentragen, bevor es gelöscht wird.

    Jede Zeile merkt sich, aus welcher Tabelle sie stammt — sonst wüsste
    später niemand mehr, wohin sie zurückgehört.
    """
    gesichert = []
    for tabelle, spalte in tabellen:
        try:
            zeilen = con.execute(
                f"SELECT * FROM {tabelle} WHERE {spalte} = ?", (uid,)).fetchall()
        except sqlite3.OperationalError:
            continue          # Tabelle gibt es nicht — dann gibt es auch nichts zu sichern
        for zeile in zeilen:
            gesichert.append({"tabelle": tabelle, "zeile": dict(zeile)})
    return gesichert


def liste(limit: int = 50) -> list[dict]:
    # daten_json kommt mit, damit die Anzahl ohne weitere Abfrage feststeht -
    # sonst waere es eine Abfrage je Zeile.
    with database.read_connection() as con:
        try:
            zeilen = database.fetch_all(
                con,
                "SELECT id, erstellt_am, art, titel, betrifft, actor, zurueckgeholt_am, "
                "daten_json FROM web_papierkorb ORDER BY erstellt_am DESC LIMIT ?", (limit,))
        except sqlite3.OperationalError:
            return []                     # Tabelle gibt es noch nicht - dann ist er leer
    for z in zeilen:
        try:
            z["anzahl"] = len(json.loads(z.pop("daten_json") or "[]"))
        except (ValueError, TypeError):
            z["anzahl"] = 0
            z.pop("daten_json", None)
    return zeilen


def _daten(eintrag_id: int) -> str:
    with database.read_connection() as con:
        zeile = database.fetch_one(
            con, "SELECT daten_json FROM web_papierkorb WHERE id = ?", (int(eintrag_id),))
    return zeile["daten_json"] if zeile else "[]"


def hole_zurueck(eintrag_id: int) -> dict:
    """Die gesicherten Zeilen wieder einsetzen.

    Bewusst mit INSERT OR REPLACE: Wenn zwischenzeitlich schon wieder etwas
    für dieselbe Person angelegt wurde, gewinnt der gesicherte Stand. Alles
    andere waere ein halb zurueckgeholter Zustand, und der ist schlimmer als
    beide Alternativen.
    """
    roh = _daten(eintrag_id)
    eintraege = json.loads(roh) if roh else []
    if not eintraege:
        raise ValueError("Zu diesem Eintrag ist nichts gespeichert.")

    zurueck: dict[str, int] = {}
    fehler: list[str] = []
    with database.write_connection() as con:
        ensure_schema(con)
        for eintrag in eintraege:
            tabelle = eintrag.get("tabelle")
            zeile = eintrag.get("zeile") or {}
            if not tabelle or not zeile:
                continue
            spalten = ", ".join(zeile.keys())
            platzhalter = ", ".join("?" for _ in zeile)
            try:
                con.execute(f"INSERT OR REPLACE INTO {tabelle} ({spalten}) "
                            f"VALUES ({platzhalter})", tuple(zeile.values()))
                zurueck[tabelle] = zurueck.get(tabelle, 0) + 1
            except sqlite3.Error as exc:
                fehler.append(f"{tabelle}: {exc}")
                logging.warning("Zurueckholen aus %s fehlgeschlagen: %s", tabelle, exc)
        con.execute("UPDATE web_papierkorb SET zurueckgeholt_am = ? WHERE id = ?",
                    (_jetzt().isoformat(timespec="seconds"), int(eintrag_id)))

    return {"zurueckgeholt": zurueck,
            "gesamt": sum(zurueck.values()),
            "fehler": fehler}


def wirf_weg(eintrag_id: int) -> bool:
    with database.write_connection() as con:
        ensure_schema(con)
        cursor = con.execute("DELETE FROM web_papierkorb WHERE id = ?", (int(eintrag_id),))
    return bool(cursor.rowcount)


# --------------------------------------------------------------------------
# Kopie der Datenbank
# --------------------------------------------------------------------------
def erstelle_kopie() -> Path:
    """Eine in sich stimmige Kopie der Datenbank anlegen.

    Nicht einfach die Datei kopieren: Der Bot schreibt weiter, und eine Kopie
    mitten in einer Buchung waere unbrauchbar. SQLite hat dafuer eine eigene
    Sicherungsfunktion, die einen sauberen Stand herausgibt, auch waehrend
    geschrieben wird.
    """
    quelle = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    try:
        ziel_datei = Path(tempfile.gettempdir()) / (
            f"kartenbot-sicherung-{_jetzt().strftime('%Y-%m-%d-%H%M')}.db")
        ziel = sqlite3.connect(str(ziel_datei))
        try:
            quelle.backup(ziel)
        finally:
            ziel.close()
    finally:
        quelle.close()
    return ziel_datei


def pruefe_datenbank() -> dict:
    """Nachsehen, ob die Datenbank in Ordnung ist."""
    with database.read_connection() as con:
        ergebnis = con.execute("PRAGMA integrity_check").fetchone()[0]
        fremd = con.execute("PRAGMA foreign_key_check").fetchall()
        tabellen = database.scalar(
            con, "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'")
    groesse = Path(config.DB_PATH).stat().st_size if Path(config.DB_PATH).exists() else 0
    return {
        "in_ordnung": ergebnis == "ok" and not fremd,
        "ergebnis": ergebnis,
        "verwaiste_verweise": len(fremd),
        "tabellen": tabellen,
        "groesse": groesse,
    }
