"""Kartenbot Web — Schnittstelle und Auslieferung der Oberfläche.

Aufbau:
  /            → die Oberfläche (statische Dateien)
  /api/...     → alles andere

Zugang siehe auth.py. Lesende Endpunkte brauchen eine Anmeldung, schreibende
zusätzlich die passende Netzstufe. Discord-Aktionen laufen je nach Einstellung
über die Auftragstabelle (der Bot führt aus) oder direkt.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (actions, audit, auth, cards, config, database, discordapi, jobs,
               logparse, names, netguard, ollama, queries, roles, schema,
               karteneditor, settings, sicherung)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Kartenbot Web", docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
def _startup() -> None:
    """Fehlende Tabellen anlegen. Läuft auch dann sauber, wenn die Datenbank
    noch gar nicht existiert — dann meldet sich später jeder Endpunkt sauber."""
    try:
        with database.write_connection() as con:
            schema.init_schema(con)
    except database.WebDBError:
        pass


# --------------------------------------------------------------------------
# Fehler in verständlicher Form
# --------------------------------------------------------------------------
@app.exception_handler(Exception)
async def _unerwartet(request: Request, exc: Exception):
    """Letzte Auffanglinie: ein Fehler muss sich selbst erklaeren koennen.

    Vorher kam bei einem unbehandelten Fehler nur ein nacktes 500 ohne Inhalt
    an. Auf dem Bildschirm stand dann "Internal Server Error" - was niemandem
    weiterhilft, weder beim Benutzen noch beim Reparieren. Jetzt landet der
    vollstaendige Verlauf im Protokoll des Containers, und in der Oberflaeche
    steht wenigstens, welche Art von Fehler wo aufgetreten ist.
    """
    logging.exception("Unbehandelter Fehler bei %s %s", request.method, request.url.path)
    return JSONResponse(
        {"error": f"{type(exc).__name__} bei {request.url.path}: {exc}"
                  f" — der vollständige Verlauf steht im Protokoll des Containers."},
        status_code=500)


@app.exception_handler(database.WebDBError)
async def _db_error(_request: Request, exc: database.WebDBError):
    return JSONResponse({"error": str(exc)}, status_code=503)


@app.exception_handler(actions.ActionError)
async def _action_error(_request: Request, exc: actions.ActionError):
    return JSONResponse({"error": str(exc)}, status_code=400)


@app.exception_handler(discordapi.DiscordError)
async def _discord_error(_request: Request, exc: discordapi.DiscordError):
    return JSONResponse({"error": str(exc)}, status_code=502)


@app.exception_handler(ollama.OllamaError)
async def _ollama_error(_request: Request, exc: ollama.OllamaError):
    return JSONResponse({"error": str(exc)}, status_code=502)


# --------------------------------------------------------------------------
# Anmeldung
# --------------------------------------------------------------------------
class PasswordBody(BaseModel):
    password: str


def _secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https" or \
        request.headers.get("x-forwarded-proto", "").lower() == "https"


@app.get("/api/auth/status")
def auth_status(request: Request):
    return auth.status(request)


@app.post("/api/auth/password")
def auth_password(body: PasswordBody, request: Request, response: Response):
    if not auth.password_configured():
        raise HTTPException(503, "Es ist kein Passwort gesetzt (WEB_PASSWORD in der .env).")
    tier = netguard.client_tier(request)
    if tier == netguard.EXTERN:
        raise HTTPException(403, "Zugriff nur aus deinem Heimnetz oder über VPN.")
    if not auth.check_password(body.password):
        audit.record(actor="unbekannt", action="login.fehlgeschlagen", ok=False,
                     client_ip=request.client.host if request.client else None)
        raise HTTPException(401, "Falsches Passwort.")
    stage = auth.STAGE_PASSWORD if auth.discord_configured() else auth.STAGE_FULL
    auth.issue_cookie(response, auth.new_session(stage), _secure_cookie(request))
    return {"ok": True, "stage": stage, "discord_required": auth.discord_configured()}


@app.get("/api/auth/discord/start")
def auth_discord_start(request: Request):
    if not auth.discord_configured():
        raise HTTPException(503, "Der Discord-Login ist nicht eingerichtet "
                                 "(DISCORD_CLIENT_ID und DISCORD_CLIENT_SECRET).")
    if not auth.current_session(request):
        raise HTTPException(401, "Bitte zuerst das Passwort eingeben.")
    redirect_uri = str(request.url_for("auth_discord_callback"))
    state = auth.new_oauth_state()
    url = (f"https://discord.com/oauth2/authorize?client_id={config.DISCORD_CLIENT_ID}"
           f"&response_type=code&scope=identify&state={state}"
           f"&redirect_uri={redirect_uri}")
    return {"url": url}


@app.get("/api/auth/discord/callback", name="auth_discord_callback")
async def auth_discord_callback(request: Request, code: str = "", state: str = ""):
    if not auth.consume_oauth_state(state):
        raise HTTPException(400, "Der Login ist abgelaufen oder wurde nicht von dieser Seite "
                                 "gestartet. Bitte noch einmal versuchen.")
    if not auth.current_session(request):
        raise HTTPException(401, "Bitte zuerst das Passwort eingeben.")

    token = await discordapi.exchange_code(code, str(request.url_for("auth_discord_callback")))
    user = await discordapi.whoami(token)
    discord_id = str(user.get("id") or "")
    name = user.get("global_name") or user.get("username") or discord_id
    if not discord_id:
        raise HTTPException(502, "Discord hat kein Konto zurückgegeben.")

    if not auth.get_owner():
        auth.claim_owner(discord_id, name)
        audit.record(actor=name, action="besitzer.beansprucht", target=discord_id)
    if not auth.owner_matches(discord_id):
        audit.record(actor=name, action="login.fremdes_konto", target=discord_id, ok=False)
        raise HTTPException(403, "Dieses Discord-Konto ist nicht der eingetragene Besitzer "
                                 "dieser Seite.")

    response = RedirectResponse("/", status_code=303)
    auth.issue_cookie(response, auth.new_session(auth.STAGE_FULL, discord_id, name),
                      _secure_cookie(request))
    audit.record(actor=name, action="login.erfolgreich", target=discord_id)
    return response


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    auth.clear_cookie(response)
    return {"ok": True}


@app.post("/api/auth/owner/reset")
def auth_owner_reset(request: Request, caller: auth.Caller = Depends(auth.require_critical)):
    auth.reset_owner()
    audit.record(actor=caller.actor, action="besitzer.zurueckgesetzt",
                 client_ip=request.client.host if request.client else None)
    return {"ok": True, "hinweis": "Der nächste Discord-Login beansprucht den Zugang neu."}


# --------------------------------------------------------------------------
# Lesen
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    """Bewusst ohne Anmeldung: nur die Aussage „das Backend antwortet"."""
    return {"ok": True, "version": config.VERSION}


@app.get("/api/overview")
def api_overview(_: auth.Caller = Depends(auth.require_login)):
    return queries.overview()


@app.get("/api/players")
async def api_players(search: str = "", limit: int = 50,
                      _: auth.Caller = Depends(auth.require_login)):
    daten = queries.players(limit=min(max(limit, 1), 200), search=search)
    # Zu allen IDs, die in den Ranglisten vorkommen, gleich die Namen mitgeben.
    # Sonst müsste die Oberfläche für jede Liste einzeln nachfragen.
    ids = []
    for liste in daten.values():
        if isinstance(liste, list):
            ids += [z.get("user_id") for z in liste if isinstance(z, dict) and z.get("user_id")]
    daten["namen"] = await names.aufloesen(ids, nachladen=False)
    return daten


@app.get("/api/player/{user_id}")
async def api_player(user_id: str, _: auth.Caller = Depends(auth.require_login)):
    # Hier darf auch ein Name stehen - Zahlen abzutippen ist nichts, was man
    # jemandem zumuten sollte.
    kennung = await names.zu_id(user_id)
    if not kennung:
        raise HTTPException(404, f"Zu „{user_id}“ konnte niemand gefunden werden. "
                                 f"Tippe den Namen genauer oder nimm die Discord-ID.")
    try:
        daten = queries.player_detail(kennung)
    except ValueError:
        raise HTTPException(400, "Das ist keine gültige Discord-Nutzer-ID.") from None
    daten["name"] = (await names.aufloesen([kennung])).get(kennung, "")
    return daten


@app.get("/api/cards")
def api_cards(_: auth.Caller = Depends(auth.require_login)):
    return {"verfuegbar": cards.available(), "karten": cards.catalog(),
            "seltenheiten": {k: len(v) for k, v in cards.rarities().items()}}


@app.get("/api/statistics")
def api_statistics(range: str = "30d", _: auth.Caller = Depends(auth.require_login)):
    return queries.statistics(range if range in queries.RANGES else "30d")


@app.get("/api/logs")
def api_logs(limit: int = 300, level: str = "", search: str = "",
             _: auth.Caller = Depends(auth.require_login)):
    return {"zeilen": logparse.read_lines(limit=min(max(limit, 1), 2000),
                                          level=level.upper() if level else "", search=search)}


@app.get("/api/guild-settings")
def api_guild_settings(guild_id: str | None = None,
                       _: auth.Caller = Depends(auth.require_login)):
    return {"server": queries.guild_settings(guild_id), "bot": queries.bot_settings()}


@app.get("/api/audit")
def api_audit(limit: int = 100, guild_id: str | None = None,
              _: auth.Caller = Depends(auth.require_login)):
    return {"eintraege": audit.recent(limit=min(max(limit, 1), 500), guild_id=guild_id)}


# --------------------------------------------------------------------------
# Einstellungen
# --------------------------------------------------------------------------
class SettingsBody(BaseModel):
    changes: dict[str, str]


class KarteBody(BaseModel):
    name: str
    aenderungen: dict


class KartenNameBody(BaseModel):
    name: str


@app.get("/api/karten/aenderungen")
def api_karten_aenderungen(_: auth.Caller = Depends(auth.require_login)):
    return {"aenderungen": karteneditor.alle(),
            "aenderbar": list(karteneditor.AENDERBAR)}


@app.post("/api/karten/aendern")
def api_karte_aendern(body: KarteBody, request: Request,
                      caller: auth.Caller = Depends(auth.require_critical)):
    """Eine Karte ändern — wirkt beim Bot, sobald er den Auftrag abholt."""
    try:
        sauber = karteneditor.setze(body.name, body.aenderungen, von=caller.actor)
    except karteneditor.EditorFehler as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(actor=caller.actor, action="karte.geaendert", target=body.name,
                 detail=", ".join(f"{k}={v}" for k, v in sauber.items()),
                 client_ip=_ip(request))
    auftrag = jobs.create("cards.reload", None, {}, caller.actor)
    return {"gespeichert": sauber, "auftrag": auftrag}


@app.post("/api/karten/zuruecksetzen")
def api_karte_zuruecksetzen(body: KartenNameBody, request: Request,
                            caller: auth.Caller = Depends(auth.require_critical)):
    if not karteneditor.zuruecksetzen(body.name, von=caller.actor):
        raise HTTPException(404, f"Für „{body.name}“ ist keine Änderung gespeichert.")
    audit.record(actor=caller.actor, action="karte.zurueckgesetzt", target=body.name,
                 client_ip=_ip(request))
    auftrag = jobs.create("cards.reload", None, {}, caller.actor)
    return {"zurueckgesetzt": True, "auftrag": auftrag}


@app.get("/api/karten/{name}/verlauf")
def api_karte_verlauf(name: str, _: auth.Caller = Depends(auth.require_login)):
    return {"verlauf": karteneditor.verlauf(name)}


# --------------------------------------------------------------------------
# Testlauf: eine Karte gegen alle anderen
# --------------------------------------------------------------------------
class TestlaufBody(BaseModel):
    name: str
    spielweise: str = "beides"
    kaempfe_je_paarung: int = 200


@app.get("/api/karten/testlauf/moeglichkeiten")
def api_testlauf_moeglichkeiten(_: auth.Caller = Depends(auth.require_login)):
    try:
        return karteneditor.testlauf_moeglichkeiten()
    except karteneditor.EditorFehler as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/karten/testlauf")
def api_karte_testlauf(body: TestlaufBody, request: Request,
                       caller: auth.Caller = Depends(auth.require_login)):
    """Einen Testlauf in Auftrag geben.

    Gerechnet wird im Bot — er hat die Karten mit allen Änderungen und kann
    die Rechnung so portionieren, dass das Spiel nebenher weiterläuft. Ein
    Lauf dauert je nach Einstellung Minuten; deshalb nur einer auf einmal.
    """
    try:
        sauber = karteneditor.pruefe_testlauf(body.name, body.spielweise,
                                              body.kaempfe_je_paarung)
    except karteneditor.EditorFehler as exc:
        raise HTTPException(400, str(exc)) from exc

    laufend = [j for j in jobs.active() if j["kind"] == "cards.testlauf"]
    if laufend:
        raise HTTPException(409, f"Es läuft schon ein Testlauf für "
                                 f"„{laufend[0]['payload'].get('karte', '?')}“ "
                                 f"(Auftrag {laufend[0]['id']}). "
                                 f"Mehrere auf einmal würden den Bot ausbremsen.")

    # Die Zahl der Gegner steht erst beim Rechnen fest; als Gesamtwert taugt
    # sie trotzdem schon, damit der Balken nicht bei 0 von 0 steht.
    auftrag = jobs.create("cards.testlauf", None, sauber, caller.actor,
                          total=max(0, len(cards.catalog()) - 1))
    audit.record(actor=caller.actor, action="testlauf.gestartet", target=sauber["karte"],
                 detail=f"{sauber['kaempfe_je_paarung']} Kämpfe je Paarung, "
                        f"{sauber['spielweise']}",
                 client_ip=_ip(request))
    return auftrag


@app.get("/api/karten/{name}/testlaeufe")
def api_karte_testlaeufe(name: str, limit: int = 5,
                         _: auth.Caller = Depends(auth.require_login)):
    return {"laeufe": queries.card_testruns(name, limit=min(max(limit, 1), 25))}


@app.get("/api/mitschrift")
def api_mitschrift(_: auth.Caller = Depends(auth.require_login)):
    """Was die Zug-Mitschrift bisher gesammelt hat."""
    return queries.mitschrift_zahlen()


@app.get("/api/papierkorb")
def api_papierkorb(_: auth.Caller = Depends(auth.require_login)):
    return {"eintraege": sicherung.liste(),
            "aufbewahrung_tage": sicherung.AUFBEWAHRUNG_TAGE}


@app.post("/api/papierkorb/{eintrag_id}/zurueckholen")
def api_papierkorb_zurueck(eintrag_id: int, request: Request,
                           caller: auth.Caller = Depends(auth.require_critical)):
    try:
        ergebnis = sicherung.hole_zurueck(eintrag_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    audit.record(actor=caller.actor, action="papierkorb.zurueckgeholt",
                 target=str(eintrag_id), detail=f"{ergebnis['gesamt']} Zeilen",
                 client_ip=_ip(request))
    return ergebnis


@app.delete("/api/papierkorb/{eintrag_id}")
def api_papierkorb_weg(eintrag_id: int, request: Request,
                       caller: auth.Caller = Depends(auth.require_critical)):
    weg = sicherung.wirf_weg(eintrag_id)
    audit.record(actor=caller.actor, action="papierkorb.endgueltig_geloescht",
                 target=str(eintrag_id), client_ip=_ip(request), ok=weg)
    return {"geloescht": weg}


@app.get("/api/datenbank/pruefen")
def api_db_pruefen(_: auth.Caller = Depends(auth.require_login)):
    return sicherung.pruefe_datenbank()


@app.get("/api/datenbank/sicherung")
def api_db_sicherung(request: Request,
                     caller: auth.Caller = Depends(auth.require_critical)):
    """Eine Kopie der Datenbank zum Herunterladen.

    Nur aus dem Heimnetz: Die Datei enthaelt alles, was der Bot ueber alle
    Spieler weiss.
    """
    pfad = sicherung.erstelle_kopie()
    audit.record(actor=caller.actor, action="datenbank.gesichert",
                 detail=f"{pfad.stat().st_size} Bytes", client_ip=_ip(request))
    return FileResponse(pfad, filename=pfad.name, media_type="application/octet-stream")


@app.get("/api/bericht.csv")
def api_bericht(bereich: str = "spieler",
                caller: auth.Caller = Depends(auth.require_login)):
    """Zahlen als CSV zum Herunterladen — oeffnet sich direkt in Excel.

    Bewusst CSV und nicht xlsx: Das braucht keine zusaetzliche Bibliothek,
    laesst sich ueberall oeffnen, und der Inhalt bleibt lesbar, auch in zehn
    Jahren.
    """
    import csv
    import io

    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";")     # Semikolon: so erwartet es Excel hier

    if bereich == "spieler":
        daten = queries.players(limit=1000)
        schreiber.writerow(["Liste", "Discord-ID", "Wert"])
        for liste_name, schluessel in (("Karten", "top_karten"), ("Infinitydust", "top_dust"),
                                       ("Units", "top_units")):
            for zeile in daten.get(schluessel, []):
                schreiber.writerow([liste_name, zeile.get("user_id"), zeile.get("wert")])
    elif bereich == "protokoll":
        schreiber.writerow(["Zeitpunkt", "Wer", "Aktion", "Ziel", "Einzelheit", "Erfolgreich"])
        for e in audit.recent(limit=1000):
            schreiber.writerow([e.get("created_at"), e.get("actor"), e.get("action"),
                                e.get("target"), e.get("detail"),
                                "ja" if e.get("ok") else "nein"])
    else:
        raise HTTPException(400, "Unbekannter Bereich. Möglich: spieler, protokoll.")

    # BOM voranstellen, sonst zeigt Excel Umlaute als Buchstabensalat.
    inhalt = "﻿" + puffer.getvalue()
    return Response(
        content=inhalt, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="kartenbot-{bereich}.csv"'})


@app.get("/api/selbsttest")
async def api_selbsttest(_: auth.Caller = Depends(auth.require_login)):
    """Prueft der Reihe nach, was zum Betrieb noetig ist.

    Gedacht als erste Anlaufstelle, wenn etwas nicht geht: statt zu raten,
    warum keine Namen erscheinen, steht hier schwarz auf weiss, ob der
    Bot-Token vorhanden ist, ob er von Discord auch akzeptiert wird und ob
    die Datenbank beschreibbar ist.
    """
    ergebnis = []

    def melde(name: str, ok: bool, text: str, hinweis: str = ""):
        ergebnis.append({"name": name, "ok": ok, "text": text, "hinweis": hinweis})

    # 1. Datenbank lesen
    try:
        with database.read_connection() as con:
            anzahl = database.scalar(con, "SELECT COUNT(*) FROM user_karten")
        melde("Datenbank lesen", True, f"in Ordnung ({anzahl} Karteneinträge)")
    except Exception as exc:  # noqa: BLE001
        melde("Datenbank lesen", False, str(exc),
              "Stimmt KARTENBOT_DIR? Der Ordner mit bot.py und kartenbot.db muss eingebunden sein.")

    # 2. Datenbank schreiben - genau das, woran das Speichern von Einstellungen haengt
    try:
        with database.write_connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS web_schreibtest (x INTEGER)")
            con.execute("DROP TABLE web_schreibtest")
        melde("Datenbank beschreiben", True, "in Ordnung")
    except Exception as exc:  # noqa: BLE001
        melde("Datenbank beschreiben", False, str(exc),
              "Ohne Schreibrecht lassen sich weder Einstellungen speichern noch Karten vergeben.")

    # 3. Bot-Token vorhanden?
    if not discordapi.bot_token_available():
        melde("Bot-Token hinterlegt", False, "kein Token gesetzt",
              "BOT_TOKEN in den Umgebungsvariablen des Stacks eintragen und "
              "„Update the stack“ drücken. Ohne Token bleiben überall die IDs stehen.")
    else:
        melde("Bot-Token hinterlegt", True, "vorhanden")
        # 4. Akzeptiert Discord ihn auch? Nur so weiss man es sicher.
        try:
            ich = await discordapi.bot_user()
            name = ich.get("global_name") or ich.get("username") or "?"
            melde("Token gültig bei Discord", True, f"angemeldet als {name}")
        except Exception as exc:  # noqa: BLE001
            melde("Token gültig bei Discord", False, str(exc),
                  "Der Token wird abgelehnt. Tippfehler, Leerzeichen am Rand, "
                  "oder er wurde im Entwicklerportal neu erzeugt?")

    # 5. Namen im Zwischenspeicher
    try:
        with database.read_connection() as con:
            gemerkt = database.scalar(
                con, "SELECT COUNT(*) FROM web_discord_cache WHERE kind = 'user'")
        melde("Bekannte Namen", gemerkt > 0, f"{gemerkt} Namen gemerkt",
              "" if gemerkt else "Öffne einmal „Rollen & Mitglieder“ und wähle "
                                 "deinen Server — danach sind die Namen überall bekannt.")
    except Exception as exc:  # noqa: BLE001
        melde("Bekannte Namen", False, str(exc))

    return {"pruefungen": ergebnis,
            "alles_gut": all(p["ok"] for p in ergebnis)}


class NamesBody(BaseModel):
    ids: list[str]


@app.post("/api/names")
async def api_names(body: NamesBody, _: auth.Caller = Depends(auth.require_login)):
    """IDs zu Namen. Fehlende bleiben weg - die Oberflaeche zeigt dann die ID."""
    return {"namen": await names.aufloesen(body.ids[:200])}


@app.get("/api/names/search")
def api_names_search(q: str = "", _: auth.Caller = Depends(auth.require_login)):
    return {"treffer": names.suche(q)}


@app.post("/api/names/aufwaermen")
async def api_names_aufwaermen(guild_id: str,
                               _: auth.Caller = Depends(auth.require_login)):
    """Holt die Mitgliederliste eines Servers und merkt sich alle Namen.

    Das ist mit Abstand der guenstigste Weg: eine einzige Anfrage bringt den
    ganzen Server. Ohne das muesste jeder Name einzeln nachgeschlagen werden -
    hunderte Anfragen, die alle auf Discords Anfragebremse zaehlen.
    """
    mitglieder = await discordapi.all_members(guild_id, cap=20000)
    names.merke_mitglieder(mitglieder)
    with database.read_connection() as con:
        bekannt = database.scalar(
            con, "SELECT COUNT(*) FROM web_discord_cache WHERE kind = 'user'")
    return {"geladen": len(mitglieder), "bekannt": bekannt}


@app.get("/api/settings")
def api_settings(_: auth.Caller = Depends(auth.require_login)):
    return {"einstellungen": settings.describe()}


@app.post("/api/settings")
def api_settings_save(body: SettingsBody, request: Request,
                      caller: auth.Caller = Depends(auth.require_login)):
    try:
        settings.set_many(body.changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit.record(actor=caller.actor, action="einstellungen.geaendert",
                 detail=", ".join(sorted(body.changes)),
                 client_ip=request.client.host if request.client else None)
    return {"ok": True, "einstellungen": settings.describe()}


# --------------------------------------------------------------------------
# Discord: lesen
# --------------------------------------------------------------------------
@app.get("/api/discord/guilds")
async def api_discord_guilds(_: auth.Caller = Depends(auth.require_login)):
    return {"server": await discordapi.guilds()}


@app.get("/api/discord/{guild_id}/roles")
async def api_discord_roles(guild_id: str, _: auth.Caller = Depends(auth.require_login)):
    return await discordapi.manageable_roles(guild_id)


@app.get("/api/discord/{guild_id}/channels")
async def api_discord_channels(guild_id: str, _: auth.Caller = Depends(auth.require_login)):
    channels = await discordapi.channels(guild_id)
    # Kategorien (Typ 4) sind selbst keine Kanaele, geben aber die Ordnung vor.
    # Ihren Namen mitzugeben hilft bei Servern mit vielen gleich benannten
    # Kanaelen ("allgemein" gibt es gern mehrfach).
    kategorien = {c["id"]: c.get("name", "") for c in channels if c.get("type") == 4}
    lesbar = []
    for c in channels:
        if c.get("type") not in (0, 5, 15):        # Text, News, Forum
            continue
        eintrag = dict(c)
        eintrag["kategorie"] = kategorien.get(c.get("parent_id"), "")
        lesbar.append(eintrag)
    return {"kanaele": sorted(lesbar, key=lambda c: (c.get("kategorie", ""),
                                                    c.get("position", 0),
                                                    c.get("name", "")))}


@app.get("/api/discord/{guild_id}/members")
async def api_discord_members(guild_id: str, search: str = "", limit: int = 1000,
                              _: auth.Caller = Depends(auth.require_login)):
    members = await discordapi.all_members(guild_id, cap=min(max(limit, 1), 20000))
    # Die Liste ist ohnehin da - also gleich alle Namen merken. Danach kann die
    # ganze Seite Namen statt IDs zeigen, ohne Discord noch einmal zu fragen.
    names.merke_mitglieder(members)
    needle = search.strip().lower()
    if needle:
        def passt(m: dict) -> bool:
            user = m.get("user") or {}
            felder = (user.get("username"), user.get("global_name"), m.get("nick"), user.get("id"))
            return any(needle in str(f).lower() for f in felder if f)
        members = [m for m in members if passt(m)]
    return {"mitglieder": members, "anzahl": len(members)}


@app.get("/api/discord/{guild_id}/permissions/{user_id}")
async def api_discord_permissions(guild_id: str, user_id: str,
                                  _: auth.Caller = Depends(auth.require_login)):
    # Auch hier darf ein Name stehen. Ohne das ging nur die ID, und Discord
    # antwortete auf einen Namen mit einem nichtssagenden "Invalid Form Body".
    return await roles.explain_permissions(guild_id, await _person(user_id))


# --------------------------------------------------------------------------
# Aktionen auf der Datenbank
# --------------------------------------------------------------------------
class CurrencyBody(BaseModel):
    currency: str
    user_id: str
    amount: int
    remove: bool = False
    guild_id: str | None = None


class CardBody(BaseModel):
    user_id: str
    card_name: str
    amount: int = 1
    remove: bool = False


class RarityBody(BaseModel):
    user_id: str
    rarity: str
    remove: bool = False


class FlagBody(BaseModel):
    guild_id: str
    flag: str
    enabled: bool


class ToggleBody(BaseModel):
    guild_id: str
    key: str
    enabled: bool


class ChannelBody(BaseModel):
    guild_id: str
    channel_id: str
    allow: bool


class UserBody(BaseModel):
    user_id: str


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _person(eingabe: str) -> str:
    """Macht aus einer Eingabe eine Discord-ID - egal ob ID oder Name.

    Jedes Feld, in das man eine Person einträgt, geht hier durch. So muss
    niemand achtzehnstellige Zahlen abtippen, und wer die ID hat, kann sie
    trotzdem einfach einfügen.
    """
    kennung = await names.zu_id(eingabe)
    if not kennung:
        raise HTTPException(404, f"Zu „{eingabe}“ konnte niemand gefunden werden. "
                                 f"Öffne einmal „Rollen & Mitglieder“, damit die Namen "
                                 f"bekannt sind — oder nimm die Discord-ID.")
    return kennung


@app.post("/api/actions/currency")
async def api_currency(body: CurrencyBody, request: Request,
                       caller: auth.Caller = Depends(auth.require_login)):
    ziel = await _person(body.user_id)
    actor_id = int(caller.discord_id) if (caller.discord_id or "").isdigit() else 0
    result = actions.adjust_currency(
        currency=body.currency, user_id=ziel, amount=body.amount,
        remove=body.remove, actor_id=actor_id,
        guild_id=int(body.guild_id) if (body.guild_id or "").isdigit() else 0)
    audit.record(actor=caller.actor, action="waehrung.entfernt" if body.remove else "waehrung.gegeben",
                 guild_id=body.guild_id, target=ziel,
                 detail=f"{result['gebucht']} {result['waehrung']}", client_ip=_ip(request))
    return result


@app.post("/api/actions/card")
async def api_card(body: CardBody, request: Request,
                   caller: auth.Caller = Depends(auth.require_login)):
    ziel = await _person(body.user_id)
    result = actions.adjust_card(user_id=ziel, card_name=body.card_name,
                                 amount=body.amount, remove=body.remove)
    audit.record(actor=caller.actor, action="karte.entfernt" if body.remove else "karte.gegeben",
                 target=ziel, detail=f"{result['gebucht']}x {result['karte']}",
                 client_ip=_ip(request))
    return result


@app.post("/api/actions/rarity-group")
async def api_rarity(body: RarityBody, request: Request,
                     caller: auth.Caller = Depends(auth.require_login)):
    body.user_id = await _person(body.user_id)
    result = actions.adjust_rarity_group(user_id=body.user_id, rarity=body.rarity,
                                         remove=body.remove)
    audit.record(actor=caller.actor,
                 action="seltenheit.entfernt" if body.remove else "seltenheit.gegeben",
                 target=body.user_id, detail=f"{result['karten']} Karten ({result['seltenheit']})",
                 client_ip=_ip(request))
    return result


@app.post("/api/actions/flag")
def api_flag(body: FlagBody, request: Request,
             caller: auth.Caller = Depends(auth.require_login)):
    result = actions.set_core_flag(guild_id=body.guild_id, flag=body.flag, enabled=body.enabled)
    audit.record(actor=caller.actor, action="schalter.gesetzt", guild_id=body.guild_id,
                 target=body.flag, detail="an" if body.enabled else "aus", client_ip=_ip(request))
    return result


@app.post("/api/actions/toggle")
def api_toggle(body: ToggleBody, request: Request,
               caller: auth.Caller = Depends(auth.require_login)):
    result = actions.set_feature_toggle(guild_id=body.guild_id, key=body.key,
                                        enabled=body.enabled)
    audit.record(actor=caller.actor, action="funktion.geschaltet", guild_id=body.guild_id,
                 target=body.key, detail="an" if body.enabled else "aus", client_ip=_ip(request))
    return result


@app.post("/api/actions/channel")
def api_channel(body: ChannelBody, request: Request,
                caller: auth.Caller = Depends(auth.require_login)):
    result = actions.set_allowed_channel(guild_id=body.guild_id, channel_id=body.channel_id,
                                         allow=body.allow)
    audit.record(actor=caller.actor, action="kanal.freigegeben" if body.allow else "kanal.gesperrt",
                 guild_id=body.guild_id, target=body.channel_id, client_ip=_ip(request))
    return result


@app.post("/api/actions/player/delete")
def api_player_delete(body: UserBody, request: Request,
                      caller: auth.Caller = Depends(auth.require_critical)):
    result = actions.delete_player(body.user_id, actor=caller.actor)
    audit.record(actor=caller.actor, action="spieler.geloescht", target=body.user_id,
                 detail=f"{result['gesamt']} Zeilen", client_ip=_ip(request))
    return result


# --------------------------------------------------------------------------
# Rollen und Mitglieder
# --------------------------------------------------------------------------
class RoleApplyBody(BaseModel):
    guild_id: str
    user_ids: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    action: str = "add"
    reason: str = "Über Kartenbot Web"
    from_role_id: str | None = None
    expires_in_minutes: int | None = None
    dry_run: bool = False


@app.post("/api/roles/apply")
async def api_roles_apply(body: RoleApplyBody, request: Request,
                          caller: auth.Caller = Depends(auth.require_critical)):
    return await roles.apply(body, caller, _ip(request))


@app.get("/api/roles/{guild_id}/history")
def api_roles_history(guild_id: str, limit: int = 200,
                      _: auth.Caller = Depends(auth.require_login)):
    return {"verlauf": queries.role_history(guild_id, limit=min(max(limit, 1), 1000))}


@app.get("/api/roles/{guild_id}/orphans")
async def api_roles_orphans(guild_id: str, _: auth.Caller = Depends(auth.require_login)):
    return await roles.orphan_roles(guild_id)


# --------------------------------------------------------------------------
# Aufträge
# --------------------------------------------------------------------------
class JobBody(BaseModel):
    kind: str
    guild_id: str | None = None
    payload: dict = Field(default_factory=dict)


@app.get("/api/jobs")
def api_jobs(guild_id: str | None = None, limit: int = 50,
             _: auth.Caller = Depends(auth.require_login)):
    return {"auftraege": jobs.recent(guild_id, limit=min(max(limit, 1), 200)),
            "laufend": jobs.active(guild_id)}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int, _: auth.Caller = Depends(auth.require_login)):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Diesen Auftrag gibt es nicht.")
    return job


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: int, request: Request,
                   caller: auth.Caller = Depends(auth.require_login)):
    if not jobs.request_cancel(job_id):
        raise HTTPException(400, "Dieser Auftrag ist schon fertig oder abgebrochen.")
    audit.record(actor=caller.actor, action="auftrag.abgebrochen", target=str(job_id),
                 client_ip=_ip(request))
    return {"ok": True}


# --------------------------------------------------------------------------
# Verlaufs-Analyse
# --------------------------------------------------------------------------
class ScanBody(BaseModel):
    guild_id: str
    channel_ids: list[str] = Field(default_factory=list)
    range_key: str = "3m"
    use_ai: bool = False


SCAN_RANGES = {
    "none": "gar nichts", "3m": "letzte 3 Monate", "6m": "letzte 6 Monate",
    "1y": "letztes Jahr", "all": "alles",
}


@app.post("/api/scan/start")
def api_scan_start(body: ScanBody, request: Request,
                   caller: auth.Caller = Depends(auth.require_login)):
    if body.range_key not in SCAN_RANGES:
        raise HTTPException(400, "Unbekannter Zeitraum. Möglich: "
                                 + ", ".join(SCAN_RANGES))
    if body.range_key == "none":
        return {"ok": True, "uebersprungen": True,
                "hinweis": "Zeitraum steht auf 'gar nichts' — es wurde nichts gestartet."}
    if not body.channel_ids:
        raise HTTPException(400, "Bitte mindestens einen Kanal auswählen.")
    laufend = [j for j in jobs.active(body.guild_id) if j["kind"] == "scan.history"]
    if laufend:
        raise HTTPException(409, f"Für diesen Server läuft schon ein Scan "
                                 f"(Auftrag {laufend[0]['id']}).")

    job = jobs.create("scan.history", body.guild_id, {
        "channel_ids": body.channel_ids,
        "range_key": body.range_key,
        "use_ai": bool(body.use_ai and settings.get_bool("ai.enabled")),
        "badwords": settings.get_list("scan.badwords"),
        "chunk_pause_ms": settings.get_int("scan.chunk_pause_ms"),
        "max_messages_per_channel": settings.get_int("scan.max_messages_per_channel"),
    }, requested_by=caller.actor, total=len(body.channel_ids))
    audit.record(actor=caller.actor, action="analyse.gestartet", guild_id=body.guild_id,
                 detail=f"{len(body.channel_ids)} Kanäle, {SCAN_RANGES[body.range_key]}",
                 client_ip=_ip(request))
    return job


@app.get("/api/scan/{guild_id}/runs")
def api_scan_runs(guild_id: str, _: auth.Caller = Depends(auth.require_login)):
    return {"laeufe": queries.scan_runs(guild_id)}


@app.get("/api/scan/{guild_id}/profiles")
def api_scan_profiles(guild_id: str, tag: str = "", limit: int = 500,
                      _: auth.Caller = Depends(auth.require_login)):
    return {"profile": queries.member_profiles(guild_id, limit=min(max(limit, 1), 2000), tag=tag)}


@app.get("/api/scan/{guild_id}/moderation")
def api_scan_moderation(guild_id: str, _: auth.Caller = Depends(auth.require_login)):
    return {"ereignisse": queries.mod_events(guild_id)}


# --------------------------------------------------------------------------
# KI
# --------------------------------------------------------------------------
class FindModelBody(BaseModel):
    candidates: list[str] = Field(default_factory=list)
    timeout: float = 90.0


@app.get("/api/ai/status")
async def api_ai_status(_: auth.Caller = Depends(auth.require_login)):
    return await ollama.status()


@app.get("/api/ai/models")
async def api_ai_models(_: auth.Caller = Depends(auth.require_login)):
    return {"modelle": await ollama.models()}


@app.post("/api/ai/find-model")
async def api_ai_find(body: FindModelBody, _: auth.Caller = Depends(auth.require_login)):
    return await ollama.find_model(body.candidates or None,
                                   per_model_timeout=min(max(body.timeout, 10.0), 600.0))


# --------------------------------------------------------------------------
# Oberfläche
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    page = STATIC_DIR / "index.html"
    if not page.exists():
        return HTMLResponse("<h1>Die Oberfläche fehlt.</h1>"
                            "<p>Die Datei static/index.html wurde nicht gefunden.</p>",
                            status_code=500)
    html = page.read_text(encoding="utf-8").replace("__VERSION__", config.VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/{dateiname}", include_in_schema=False)
def statische_datei(dateiname: str):
    """Liefert style.css und app.js auch ohne /static davor aus.

    Die Seite verweist bewusst relativ auf ihre Dateien, damit sie hinter
    WebHafen funktioniert - dort liegen sie im Wurzelverzeichnis. Damit
    dieselbe Seite auch beim Backend selbst laedt, muessen die Dateien hier
    ebenfalls direkt unter / erreichbar sein.

    Bewusst eng gehalten: keine Unterordner, keine versteckten Dateien, und
    nur was wirklich im Ordner liegt. Sonst waere das ein Weg, beliebige
    Dateien aus dem Container zu lesen.
    """
    if not STATIC_DIR.exists() or dateiname.startswith("."):
        raise HTTPException(404, "Nicht gefunden.")
    ziel = STATIC_DIR / dateiname
    # resolve() loest .. auf - danach muss die Datei immer noch im Ordner liegen.
    if ziel.resolve().parent != STATIC_DIR.resolve() or not ziel.is_file():
        raise HTTPException(404, "Nicht gefunden.")
    return FileResponse(ziel)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
