"""Kartenänderungen aus der Datenbank, ohne karten.py anzufassen.

Warum so und nicht anders:

karten.py ist die Quelle aller Karten und wird von acht Modulen beim Start
eingelesen. Diese Datei in die Datenbank zu verlagern hiesse, jeden dieser
Aufrufer umzubauen — viel Risiko für wenig Gewinn, denn die allermeisten
Karten ändern sich nie.

Stattdessen bleibt karten.py die Grundlage, und die Datenbank hält nur die
Abweichungen davon. Wer nichts ändert, merkt von alledem nichts.

Der eigentliche Kniff steckt in `anwenden()`: Alle Module haben mit
`from karten import karten` **dasselbe Listenobjekt** gebunden. Wird diese
Liste an Ort und Stelle geändert (`karten[:] = ...`), sehen es alle sofort —
ohne Neustart, ohne dass ein einziges dieser Module davon wissen muss.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from db import db_context

# Diese Felder darf die Website ändern. Bewusst eng: Angriffe haben eine
# eigene, verschachtelte Form mit Wirkungen und Abklingzeiten — dort wäre ein
# falscher Wert im Kampf sofort spürbar. Das kommt später und mit Prüfung.
AENDERBAR = ("seltenheit", "hp", "beschreibung", "bild")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ensure_schema() -> None:
    async with db_context() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS card_overrides (
                karten_name TEXT PRIMARY KEY,
                daten_json  TEXT NOT NULL,
                geaendert_am TEXT NOT NULL,
                geaendert_von TEXT NOT NULL DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS card_override_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                karten_name  TEXT NOT NULL,
                daten_json   TEXT NOT NULL,
                geaendert_am TEXT NOT NULL,
                geaendert_von TEXT NOT NULL DEFAULT ''
            )
        """)
        await db.commit()


async def alle() -> dict[str, dict]:
    """Alle gespeicherten Abweichungen, nach Kartenname."""
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute("SELECT karten_name, daten_json FROM card_overrides")
        zeilen = await cursor.fetchall()
    out = {}
    for name, roh in zeilen:
        try:
            out[name] = json.loads(roh)
        except (ValueError, TypeError):
            logging.warning("Kartenänderung für %s ist unlesbar und wird übergangen", name)
    return out


async def setze(name: str, aenderungen: dict, *, von: str = "") -> dict:
    """Eine Abweichung festhalten. Der vorherige Stand wandert in den Verlauf."""
    await ensure_schema()
    sauber = {k: v for k, v in (aenderungen or {}).items() if k in AENDERBAR}
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT daten_json FROM card_overrides WHERE karten_name = ?", (name,))
        vorher = await cursor.fetchone()
        if vorher:
            # Erst den alten Stand wegschreiben, dann überschreiben — so lässt
            # sich jede Änderung zurücknehmen, auch die vorletzte.
            await db.execute(
                "INSERT INTO card_override_history (karten_name, daten_json, geaendert_am, "
                "geaendert_von) VALUES (?, ?, ?, ?)", (name, vorher[0], _jetzt(), von))
        await db.execute(
            "INSERT INTO card_overrides (karten_name, daten_json, geaendert_am, geaendert_von) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(karten_name) DO UPDATE SET "
            "daten_json = excluded.daten_json, geaendert_am = excluded.geaendert_am, "
            "geaendert_von = excluded.geaendert_von",
            (name, json.dumps(sauber, ensure_ascii=False), _jetzt(), von))
        await db.commit()
    return sauber


async def zuruecksetzen(name: str, *, von: str = "") -> bool:
    """Eine Karte auf den Stand aus karten.py zurückbringen."""
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT daten_json FROM card_overrides WHERE karten_name = ?", (name,))
        vorher = await cursor.fetchone()
        if not vorher:
            return False
        await db.execute(
            "INSERT INTO card_override_history (karten_name, daten_json, geaendert_am, "
            "geaendert_von) VALUES (?, ?, ?, ?)", (name, vorher[0], _jetzt(), von))
        await db.execute("DELETE FROM card_overrides WHERE karten_name = ?", (name,))
        await db.commit()
    return True


async def verlauf(name: str, limit: int = 20) -> list[dict]:
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT daten_json, geaendert_am, geaendert_von FROM card_override_history "
            "WHERE karten_name = ? ORDER BY id DESC LIMIT ?", (name, limit))
        zeilen = await cursor.fetchall()
    out = []
    for roh, wann, wer in zeilen:
        try:
            out.append({"daten": json.loads(roh), "geaendert_am": wann, "geaendert_von": wer})
        except (ValueError, TypeError):
            continue
    return out


async def anwenden(karten_liste: list) -> int:
    """Die Abweichungen auf die laufende Kartenliste legen.

    Verändert die Liste an Ort und Stelle. Genau das ist der Punkt: Alle
    Module halten dieselbe Liste, also wirkt die Änderung sofort überall —
    ohne dass der Bot neu starten muss.

    Gibt zurück, wie viele Karten angepasst wurden.
    """
    abweichungen = await alle()
    if not abweichungen:
        return 0
    getroffen = 0
    for karte in karten_liste:
        aenderung = abweichungen.get(karte.get("name"))
        if not aenderung:
            continue
        for feld, wert in aenderung.items():
            if feld in AENDERBAR:
                karte[feld] = wert
        getroffen += 1
    if getroffen:
        logging.info("%s Karten aus der Datenbank angepasst", getroffen)
    return getroffen
