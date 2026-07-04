"""Kartenbot-Dashboard — FastAPI-App.

Start (aus dem Ordner website/):
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
"""
from __future__ import annotations

import os
import re
import time
import urllib.request
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import actions, auth, config, names, queries
from .cards import all_card_names
from .database import DashboardDBError
from .logparse import parse_log

WEBSITE_DIR = Path(__file__).resolve().parent.parent
VERSION = (WEBSITE_DIR / "VERSION").read_text(encoding="utf-8").strip() if (WEBSITE_DIR / "VERSION").exists() else "0.0.0"

app = FastAPI(title="Kartenbot Dashboard", version=VERSION, docs_url=None, redoc_url=None)

STATIC_DIR = WEBSITE_DIR / "static"

VALID_RANGES = {"today", "7d", "30d", "all"}


def _range(range: str = Query("7d")) -> str:
    return range if range in VALID_RANGES else "7d"


@app.exception_handler(DashboardDBError)
async def db_error_handler(_request: Request, exc: DashboardDBError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ------------------------------------------------------------------ Reads ---

@app.get("/api/health")
def api_health():
    return queries.health_stats()


@app.get("/api/logs")
def api_logs(
    limit: int = Query(200, ge=1, le=1000),
    level: str | None = Query(None),
    q: str | None = Query(None),
):
    return parse_log(limit=limit, level=level, query=q)


@app.get("/api/overview")
def api_overview(range: str = Depends(_range)):
    return queries.overview_stats(range)


@app.get("/api/players")
def api_players(range: str = Depends(_range)):
    return queries.player_stats(range)


@app.get("/api/battles")
def api_battles(range: str = Depends(_range)):
    return queries.battle_stats(range)


@app.get("/api/analytics")
def api_analytics(range: str = Depends(_range)):
    return queries.analytics_stats(range)


@app.get("/api/user/{user_id}")
def api_user(user_id: int):
    return queries.user_detail(user_id)


@app.get("/api/user/{user_id}/full")
def api_user_full(user_id: int):
    return queries.user_full(user_id)


@app.get("/api/meta")
def api_meta():
    return {
        "cards": all_card_names(),
        "admin_enabled": auth.admin_enabled(),
        "guild_flags": sorted(actions.GUILD_FLAGS),
        "names_enabled": names.enabled(),
        "version": VERSION,
    }


@app.post("/api/names")
def api_names(payload: dict = Body(...)):
    users = payload.get("users") or []
    guilds = payload.get("guilds") or []
    if not isinstance(users, list) or not isinstance(guilds, list):
        raise HTTPException(status_code=400, detail="users/guilds müssen Listen sein.")
    return names.resolve(users, guilds)


@app.get("/api/names/search")
def api_names_search(q: str = Query("", max_length=64)):
    return {"results": names.search(q)}


# ------------------------------------------------------------ Update-Check --

UPDATE_URL = os.getenv(
    "DASHBOARD_UPDATE_URL",
    "https://raw.githubusercontent.com/BastiLd/kartenbot/main/website/VERSION",
)
_update_cache: dict = {"at": 0.0, "latest": None}


def _version_tuple(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


@app.get("/api/update-check")
def api_update_check(force: bool = Query(False)):
    now = time.time()
    if force or now - _update_cache["at"] > 1800:
        try:
            with urllib.request.urlopen(UPDATE_URL, timeout=4) as res:
                _update_cache["latest"] = res.read().decode("utf-8").strip()[:32]
        except OSError:
            _update_cache["latest"] = _update_cache.get("latest")
        _update_cache["at"] = now
    latest = _update_cache["latest"]
    return {
        "current": VERSION,
        "latest": latest,
        "update_available": bool(latest) and _version_tuple(latest) > _version_tuple(VERSION),
    }


# ------------------------------------------------------------------- Auth ---

@app.post("/api/admin/login")
def api_login(response: Response, payload: dict = Body(...)):
    if not auth.admin_enabled():
        raise HTTPException(status_code=503, detail="DASHBOARD_PASSWORD ist nicht gesetzt — Admin deaktiviert.")
    if not auth.check_password(str(payload.get("password", ""))):
        raise HTTPException(status_code=401, detail="Falsches Passwort.")
    auth.create_session(response)
    return {"ok": True}


@app.post("/api/admin/logout")
def api_logout(response: Response):
    auth.clear_session(response)
    return {"ok": True}


@app.get("/api/admin/status")
def api_admin_status(request: Request):
    return {
        "admin_enabled": auth.admin_enabled(),
        "authenticated": auth.is_authenticated(request),
    }


# ---------------------------------------------------------------- Actions ---

@app.post("/api/admin/currency", dependencies=[Depends(auth.require_admin)])
def api_currency(payload: dict = Body(...)):
    return actions.adjust_currency(
        kind=str(payload.get("kind", "")),
        user_id=int(payload.get("user_id", 0)),
        amount=int(payload.get("amount", 0)),
        action=str(payload.get("action", "")),
    )


@app.post("/api/admin/card", dependencies=[Depends(auth.require_admin)])
def api_card(payload: dict = Body(...)):
    return actions.adjust_card(
        user_id=int(payload.get("user_id", 0)),
        card_name=str(payload.get("card_name", "")),
        amount=int(payload.get("amount", 1)),
        action=str(payload.get("action", "give")),
    )


@app.post("/api/admin/tradingpost/delete", dependencies=[Depends(auth.require_admin)])
def api_trading_delete(payload: dict = Body(...)):
    return actions.delete_trading_entry(str(payload.get("code", "")))


@app.get("/api/admin/guilds", dependencies=[Depends(auth.require_admin)])
def api_guilds():
    return actions.list_guild_configs()


@app.post("/api/admin/guild-flag", dependencies=[Depends(auth.require_admin)])
def api_guild_flag(payload: dict = Body(...)):
    return actions.set_guild_flag(
        guild_id=int(payload.get("guild_id", 0)),
        flag=str(payload.get("flag", "")),
        enabled=bool(payload.get("enabled", False)),
    )


@app.post("/api/admin/cleanup", dependencies=[Depends(auth.require_admin)])
def api_cleanup(payload: dict = Body(...)):
    return actions.cleanup(str(payload.get("what", "")))


@app.post("/api/admin/session/end", dependencies=[Depends(auth.require_admin)])
def api_session_end(payload: dict = Body(...)):
    return actions.end_session(int(payload.get("session_id", 0)))


@app.post("/api/admin/thread/close", dependencies=[Depends(auth.require_admin)])
def api_thread_close(payload: dict = Body(...)):
    return actions.close_thread(
        thread_id=int(payload.get("thread_id", 0)),
        delete_discord=bool(payload.get("delete_discord", False)),
    )


@app.get("/api/admin/audit", dependencies=[Depends(auth.require_admin)])
def api_audit(limit: int = Query(100, ge=1, le=500)):
    return actions.audit_log(limit=limit)


# ----------------------------------------------------------------- Static ---

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
