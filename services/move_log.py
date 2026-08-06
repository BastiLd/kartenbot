"""Zug-Mitschrift: was im Moment einer Entscheidung auf dem Tisch lag.

Bisher stand im Verlauf nur, welcher Angriff benutzt wurde und wer gewonnen
hat. Damit lässt sich nicht lernen, *warum* jemand so gespielt hat — dafür
braucht es die Lage im Moment der Wahl. Genau die wird hier festgehalten:
Lebenspunkte beider Seiten, aktive Effekte, Abklingzeiten, welche Angriffe
überhaupt zur Verfügung standen, und die getroffene Wahl.

Dazu die **Bedenkzeit**. Sie verrät viel: Ein Zug nach zwei Sekunden ist
Routine, einer nach dreißig war eine schwierige Lage. Genau solche Stellen
sind zum Lernen die wertvollsten.

**Zwei Regeln, die hier über allem stehen:**

1. *Der Kampf darf davon nichts merken.* Jeder Aufruf fängt seine Fehler
   selbst ab. Geht das Mitschreiben schief, wird es protokolliert und der
   Kampf läuft weiter, als wäre nichts gewesen. Lieber ein fehlender
   Datensatz als ein abgebrochener Kampf.
2. *Nichts wird still gesammelt.* Ohne den Schalter ``mitschrift.aktiv`` in
   den Einstellungen passiert gar nichts.

**Was bewusst NICHT gespeichert wird**, weil es sich später aus den Daten
ableiten lässt — doppelt zu speichern hiesse nur, zwei Wahrheiten zu haben:

- *Wiederholung nach einem Fehlschlag*: steht im vorherigen Zug desselben
  Spielers im selben Kampf.
- *Karte kurz vorher geändert*: ergibt sich aus ``erstellt_am`` und dem
  Änderungsverlauf in ``card_override_history``.

Der Ausgang wird nachgetragen, wenn der Kampf vorbei ist (``setze_ausgang``).
Bis dahin steht er auf ``NULL`` — ein Kampf, der nie zu Ende gespielt wurde,
bleibt so erkennbar und verfälscht das Lernen nicht.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from db import db_context

# Ohne diesen Schalter wird nichts mitgeschrieben. Er liegt in web_settings,
# also dort, wo auch alle anderen Einstellungen der Website stehen.
SCHALTER = "mitschrift.aktiv"

# Der Schalter wird nicht bei jedem Zug neu aus der Datenbank geholt. Eine
# Minute Verzögerung beim Umlegen ist verschmerzbar; eine Abfrage je Zug
# wäre unnötige Last auf der einzigen Schreibverbindung.
SCHALTER_CACHE_S = 60

# So viele Angriffe hat eine Karte höchstens - mehr zeigt der Kampf nicht an.
MAX_ANGRIFFE = 4

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS battle_moves (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        erstellt_am    TEXT NOT NULL,
        session_id     INTEGER,
        kampf_art      TEXT NOT NULL DEFAULT '',
        runde          INTEGER NOT NULL DEFAULT 0,
        spieler_id     TEXT NOT NULL DEFAULT '',
        ist_bot        INTEGER NOT NULL DEFAULT 0,
        karte          TEXT NOT NULL DEFAULT '',
        gegner_karte   TEXT NOT NULL DEFAULT '',
        eigene_hp      INTEGER NOT NULL DEFAULT 0,
        eigene_max_hp  INTEGER NOT NULL DEFAULT 0,
        gegner_hp      INTEGER NOT NULL DEFAULT 0,
        gegner_max_hp  INTEGER NOT NULL DEFAULT 0,
        angriff_index  INTEGER NOT NULL DEFAULT -1,
        angriff_name   TEXT NOT NULL DEFAULT '',
        lage_json      TEXT NOT NULL DEFAULT '{}',
        bedenkzeit_ms  INTEGER,
        ausgang        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_battle_moves_session ON battle_moves(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_battle_moves_lernen ON battle_moves(karte, ausgang)",
)

_schema_ready = False
_schalter_wert = False
_schalter_geprueft = 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    async with db_context() as db:
        for anweisung in _SCHEMA:
            await db.execute(anweisung)
        await db.commit()
    _schema_ready = True


def schalter_zuruecksetzen() -> None:
    """Zwingt die nächste Abfrage, wieder in die Datenbank zu schauen."""
    global _schalter_geprueft
    _schalter_geprueft = 0.0


async def ist_an() -> bool:
    """Ob mitgeschrieben werden darf. Im Zweifel: nein."""
    global _schalter_wert, _schalter_geprueft
    jetzt = time.monotonic()
    if jetzt - _schalter_geprueft < SCHALTER_CACHE_S:
        return _schalter_wert
    try:
        await ensure_schema()
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT value FROM web_settings WHERE key = ?", (SCHALTER,))
            zeile = await cursor.fetchone()
        _schalter_wert = str(zeile[0]).strip().lower() in ("1", "true", "an", "ja") \
            if zeile else False
    except Exception:
        # Fehlt die Tabelle (Website lief noch nie), wird nicht mitgeschrieben.
        _schalter_wert = False
    _schalter_geprueft = jetzt
    return _schalter_wert


def _effekte(active_effects, spieler_id) -> list[dict]:
    """Die aktiven Effekte einer Seite, auf das Wesentliche eingedampft."""
    heraus = []
    for effekt in (active_effects or {}).get(spieler_id, []) or []:
        if not isinstance(effekt, dict):
            continue
        eintrag = {"typ": str(effekt.get("type") or effekt.get("typ") or "")}
        for feld in ("turns", "turns_left", "value", "amount", "percent"):
            if effekt.get(feld) not in (None, ""):
                eintrag[feld] = effekt[feld]
        if eintrag["typ"]:
            heraus.append(eintrag)
    return heraus


def _abklingzeiten(view, spieler_id) -> dict:
    roh = getattr(view, "attack_cooldowns", None)
    if not isinstance(roh, dict):
        roh = getattr(view, "_cooldowns_by_player", None)
    eigene = (roh or {}).get(spieler_id, {}) if isinstance(roh, dict) else {}
    return {str(k): int(v) for k, v in (eigene or {}).items() if isinstance(v, int)}


def _angriffe(karte, abklingzeiten: dict) -> list[dict]:
    """Was zur Wahl stand — mit dem, was die Entscheidung beeinflusst."""
    heraus = []
    for index, angriff in enumerate((karte or {}).get("attacks", [])[:MAX_ANGRIFFE]):
        if not isinstance(angriff, dict):
            continue
        heraus.append({
            "index": index,
            "name": str(angriff.get("name") or ""),
            "schaden": angriff.get("damage"),
            "abklingzeit": int(angriff.get("cooldown_turns") or 0),
            "wartet_noch": int(abklingzeiten.get(str(index), 0) or 0),
            "standard": bool(angriff.get("is_standard_attack")),
            "wirkungen": [str(e.get("type")) for e in (angriff.get("effects") or [])
                          if isinstance(e, dict) and e.get("type")],
        })
    return heraus


def _bedenkzeit_ms(view) -> int | None:
    """Wie lange jemand für die Entscheidung gebraucht hat.

    Zwei Quellen für den Startpunkt, weil die beiden Kampfarten verschieden
    ticken: Im PvP setzt ihn der Setter von ``current_turn`` in
    ``BaseBattleView``, sobald jemand an die Reihe kommt. In Missionen bleibt
    ``current_turn`` dagegen durchgehend beim Spieler — dort zählt deshalb
    die Zeit seit dem letzten mitgeschriebenen Zug, die am Ende von
    ``schreibe_zug`` gesetzt wird. Beides ist dasselbe: die Spanne, in der
    überlegt wurde.
    """
    begonnen = getattr(view, "_zug_begonnen_ts", None)
    if not begonnen:
        return None
    dauer = (time.monotonic() - float(begonnen)) * 1000
    # Ein Kampf kann über Nacht offen bleiben. Solche Werte sagen nichts über
    # die Schwierigkeit der Lage aus und wuerden jeden Durchschnitt ruinieren.
    return int(dauer) if 0 <= dauer <= 30 * 60 * 1000 else None


def _karte_von(view, spieler_id: int):
    """Das Kartenobjekt einer Seite.

    Die beiden Kampfarten benennen dasselbe verschieden: PvP hat
    ``player1_card``/``player2_card``, Missionen ``player_card``/``bot_card``.
    Deshalb wird über die zugehörige Id gesucht statt geraten.
    """
    for id_feld, karten_feld in (("player1_id", "player1_card"),
                                 ("player2_id", "player2_card"),
                                 ("user_id", "player_card")):
        if int(getattr(view, id_feld, -1) or -1) == spieler_id:
            karte = getattr(view, karten_feld, None)
            if karte:
                return karte
    if spieler_id == 0:                     # der Bot, in Missionen
        return getattr(view, "bot_card", None) or getattr(view, "player2_card", None)
    return None


def _sammle(view, spieler_id: int, angriff_index: int) -> dict:
    """Alles, was von einem Zug festgehalten wird — als fertige Zeile."""
    hp = getattr(view, "_hp_by_player", {}) or {}
    max_hp = getattr(view, "_max_hp_by_player", {}) or {}
    namen = getattr(view, "_card_names_by_player", {}) or {}

    # Die Gegenseite über die HP-Tabelle bestimmen: Sie hat in beiden
    # Kampfarten genau die zwei Beteiligten als Schlüssel.
    gegner_id = next((int(i) for i in hp if int(i) != spieler_id), 0)
    eigene_karte = _karte_von(view, spieler_id)

    abklingzeiten = _abklingzeiten(view, spieler_id)
    angriffe = _angriffe(eigene_karte, abklingzeiten)
    gewaehlt = next((a for a in angriffe if a["index"] == angriff_index), None)

    lage = {
        "eigene_effekte": _effekte(getattr(view, "active_effects", {}), spieler_id),
        "gegner_effekte": _effekte(getattr(view, "active_effects", {}), gegner_id),
        "abklingzeiten": abklingzeiten,
        "gegner_abklingzeiten": _abklingzeiten(view, gegner_id),
        "angriffe": angriffe,
        "betaeubt": bool((getattr(view, "stunned_next_turn", {}) or {}).get(spieler_id)),
        "verwirrt": bool((getattr(view, "confused_next_turn", {}) or {}).get(spieler_id)),
    }

    return {
        "erstellt_am": _now(),
        "session_id": int(getattr(view, "session_id", 0) or 0) or None,
        "kampf_art": str(getattr(view, "session_kind", "") or ""),
        "runde": int(getattr(view, "round_counter", 0) or 0),
        "spieler_id": str(spieler_id),
        "ist_bot": 1 if spieler_id == 0 else 0,
        "karte": str(namen.get(spieler_id, "")),
        "gegner_karte": str(namen.get(gegner_id, "")),
        "eigene_hp": int(hp.get(spieler_id, 0) or 0),
        "eigene_max_hp": int(max_hp.get(spieler_id, 0) or 0),
        "gegner_hp": int(hp.get(gegner_id, 0) or 0),
        "gegner_max_hp": int(max_hp.get(gegner_id, 0) or 0),
        "angriff_index": angriff_index,
        "angriff_name": str(gewaehlt["name"]) if gewaehlt else "",
        "lage_json": json.dumps(lage, ensure_ascii=False),
        "bedenkzeit_ms": _bedenkzeit_ms(view),
    }


def merke_lage(view, spieler_id: int, angriff_index: int) -> None:
    """Die Lage einsammeln, *bevor* der Zug sie verändert.

    Bewusst in zwei Schritten und nicht in einem: Zwischen der Wahl eines
    Angriffs und seiner Ausführung liegen mehrere Prüfungen, die den Zug noch
    ablehnen können (Abklingzeit, Sperre, erzwungene Landung). Wer schon beim
    Klick schreibt, sammelt Züge ein, die nie stattgefunden haben — und lernte
    aus Entscheidungen, die das Spiel gar nicht zugelassen hat.

    Hier wird deshalb nur gemerkt. Geschrieben wird in
    ``schreibe_gemerkten_zug``, sobald der Zug wirklich durch ist. Kommt
    dazwischen ein Abbruch, wird die gemerkte Lage nie geschrieben.

    Synchron und ohne Datenbank — das darf an keiner Stelle im Kampf bremsen.
    """
    try:
        view._mitschrift_zug = None
        # Steht fest, dass der Schalter aus ist, wird gar nicht erst gesammelt.
        # Beim allerersten Zug nach dem Start ist er noch nicht abgefragt - das
        # geht nur async. Dann wird einmal gesammelt und beim Schreiben wieder
        # verworfen; ab da greift der Zwischenspeicher.
        if _schalter_geprueft and not _schalter_wert:
            return
        view._mitschrift_zug = _sammle(view, int(spieler_id), int(angriff_index))
    except Exception:
        logging.exception("Zug-Mitschrift: Lage konnte nicht gelesen werden")


async def schreibe_gemerkten_zug(view) -> None:
    """Den gemerkten Zug wegschreiben — der Zug ist jetzt wirklich passiert.

    Ohne gemerkte Lage passiert nichts. Die Lage wird dabei verbraucht, damit
    ein zweiter Aufruf nicht dieselbe Zeile noch einmal schreibt.

    Wirft nie: Ein Fehler beim Mitschreiben darf einen laufenden Kampf
    niemals stören.
    """
    try:
        zeile = getattr(view, "_mitschrift_zug", None)
        view._mitschrift_zug = None
        if not zeile or not await ist_an():
            return

        await ensure_schema()
        async with db_context() as db:
            await db.execute(
                "INSERT INTO battle_moves (erstellt_am, session_id, kampf_art, runde, "
                "spieler_id, ist_bot, karte, gegner_karte, eigene_hp, eigene_max_hp, "
                "gegner_hp, gegner_max_hp, angriff_index, angriff_name, lage_json, "
                "bedenkzeit_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (zeile["erstellt_am"], zeile["session_id"], zeile["kampf_art"],
                 zeile["runde"], zeile["spieler_id"], zeile["ist_bot"], zeile["karte"],
                 zeile["gegner_karte"], zeile["eigene_hp"], zeile["eigene_max_hp"],
                 zeile["gegner_hp"], zeile["gegner_max_hp"], zeile["angriff_index"],
                 zeile["angriff_name"], zeile["lage_json"], zeile["bedenkzeit_ms"]))
            await db.commit()

        # Ab jetzt laeuft die Uhr fuer den naechsten Zug. Im PvP setzt der
        # Zugwechsel sie gleich noch einmal - dort ist diese Zeile wirkungslos.
        # In Missionen ist sie die einzige Quelle, siehe _bedenkzeit_ms.
        view._zug_begonnen_ts = time.monotonic()
    except Exception:
        logging.exception("Zug-Mitschrift fehlgeschlagen (der Kampf laeuft weiter)")


async def setze_ausgang(session_id, gewinner_id) -> None:
    """Nachtragen, wie der Kampf ausging.

    Erst damit werden die Züge zum Lernen brauchbar: Es geht ja gerade darum,
    welche Entscheidungen am Ende zum Sieg geführt haben.
    """
    try:
        if not session_id or not await ist_an():
            return
        async with db_context() as db:
            if gewinner_id is None:
                await db.execute(
                    "UPDATE battle_moves SET ausgang = 'unentschieden' "
                    "WHERE session_id = ? AND ausgang IS NULL", (int(session_id),))
            else:
                await db.execute(
                    "UPDATE battle_moves SET ausgang = "
                    "CASE WHEN spieler_id = ? THEN 'gewonnen' ELSE 'verloren' END "
                    "WHERE session_id = ? AND ausgang IS NULL",
                    (str(int(gewinner_id)), int(session_id)))
            await db.commit()
    except Exception:
        logging.exception("Ausgang des Kampfes konnte nicht nachgetragen werden")


async def zaehle() -> dict:
    """Wie viel schon zusammengekommen ist — für die Einstellungsseite.

    „Nichts wird still gelernt": Der Bereich soll zeigen, woraus gelernt
    wurde und wie viele Kämpfe eingeflossen sind.
    """
    try:
        await ensure_schema()
        async with db_context() as db:
            cursor = await db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT session_id), "
                "SUM(CASE WHEN ausgang IS NOT NULL THEN 1 ELSE 0 END) FROM battle_moves")
            zeile = await cursor.fetchone()
        return {"zuege": int(zeile[0] or 0), "kaempfe": int(zeile[1] or 0),
                "verwertbar": int(zeile[2] or 0)}
    except Exception:
        logging.exception("Zaehlen der Mitschrift fehlgeschlagen")
        return {"zuege": 0, "kaempfe": 0, "verwertbar": 0}
