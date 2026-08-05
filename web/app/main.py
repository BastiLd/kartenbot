"""Kartenbot Web — Schnittstelle und Auslieferung der Oberfläche.

Aufbau:
  /            → die Oberfläche (statische Dateien)
  /api/...     → alles andere

Zugang siehe auth.py. Lesende Endpunkte brauchen eine Anmeldung, schreibende
zusätzlich die passende Netzstufe. Discord-Aktionen laufen je nach Einstellung
über die Auftragstabelle (der Bot führt aus) oder direkt.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (actions, audit, auth, cards, config, database, discordapi, jobs,
               logparse, netguard, ollama, queries, roles, schema, settings)

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
def api_players(search: str = "", limit: int = 50,
                _: auth.Caller = Depends(auth.require_login)):
    return queries.players(limit=min(max(limit, 1), 200), search=search)


@app.get("/api/player/{user_id}")
def api_player(user_id: str, _: auth.Caller = Depends(auth.require_login)):
    try:
        return queries.player_detail(user_id)
    except ValueError:
        raise HTTPException(400, "Das ist keine gültige Discord-Nutzer-ID.") from None


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
    lesbar = [c for c in channels if c.get("type") in (0, 5, 15)]     # Text, News, Forum
    return {"kanaele": sorted(lesbar, key=lambda c: (c.get("position", 0), c.get("name", "")))}


@app.get("/api/discord/{guild_id}/members")
async def api_discord_members(guild_id: str, search: str = "", limit: int = 1000,
                              _: auth.Caller = Depends(auth.require_login)):
    members = await discordapi.all_members(guild_id, cap=min(max(limit, 1), 20000))
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
    return await roles.explain_permissions(guild_id, user_id)


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


@app.post("/api/actions/currency")
def api_currency(body: CurrencyBody, request: Request,
                 caller: auth.Caller = Depends(auth.require_login)):
    actor_id = int(caller.discord_id) if (caller.discord_id or "").isdigit() else 0
    result = actions.adjust_currency(
        currency=body.currency, user_id=body.user_id, amount=body.amount,
        remove=body.remove, actor_id=actor_id,
        guild_id=int(body.guild_id) if (body.guild_id or "").isdigit() else 0)
    audit.record(actor=caller.actor, action="waehrung.entfernt" if body.remove else "waehrung.gegeben",
                 guild_id=body.guild_id, target=body.user_id,
                 detail=f"{result['gebucht']} {result['waehrung']}", client_ip=_ip(request))
    return result


@app.post("/api/actions/card")
def api_card(body: CardBody, request: Request,
             caller: auth.Caller = Depends(auth.require_login)):
    result = actions.adjust_card(user_id=body.user_id, card_name=body.card_name,
                                 amount=body.amount, remove=body.remove)
    audit.record(actor=caller.actor, action="karte.entfernt" if body.remove else "karte.gegeben",
                 target=body.user_id, detail=f"{result['gebucht']}x {result['karte']}",
                 client_ip=_ip(request))
    return result


@app.post("/api/actions/rarity-group")
def api_rarity(body: RarityBody, request: Request,
               caller: auth.Caller = Depends(auth.require_login)):
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
    result = actions.delete_player(body.user_id)
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


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
