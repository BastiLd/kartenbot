"""Belohnungen für MEE6-Level und Einladungen.

Was hier steht, ist absichtlich stumpfe Rechnerei: Welche Stufe hat jemand
laut seinen Rollen, was steht ihm zu, was hat er schon? Discord kommt darin
nicht vor — das macht bot.py.

**Höchstens einmal:** Jede Belohnung hat einen Schlüssel (z. B.
``level:5:design:Black Widow:2``). Erst wird der Schlüssel ins Protokoll
geschrieben, dann wird vergeben. Ist der Schlüssel schon da, passiert nichts.
Geht die Vergabe schief, wird der Eintrag wieder gelöscht — dann kann es
später erneut versucht werden.

Wurde eine Belohnung später **entzogen** (Rolle verloren, siehe Plan 2,
Schritt 7), gilt sie als „nicht vergeben": Erreicht der Spieler die Stufe
erneut, bekommt er sie wieder.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, NamedTuple

from db import db_context
from level_reward_config import (
    EINGELADENER_STAUB,
    EINLADUNG_AB_STUFE,
    EINLADUNG_STAUB_AB_11,
    EINLADUNG_STUFEN,
    LEVEL_BELOHNUNGEN,
    LEVEL_HINWEISE,
    LEVEL_STUFEN,
    Design,
    Staub,
)
from services import db as _db_modul
from services import designs
from services.user_data import add_infinitydust

QUELLE_LEVEL = "level"
QUELLE_EINLADUNG = "einladung"

# Status im Protokoll. "entzogen" zählt als nicht vergeben.
STATUS_VERGEBEN = "vergeben"
STATUS_BEHALTEN = "behalten"
STATUS_ENTZOGEN = "entzogen"
ERLEDIGT = (STATUS_VERGEBEN, STATUS_BEHALTEN)

_schema_fuer_pfad: str | None = None


class Faellig(NamedTuple):
    """Eine Belohnung, die jemandem zusteht."""
    schluessel: str
    stufe: int
    belohnung: Any          # Design(...) oder Staub(...)
    quelle: str

    @property
    def art(self) -> str:
        return "design" if isinstance(self.belohnung, Design) else "staub"

    def text(self) -> str:
        if isinstance(self.belohnung, Design):
            return f"Design {self.belohnung.nummer} von {self.belohnung.karte}"
        return f"{self.belohnung.menge} Infinitydust"


@dataclass
class Ergebnis:
    vergeben: list[Faellig] = field(default_factory=list)
    uebersprungen: list[Faellig] = field(default_factory=list)   # hatte er schon
    fehler: list[tuple[Faellig, str]] = field(default_factory=list)

    @property
    def designs(self) -> int:
        return sum(1 for f in self.vergeben if f.art == "design")

    @property
    def staub(self) -> int:
        return sum(f.belohnung.menge for f in self.vergeben if f.art == "staub")


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ensure_schema() -> None:
    """Tabellen anlegen, falls nötig — einmal je Datenbank (wie services/designs.py)."""
    global _schema_fuer_pfad
    pfad = str(_db_modul.DB_PATH)
    if _schema_fuer_pfad == pfad:
        return
    async with db_context() as db:
        for sql in _db_modul.LEVEL_TABLES_SQL:
            await db.execute(sql)
        await db.commit()
    _schema_fuer_pfad = pfad


# --------------------------------------------------------------------------
# Reine Logik
# --------------------------------------------------------------------------
def stufe_aus_rollen(rollen_ids: Iterable[int], zuordnung: dict[int, int]) -> int:
    """Die höchste Stufe, deren Rolle der Spieler hat. Ohne Level-Rolle: 0.

    ``zuordnung`` ist Stufe -> Rollen-ID. Es zählt nur die höchste Rolle —
    egal, ob MEE6 die alten Rollen entfernt oder stehen lässt.
    """
    vorhanden = {int(r) for r in rollen_ids or ()}
    stufen = [int(stufe) for stufe, rolle in (zuordnung or {}).items() if int(rolle) in vorhanden]
    return max(stufen) if stufen else 0


def schluessel_fuer(quelle: str, stufe: int, belohnung: Any) -> str:
    if isinstance(belohnung, Design):
        return f"{quelle}:{int(stufe)}:design:{belohnung.karte}:{int(belohnung.nummer)}"
    return f"{quelle}:{int(stufe)}:staub"


def _faellig(quelle: str, stufe: int, belohnung: Any) -> Faellig:
    return Faellig(schluessel_fuer(quelle, stufe, belohnung), int(stufe), belohnung, quelle)


def belohnungen_bis(stufe: int) -> list[Faellig]:
    """Alles, was jemandem mit dieser Stufe zusteht (kumulativ, ohne Protokoll)."""
    out: list[Faellig] = []
    for s in sorted(LEVEL_BELOHNUNGEN):
        if s <= int(stufe or 0):
            out.extend(_faellig(QUELLE_LEVEL, s, b) for b in LEVEL_BELOHNUNGEN[s])
    return out


def faellige_belohnungen(stufe: int, schon: Iterable[str]) -> list[Faellig]:
    """Belohnungen für Level ≤ Stufe, die noch nicht erledigt sind."""
    erledigt = set(schon or ())
    return [f for f in belohnungen_bis(stufe) if f.schluessel not in erledigt]


def belohnungen_zwischen(von_stufe: int, auf_stufe: int) -> list[Faellig]:
    """Was jemand nur wegen der verlorenen Stufen bekommen hat (für die Rückfrage)."""
    unten, oben = int(auf_stufe or 0), int(von_stufe or 0)
    return [f for f in belohnungen_bis(oben) if f.stufe > unten]


def einladung_belohnungen(neue_anzahl: int) -> list[Faellig]:
    """Was der Einlader für GENAU diese Einladung bekommt."""
    anzahl = int(neue_anzahl or 0)
    if anzahl in EINLADUNG_STUFEN:
        return [_faellig(QUELLE_EINLADUNG, anzahl, b) for b in EINLADUNG_STUFEN[anzahl]]
    if anzahl >= EINLADUNG_AB_STUFE:
        return [_faellig(QUELLE_EINLADUNG, anzahl, Staub(EINLADUNG_STAUB_AB_11))]
    return []


def einladung_faellige(anzahl: int, schon: Iterable[str], *, mit_staub_ab_11: bool = True) -> list[Faellig]:
    """Alles, was einem Einlader mit dieser Zahl zusteht und noch fehlt (fürs Nachholen)."""
    erledigt = set(schon or ())
    gesamt = int(anzahl or 0)
    out: list[Faellig] = []
    for stufe in sorted(EINLADUNG_STUFEN):
        if stufe <= gesamt:
            out.extend(_faellig(QUELLE_EINLADUNG, stufe, b) for b in EINLADUNG_STUFEN[stufe])
    if mit_staub_ab_11:
        for n in range(EINLADUNG_AB_STUFE, gesamt + 1):
            out.append(_faellig(QUELLE_EINLADUNG, n, Staub(EINLADUNG_STAUB_AB_11)))
    return [f for f in out if f.schluessel not in erledigt]


def verloren_haben(stufe_vorher: int, stufe_nachher: int) -> bool:
    """Nur ein echtes Absinken zählt. 5 -> 10 (Rolle ersetzt) ist kein Verlust."""
    return int(stufe_nachher or 0) < int(stufe_vorher or 0)


def titel(stufe: int) -> str:
    return LEVEL_STUFEN.get(int(stufe or 0), "")


def hinweis(stufe: int) -> str:
    return LEVEL_HINWEISE.get(int(stufe or 0), "")


def naechste_stufe(stufe: int) -> int:
    """Die nächste Stufe überhaupt (auch ohne Bot-Belohnung). 0 = keine mehr."""
    hoehere = [s for s in sorted(LEVEL_STUFEN) if s > int(stufe or 0)]
    return hoehere[0] if hoehere else 0


def naechste_stufe_mit_belohnung(stufe: int) -> int:
    hoehere = [s for s in sorted(LEVEL_BELOHNUNGEN) if s > int(stufe or 0)]
    return hoehere[0] if hoehere else 0


def naechste_einladungsstufe(anzahl: int) -> int:
    hoehere = [s for s in sorted(EINLADUNG_STUFEN) if s > int(anzahl or 0)]
    return hoehere[0] if hoehere else 0


# --------------------------------------------------------------------------
# Protokoll und Vergabe
# --------------------------------------------------------------------------
async def erledigte_schluessel(user_id: int) -> set[str]:
    """Schlüssel, die als vergeben gelten. "entzogen" ist NICHT dabei."""
    await ensure_schema()
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT schluessel FROM belohnungs_protokoll WHERE user_id = ? AND status IN (?, ?)",
            (int(user_id), STATUS_VERGEBEN, STATUS_BEHALTEN))
        return {str(z[0]) for z in await cursor.fetchall()}


async def protokoll_von(user_id: int, *, quelle: str | None = None) -> list[dict[str, Any]]:
    await ensure_schema()
    sql = "SELECT schluessel, art, quelle, status, am FROM belohnungs_protokoll WHERE user_id = ?"
    werte: list[Any] = [int(user_id)]
    if quelle:
        sql += " AND quelle = ?"
        werte.append(quelle)
    async with db_context() as db:
        cursor = await db.execute(sql, tuple(werte))
        return [dict(z) for z in await cursor.fetchall()]


async def _eintragen(user_id: int, eintrag: Faellig, quelle: str) -> bool:
    """Platz im Protokoll belegen. False, wenn die Belohnung schon erledigt ist.

    War sie „entzogen", wird sie wieder auf „vergeben" gesetzt — entzogen
    zählt als nicht vergeben.
    """
    async with db_context() as db:
        cursor = await db.execute(
            "SELECT status FROM belohnungs_protokoll WHERE user_id = ? AND schluessel = ?",
            (int(user_id), eintrag.schluessel))
        vorhanden = await cursor.fetchone()
        if vorhanden is not None and str(vorhanden[0]) in ERLEDIGT:
            return False
        details = json.dumps(
            {"text": eintrag.text(), "stufe": eintrag.stufe} |
            ({"karte": eintrag.belohnung.karte, "nummer": eintrag.belohnung.nummer}
             if isinstance(eintrag.belohnung, Design) else {"menge": eintrag.belohnung.menge}),
            ensure_ascii=False)
        await db.execute(
            "INSERT INTO belohnungs_protokoll (user_id, schluessel, art, quelle, details_json, "
            "status, am) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, schluessel) DO UPDATE SET status = excluded.status, "
            "am = excluded.am, quelle = excluded.quelle",
            (int(user_id), eintrag.schluessel, eintrag.art, str(quelle), details,
             STATUS_VERGEBEN, _jetzt()))
        await db.commit()
    return True


async def _austragen(user_id: int, schluessel: str) -> None:
    """Den Platz wieder freigeben, wenn die Vergabe scheitert."""
    async with db_context() as db:
        await db.execute(
            "DELETE FROM belohnungs_protokoll WHERE user_id = ? AND schluessel = ?",
            (int(user_id), schluessel))
        await db.commit()


async def status_setzen(user_id: int, schluessel: str, status: str) -> None:
    await ensure_schema()
    async with db_context() as db:
        await db.execute(
            "UPDATE belohnungs_protokoll SET status = ? WHERE user_id = ? AND schluessel = ?",
            (str(status), int(user_id), str(schluessel)))
        await db.commit()


async def vergebe(user_id: int, belohnungen: Iterable[Faellig], quelle: str) -> Ergebnis:
    """Belohnungen vergeben — jede höchstens einmal.

    Reihenfolge je Belohnung: Platz im Protokoll belegen, dann vergeben.
    Schlägt die Vergabe fehl, wird der Platz wieder frei.
    """
    await ensure_schema()
    ergebnis = Ergebnis()
    for eintrag in belohnungen or ():
        if isinstance(eintrag.belohnung, Design) and designs.finde_karte(eintrag.belohnung.karte) is None:
            # Falsch geschriebene oder gelöschte Karte: melden, aber weitermachen.
            logging.warning("Belohnung %s: Karte %r gibt es nicht",
                            eintrag.schluessel, eintrag.belohnung.karte)
            ergebnis.fehler.append((eintrag, f"Die Karte „{eintrag.belohnung.karte}“ gibt es nicht."))
            continue
        if not await _eintragen(user_id, eintrag, quelle):
            ergebnis.uebersprungen.append(eintrag)
            continue
        try:
            if isinstance(eintrag.belohnung, Design):
                await designs.freischalten(user_id, eintrag.belohnung.karte,
                                           int(eintrag.belohnung.nummer), quelle)
            else:
                await add_infinitydust(int(user_id), int(eintrag.belohnung.menge))
        except Exception as fehler:                                # noqa: BLE001
            logging.exception("Belohnung %s für %s fehlgeschlagen", eintrag.schluessel, user_id)
            await _austragen(user_id, eintrag.schluessel)
            ergebnis.fehler.append((eintrag, str(fehler)))
            continue
        ergebnis.vergeben.append(eintrag)
    return ergebnis


async def entziehe(user_id: int, belohnungen: Iterable[Faellig]) -> int:
    """Designs wieder wegnehmen und im Protokoll als „entzogen" führen."""
    await ensure_schema()
    anzahl = 0
    for eintrag in belohnungen or ():
        if isinstance(eintrag.belohnung, Design):
            try:
                await designs.entziehen(user_id, eintrag.belohnung.karte, int(eintrag.belohnung.nummer))
            except Exception:                                      # noqa: BLE001
                logging.exception("Design %s konnte nicht entzogen werden", eintrag.schluessel)
                continue
        await status_setzen(user_id, eintrag.schluessel, STATUS_ENTZOGEN)
        anzahl += 1
    return anzahl


async def behalte(user_id: int, belohnungen: Iterable[Faellig]) -> int:
    """Bei „Behalten": nie wieder nachfragen."""
    await ensure_schema()
    anzahl = 0
    for eintrag in belohnungen or ():
        await status_setzen(user_id, eintrag.schluessel, STATUS_BEHALTEN)
        anzahl += 1
    return anzahl


def eingeladener_staub() -> int:
    return int(EINGELADENER_STAUB)
