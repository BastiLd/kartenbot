"""Alternative Karten-Designs: ein anderes Aussehen für eine Karte.

Ein Design ist **nur ein anderes Bild**. Werte, Angriffe und Name der Karte
bleiben, wie sie sind — und ein Design ist keine eigene Sammlungskarte (anders
als die Iron-Man-Varianten in services/card_variants.py, die damit nichts zu
tun haben).

Design 1 ist das normale Bild (`bild`) und immer frei. Design 2 und 3 kommen
aus `bild_2` und `bild_3` der Karte, die auf der Website eingetragen werden.
Für einen Spieler **sichtbar und wählbar** ist ein Design erst, wenn beides
da ist: der Bild-Link und die Freischaltung. Freischalten geht auch ohne
Link — sonst gingen Belohnungen verloren, solange Bilder noch fehlen.

Alles, was hier schiefgehen kann, endet still beim Standardbild. Eine Karte
ohne Bild oder ein Kampf, der wegen eines Designs abbricht, wäre schlimmer
als ein fehlendes Design.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from db import db_context
from services import db as _db_modul
from services.card_variants import base_card_name

# Die einzige Stelle, an der die Obergrenze steht. Ein viertes Design hiesse:
# hier 4, ein Feld "bild_4" in DESIGN_FELD und den Listen der Kartenänderungen.
MAX_DESIGNS = 3

DESIGN_FELD = {1: "bild", 2: "bild_2", 3: "bild_3"}

# Wer hat es vergeben? "level" und "einladung" kommen mit Plan 2.
QUELLEN = ("admin", "level", "einladung")

_schema_fuer_pfad: str | None = None


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ensure_schema() -> None:
    """Tabellen anlegen, falls es sie noch nicht gibt — einmal je Datenbank.

    Der Bot legt sie schon in init_db() an. Das hier ist für alles, was ohne
    init_db() läuft (Tests, Skripte). Bewusst nur einmal: Die Datenbank hat
    eine gemeinsame Verbindung, und ein commit() mitten im Lesen könnte eine
    halbe Schreibaktion von woanders mit festschreiben.
    """
    global _schema_fuer_pfad
    pfad = str(_db_modul.DB_PATH)
    if _schema_fuer_pfad == pfad:
        return
    async with db_context() as db:
        for sql in _db_modul.DESIGN_TABLES_SQL:
            await db.execute(sql)
        await db.commit()
    _schema_fuer_pfad = pfad


# --------------------------------------------------------------------------
# Reine Logik: was eine Karte hergibt
# --------------------------------------------------------------------------
def _live_karten() -> list:
    # Dieselbe Liste, die card_store.anwenden() an Ort und Stelle ändert —
    # so sind neue Links von der Website sofort da.
    from karten import karten
    return karten


def grundname(karte: Any) -> str:
    """Name, unter dem Designs gespeichert werden (Iron-Man-Varianten -> "Iron-Man")."""
    return base_card_name(karte)


def _grundkarte(karte: Any) -> dict | None:
    """Die Karte aus der laufenden Liste, die die Design-Links trägt."""
    name = grundname(karte)
    if name:
        for eintrag in _live_karten():
            if isinstance(eintrag, dict) and str(eintrag.get("name") or "").strip() == name:
                return eintrag
    # Nicht in der Liste (z. B. eine Karte, die es nur im Test gibt): dann
    # trägt sie ihre Links selbst.
    return karte if isinstance(karte, dict) else None


def finde_karte(name: Any) -> dict | None:
    """Die Grundkarte zu einem eingetippten Namen (Groß/klein egal), sonst None.

    Varianten-Namen wie "Alpha_Iron-Man" führen zur Grundkarte "Iron-Man".
    """
    gesucht = grundname(str(name or "").strip()).lower()
    if not gesucht:
        return None
    for eintrag in _live_karten():
        if isinstance(eintrag, dict) and str(eintrag.get("name") or "").strip().lower() == gesucht:
            return eintrag
    return None


def alle_grundnamen() -> list[str]:
    """Namen aller Karten in der Reihenfolge von karten.py."""
    return [str(k.get("name")) for k in _live_karten() if isinstance(k, dict) and k.get("name")]


def _link(quelle: dict | None, feld: str) -> str:
    wert = (quelle or {}).get(feld)
    return wert.strip() if isinstance(wert, str) else ""


def gueltige_nummer(design: Any) -> bool:
    """Nummern, die man freischalten kann: 2 bis MAX_DESIGNS."""
    return isinstance(design, int) and not isinstance(design, bool) and 2 <= design <= MAX_DESIGNS


def verfuegbare_designs(karte: Any) -> list[int]:
    """Welche Designs die Karte überhaupt hat (Link eingetragen), ohne Lücken.

    Design 1 ist immer dabei. Fehlt Design 2, zählt Design 3 nicht, auch wenn
    sein Link eingetragen ist.
    """
    quelle = _grundkarte(karte)
    out = [1]
    for nummer in range(2, MAX_DESIGNS + 1):
        if not _link(quelle, DESIGN_FELD[nummer]):
            break
        out.append(nummer)
    return out


def bild_link(karte: Any, design: int) -> str:
    """Der Bild-Link eines Designs, oder "" wenn es ihn nicht gibt.

    Design 1 ist das Bild der Karte selbst — bei Iron-Man also das Bild der
    jeweiligen Variante.
    """
    if design == 1:
        if isinstance(karte, dict):
            return _link(karte, "bild")
        return _link(_grundkarte(karte), "bild")
    if design not in verfuegbare_designs(karte):
        return ""
    return _link(_grundkarte(karte), DESIGN_FELD[design])


def bild_aus_wahl(karte: Any, aktive: dict[str, int] | None) -> str:
    """Bild einer Karte zu einer schon geladenen Wahl (aus `aktive_designs_von`).

    Für Schleifen über viele Karten: kein Datenbankzugriff.
    """
    standard = bild_link(karte, 1)
    design = (aktive or {}).get(grundname(karte), 1)
    if design == 1:
        return standard
    return bild_link(karte, design) or standard


# --------------------------------------------------------------------------
# Datenbank
# --------------------------------------------------------------------------
def _bekannte_karte(name: str) -> bool:
    return any(isinstance(k, dict) and str(k.get("name") or "").strip() == name
               for k in _live_karten())


async def freigeschaltet(user_id: int, karten_name: Any) -> set[int]:
    """Freigeschaltete Designs, mit oder ohne Link. Design 1 ist immer dabei."""
    name = grundname(karten_name)
    out = {1}
    try:
        await ensure_schema()
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT design FROM user_designs WHERE user_id = ? AND karten_name = ?",
                (int(user_id), name))
            for (design,) in await cursor.fetchall():
                if gueltige_nummer(design):
                    out.add(int(design))
    except Exception:
        logging.exception("Designs von %s für %s nicht lesbar", user_id, name)
    return out


async def anzahl_freigeschaltet(user_id: int) -> int:
    """Wie viele zusätzliche Designs hat dieser Spieler? (Design 1 zählt nicht mit.)"""
    try:
        await ensure_schema()
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM user_designs WHERE user_id = ?", (int(user_id),))
            zeile = await cursor.fetchone()
        return int(zeile[0]) if zeile else 0
    except Exception:
        logging.exception("Anzahl der Designs von %s nicht lesbar", user_id)
        return 0


async def freischalten(user_id: int, karten_name: Any, design: int, quelle: str = "admin") -> bool:
    """Ein Design freischalten. False, wenn es schon frei war oder nicht geht.

    Hängt bewusst **nicht** daran, ob der Bild-Link schon eingetragen ist.
    Und auch nicht daran, ob der Spieler die Karte besitzt: Die Freischaltung
    wird gemerkt und wirkt, sobald er sie hat.
    """
    if not gueltige_nummer(design):
        return False
    name = grundname(karten_name)
    if not _bekannte_karte(name):
        logging.warning("Design %s für unbekannte Karte %r nicht freigeschaltet", design, karten_name)
        return False
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO user_designs (user_id, karten_name, design, quelle, "
            "freigeschaltet_am) VALUES (?, ?, ?, ?, ?)",
            (int(user_id), name, int(design), str(quelle or ""), _jetzt()))
        neu = (cursor.rowcount or 0) > 0
        await db.commit()
    return neu


async def entziehen(user_id: int, karten_name: Any, design: int) -> bool:
    """Eine Freischaltung wieder wegnehmen. War es gewählt, gilt wieder Design 1."""
    if not gueltige_nummer(design):
        return False
    name = grundname(karten_name)
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "DELETE FROM user_designs WHERE user_id = ? AND karten_name = ? AND design = ?",
            (int(user_id), name, int(design)))
        weg = (cursor.rowcount or 0) > 0
        await db.execute(
            "DELETE FROM user_design_wahl WHERE user_id = ? AND karten_name = ? AND design = ?",
            (int(user_id), name, int(design)))
        await db.commit()
    return weg


async def waehlbare_designs(user_id: int, karte: Any) -> list[int]:
    """Designs, die der Spieler jetzt wählen kann: Link da **und** freigeschaltet."""
    frei = await freigeschaltet(user_id, karte)
    return [d for d in verfuegbare_designs(karte) if d in frei]


async def hat_waehlbare_designs(user_id: int, karten_namen: Any) -> bool:
    """Kann der Spieler für mindestens eine dieser Karten ein anderes Design wählen?

    Für Hinweise wie „Design ändern: /design“ — eine einzige Abfrage, und
    bei jedem Fehler einfach False (dann gibt es eben keinen Hinweis).
    """
    try:
        await ensure_schema()
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT karten_name, design FROM user_designs WHERE user_id = ?", (int(user_id),))
            zeilen = await cursor.fetchall()
        if not zeilen:
            return False
        frei: dict[str, set[int]] = {}
        for name, design in zeilen:
            frei.setdefault(str(name), set()).add(int(design))
        for name in karten_namen or []:
            basis = grundname(name)
            if any(bild_link(basis, d) for d in frei.get(basis, ())):
                return True
    except Exception:
        logging.exception("Designs von %s nicht prüfbar", user_id)
    return False


async def waehle(user_id: int, karten_name: Any, design: int) -> bool:
    """Ein Design als Standard für Sammlung und Kampf setzen.

    Design 1 geht immer (das ist „zurück auf normal“). 2 und 3 nur, wenn sie
    freigeschaltet sind und ihr Link eingetragen ist.
    """
    name = grundname(karten_name)
    if design == 1:
        await ensure_schema()
        async with db_context() as db:
            await db.execute(
                "DELETE FROM user_design_wahl WHERE user_id = ? AND karten_name = ?",
                (int(user_id), name))
            await db.commit()
        return True
    if not gueltige_nummer(design):
        return False
    if design not in await waehlbare_designs(user_id, karten_name):
        return False
    async with db_context() as db:
        await db.execute(
            "INSERT INTO user_design_wahl (user_id, karten_name, design) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, karten_name) DO UPDATE SET design = excluded.design",
            (int(user_id), name, int(design)))
        await db.commit()
    return True


async def alle_zuruecksetzen(user_id: int) -> int:
    """Alle Karten des Spielers wieder auf Design 1. Gibt zurück, wie viele es waren."""
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "DELETE FROM user_design_wahl WHERE user_id = ?", (int(user_id),))
        anzahl = cursor.rowcount or 0
        await db.commit()
    return int(anzahl)


async def aktive_designs_von(user_id: int) -> dict[str, int]:
    """Alle Wahlen eines Spielers auf einmal: Grundname -> Design.

    Enthält nur Wahlen, die noch freigeschaltet sind. Ob der Link noch da
    ist, prüft `bild_aus_wahl` beim Anzeigen — das kostet keine Abfrage.
    Karten ohne Eintrag zeigen Design 1.
    """
    try:
        await ensure_schema()
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT w.karten_name, w.design FROM user_design_wahl w "
                "JOIN user_designs d ON d.user_id = w.user_id "
                "AND d.karten_name = w.karten_name AND d.design = w.design "
                "WHERE w.user_id = ?", (int(user_id),))
            zeilen = await cursor.fetchall()
    except Exception:
        logging.exception("Design-Wahl von %s nicht lesbar", user_id)
        return {}
    return {str(name): int(design) for name, design in zeilen if gueltige_nummer(design)}


async def aktives_design(user_id: int, karte: Any) -> int:
    """Das Design, das gerade angezeigt wird — 1, wenn die Wahl nicht mehr trägt."""
    design = (await aktive_designs_von(user_id)).get(grundname(karte), 1)
    if design != 1 and not bild_link(karte, design):
        return 1
    return design


async def bild_fuer(user_id: int | None, karte: Any) -> str:
    """Das Bild, das dieser Spieler für diese Karte sieht.

    Fällt still auf das normale Bild zurück, wenn das gewählte Design
    gesperrt, entfernt oder sein Link leer ist.
    """
    if not user_id:
        return bild_aus_wahl(karte, None)
    return bild_aus_wahl(karte, await aktive_designs_von(int(user_id)))
