"""Gegner-Versionen: benannte Einstellungen für den Bot im Kampf.

Eine Version ist ein Satz Einstellungen mit einem Namen und einer
Beschreibung — „Standard", „Schwer", „Übungsgegner". Damit lässt sich der
Bot verschieden schwer machen, ohne im Code etwas zu ändern.

**Voreingestellt ändert sich nichts.** „Standard" ist fest eingebaut, hat
Fehlerquote 0 und lässt sich nicht löschen. Solange keine andere Version
aktiv ist, spielt der Bot exakt wie bisher.

Die **Fehlerquote** ist das, was heute schon wirkt: Mit dieser
Wahrscheinlichkeit nimmt der Bot nicht den besten Zug, sondern einen anderen
möglichen. Dasselbe Prinzip wie ``AverageStrategy`` in der Simulation — nur
dort, wo der Bot im echten Spiel entscheidet.

Die **Gewichte** sind vorbereitet, aber noch ohne Wirkung: Sie sind der
Platz, an dem später das Gelernte landet (Stufe 5, Schritt 9).
"""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone

from db import db_context

STANDARD_NAME = "Standard"

# Wie lange die aktive Version zwischengespeichert wird. Sie bei jedem Zug
# neu aus der Datenbank zu holen waere Verschwendung; eine Minute Verzoegerung
# beim Umstellen faellt niemandem auf.
CACHE_S = 60

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS bot_versions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        beschreibung  TEXT NOT NULL DEFAULT '',
        fehlerquote   REAL NOT NULL DEFAULT 0.0,
        gewichte_json TEXT NOT NULL DEFAULT '{}',
        lernstand_json TEXT NOT NULL DEFAULT '{}',
        erstellt_am   TEXT NOT NULL,
        geaendert_am  TEXT NOT NULL,
        ist_standard  INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_versions_name ON bot_versions(name)",
    """
    CREATE TABLE IF NOT EXISTS bot_version_aktiv (
        guild_id   TEXT PRIMARY KEY,
        version_id INTEGER,
        gesetzt_am TEXT NOT NULL
    )
    """,
)

# Fuer "gilt fuer alle Server" - eine eigene Zeile waere sauberer als ein
# Sonderwert, aber so bleibt die Abfrage ein einziges SELECT.
ALLE = "*"

_schema_ready = False
_cache: dict[str, tuple[float, dict]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def standard() -> dict:
    """Die eingebaute Version: keine Fehler, keine Gewichte."""
    return {"id": 0, "name": STANDARD_NAME, "beschreibung":
            "Spielt so gut er kann — der Bot, wie es ihn immer gab.",
            "fehlerquote": 0.0, "gewichte": {}, "ist_standard": True}


async def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with db_context() as db:
        for anweisung in _SCHEMA:
            await db.execute(anweisung)
        await db.commit()
    _schema_ready = True


def _als_dict(zeile) -> dict:
    def _laden(roh):
        try:
            wert = json.loads(roh or "{}")
            return wert if isinstance(wert, dict) else {}
        except (TypeError, ValueError):
            return {}

    return {
        "id": zeile[0], "name": zeile[1], "beschreibung": zeile[2],
        "fehlerquote": float(zeile[3] or 0.0),
        "gewichte": _laden(zeile[4]), "lernstand": _laden(zeile[5]),
        "erstellt_am": zeile[6], "geaendert_am": zeile[7],
        "ist_standard": bool(zeile[8]),
    }


async def alle() -> list[dict]:
    """Alle gespeicherten Versionen, „Standard" immer zuerst."""
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT id, name, beschreibung, fehlerquote, gewichte_json, lernstand_json, "
            "erstellt_am, geaendert_am, ist_standard FROM bot_versions ORDER BY name")
        zeilen = await cursor.fetchall()
    return [standard()] + [_als_dict(z) for z in zeilen]


async def hole(version_id: int) -> dict | None:
    if not version_id:
        return standard()
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT id, name, beschreibung, fehlerquote, gewichte_json, lernstand_json, "
            "erstellt_am, geaendert_am, ist_standard FROM bot_versions WHERE id = ?",
            (int(version_id),))
        zeile = await cursor.fetchone()
    return _als_dict(zeile) if zeile else None


async def aktive(guild_id=None) -> dict:
    """Welche Version auf diesem Server gilt.

    Reihenfolge: erst die Einstellung für genau diesen Server, dann die für
    alle, sonst „Standard". Im Zweifel immer „Standard" — ein Fehler beim
    Nachschlagen darf nie dazu führen, dass der Bot anders spielt als erwartet.
    """
    schluessel = str(guild_id or ALLE)
    gemerkt = _cache.get(schluessel)
    if gemerkt and time.monotonic() - gemerkt[0] < CACHE_S:
        return gemerkt[1]
    try:
        await ensure_schema()
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT version_id FROM bot_version_aktiv WHERE guild_id IN (?, ?) "
                "ORDER BY CASE guild_id WHEN ? THEN 0 ELSE 1 END LIMIT 1",
                (schluessel, ALLE, schluessel))
            zeile = await cursor.fetchone()
        version = await hole(zeile[0]) if zeile and zeile[0] else standard()
        version = version or standard()
    except Exception:
        logging.exception("Aktive Gegner-Version nicht ermittelbar - es gilt Standard")
        version = standard()
    _cache[schluessel] = (time.monotonic(), version)
    return version


def cache_leeren() -> None:
    _cache.clear()


def waehle_mit_fehlerquote(kandidaten: list[int], bester: int, fehlerquote: float,
                           rng: random.Random | None = None) -> int:
    """Mit der Fehlerquote nicht den besten, sondern einen anderen Zug nehmen.

    Genau ein Zufallsgriff, und nur wenn es überhaupt eine Alternative gibt.
    Bei Fehlerquote 0 wird gar nicht erst gewürfelt — dann verhält sich der
    Bot bis auf das letzte Bit wie vorher.
    """
    quote = max(0.0, min(1.0, float(fehlerquote or 0.0)))
    if quote <= 0 or len(kandidaten) < 2:
        return bester
    wuerfel = rng or random
    if wuerfel.random() >= quote:
        return bester
    andere = [k for k in kandidaten if k != bester]
    return wuerfel.choice(andere) if andere else bester


# --------------------------------------------------------------------------
# Verwaltung (wird von der Website über die Auftragstabelle angestossen)
# --------------------------------------------------------------------------
async def setze_aktiv(guild_id, version_id: int | None) -> None:
    await ensure_schema()
    async with db_context() as db:
        await db.execute(
            "INSERT INTO bot_version_aktiv (guild_id, version_id, gesetzt_am) "
            "VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET "
            "version_id = excluded.version_id, gesetzt_am = excluded.gesetzt_am",
            (str(guild_id or ALLE), int(version_id) if version_id else None, _now()))
        await db.commit()
    cache_leeren()
