"""Discord-IDs in lesbare Namen übersetzen — und zurück.

Warum eigen: In der Datenbank des Bots steht überall nur die Discord-ID. Eine
Zahlenkolonne wie 965593518745731200 sagt niemandem etwas. Diese Datei holt die
Namen einmal von Discord, merkt sie sich, und beantwortet danach alles aus dem
Zwischenspeicher.

Ohne Bot-Token gibt es nichts zu holen. Dann liefert alles hier einfach die ID
zurück — die Seite bleibt benutzbar, sie zeigt eben weiter Zahlen.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import config, database, discordapi

_KIND = "user"
# So lange gilt ein gemerkter Name als frisch. Namen ändern sich selten, und
# ein Fehlgriff ist harmlos - deshalb großzügig.
_MAX_ALTER_TAGE = 7


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _anzeigename(daten: dict) -> str:
    """Was der Mensch sehen soll.

    Discord hat drei Stufen: der Servername (nick), der globale Anzeigename und
    der eigentliche Benutzername. Wir nehmen den spezifischsten, der da ist.
    """
    for schluessel in ("nick", "global_name", "username"):
        wert = daten.get(schluessel)
        if wert:
            return str(wert)
    return ""


def _lies_zwischenspeicher(ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    platzhalter = ",".join("?" for _ in ids)
    with database.read_connection() as con:
        zeilen = database.fetch_all(
            con,
            f"SELECT object_id, data_json FROM web_discord_cache "
            f"WHERE kind = ? AND object_id IN ({platzhalter})",
            (_KIND, *ids))
    treffer = {}
    for zeile in zeilen:
        try:
            daten = json.loads(zeile["data_json"])
        except (ValueError, TypeError):
            continue
        name = daten.get("name") or ""
        if name:
            treffer[str(zeile["object_id"])] = name
    return treffer


def merke(paare: dict[str, str]) -> None:
    """Namen dauerhaft festhalten, damit Discord nicht ständig gefragt wird."""
    paare = {str(k): v for k, v in paare.items() if v}
    if not paare:
        return
    jetzt = _jetzt()
    with database.write_connection() as con:
        con.executemany(
            "INSERT INTO web_discord_cache (kind, guild_id, object_id, data_json, updated_at) "
            "VALUES (?, '', ?, ?, ?) "
            "ON CONFLICT(kind, guild_id, object_id) DO UPDATE SET "
            "data_json = excluded.data_json, updated_at = excluded.updated_at",
            [(_KIND, uid, json.dumps({"name": name}), jetzt) for uid, name in paare.items()])


def merke_mitglieder(mitglieder: list[dict]) -> None:
    """Beim Laden einer Mitgliederliste gleich alle Namen mitnehmen.

    Das ist der günstigste Weg an viele Namen zu kommen: eine Anfrage, die
    ohnehin läuft, füllt den Zwischenspeicher für den ganzen Server.
    """
    paare = {}
    for m in mitglieder or []:
        nutzer = m.get("user") or {}
        uid = nutzer.get("id")
        if not uid:
            continue
        name = _anzeigename({**nutzer, "nick": m.get("nick")})
        if name:
            paare[str(uid)] = name
    merke(paare)


async def _hole_von_discord(ids: list[str]) -> dict[str, str]:
    """Einzelne Nutzer bei Discord nachschlagen.

    Bewusst einzeln und nur für das, was wirklich fehlt: Discord hat keine
    Sammelabfrage für Nutzer, und jede Anfrage zählt auf die Anfragebremse.
    """
    gefunden: dict[str, str] = {}
    for uid in ids:
        try:
            daten = await discordapi.user(uid)
        except discordapi.DiscordError as exc:
            # Gelöschte Konten und Fremde, die der Bot nicht sieht, sind normal.
            logging.debug("Name für %s nicht abrufbar: %s", uid, exc)
            continue
        name = _anzeigename(daten)
        if name:
            gefunden[str(uid)] = name
    return gefunden


async def aufloesen(ids: list[str], nachladen: bool = True) -> dict[str, str]:
    """IDs zu Namen. Unbekanntes bleibt schlicht weg — der Aufrufer zeigt dann die ID."""
    eindeutig = [str(i) for i in dict.fromkeys(ids) if str(i).isdigit()]
    if not eindeutig:
        return {}
    treffer = _lies_zwischenspeicher(eindeutig)
    fehlend = [i for i in eindeutig if i not in treffer]
    if fehlend and nachladen and discordapi.bot_token_available():
        # Deckel, damit ein Aufruf mit hunderten unbekannten IDs nicht minutenlang
        # gegen Discords Anfragebremse läuft. Der Rest kommt beim nächsten Mal.
        neu = await _hole_von_discord(fehlend[:25])
        if neu:
            merke(neu)
            treffer.update(neu)
    return treffer


def suche(text: str, grenze: int = 25) -> list[dict]:
    """Nach einem Namen suchen und die passenden IDs liefern.

    Sucht nur im Zwischenspeicher — was noch nie geladen wurde, ist auch nicht
    findbar. Deshalb füllt die Mitgliederliste ihn beim Öffnen gleich mit.
    """
    text = (text or "").strip()
    if not text:
        return []
    with database.read_connection() as con:
        zeilen = database.fetch_all(
            con,
            "SELECT object_id, data_json FROM web_discord_cache WHERE kind = ?",
            (_KIND,))
    klein = text.lower()
    treffer = []
    for zeile in zeilen:
        try:
            name = (json.loads(zeile["data_json"]) or {}).get("name") or ""
        except (ValueError, TypeError):
            continue
        if klein in name.lower():
            treffer.append({"id": str(zeile["object_id"]), "name": name})
    # Wer vorne anfängt, passt besser als wer es nur irgendwo enthält.
    treffer.sort(key=lambda t: (not t["name"].lower().startswith(klein), t["name"].lower()))
    return treffer[:grenze]


async def zu_id(eingabe: str) -> str | None:
    """Nimmt eine ID oder einen Namen und macht daraus eine ID.

    Damit dürfen alle Eingabefelder beides annehmen. Eine reine Zahl gilt als
    ID; sonst wird im Zwischenspeicher nach dem Namen gesucht. Nur wenn genau
    ein Name passt, gibt es ein Ergebnis - bei mehreren wäre jede Wahl geraten.
    """
    eingabe = (eingabe or "").strip()
    if not eingabe:
        return None
    if eingabe.isdigit():
        return eingabe
    # <@123> und <@!123> aus Discord kopiert
    roh = eingabe.strip("<@!>")
    if roh.isdigit():
        return roh
    treffer = suche(eingabe, grenze=5)
    genau = [t for t in treffer if t["name"].lower() == eingabe.lower()]
    if len(genau) == 1:
        return genau[0]["id"]
    if len(treffer) == 1:
        return treffer[0]["id"]
    return None
