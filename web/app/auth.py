"""Zugang zu Kartenbot Web.

Zwei Schlösser hintereinander:

  1. Passwort  — ohne das ist gar nichts sichtbar.
  2. Discord   — danach musst du dich mit dem Discord-Konto anmelden, das als
                 Besitzer eingetragen ist. Ist noch keiner eingetragen, wird
                 der erste erfolgreiche Login dauerhaft zum Besitzer.

Dazu kommt die Netzstufe aus netguard.py: kritische Aktionen gehen nur aus dem
Heimnetz. Die drei Prüfungen sind unabhängig — eine ersetzt die andere nie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Response

from . import config, database, netguard

COOKIE_NAME = "kbweb_session"

STAGE_PASSWORD = "pw"      # Passwort ok, Discord fehlt noch
STAGE_FULL = "full"        # Passwort + Discord ok


# --------------------------------------------------------------------------
# Sitzungs-Cookie: signiert, damit niemand seinen Inhalt fälschen kann.
# --------------------------------------------------------------------------
def _sign(payload: bytes) -> str:
    return hmac.new(config.SESSION_SECRET.encode(), payload, hashlib.sha256).hexdigest()


def _encode(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{body}.{_sign(raw)}"


def _decode(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, _, signature = token.rpartition(".")
    padding = "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(body + padding)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(raw), signature):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data


def issue_cookie(response: Response, data: dict, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME, _encode(data), max_age=config.SESSION_TTL,
        httponly=True, samesite="lax", secure=secure, path="/",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def new_session(stage: str, discord_id: str | None = None, name: str | None = None) -> dict:
    return {
        "stage": stage,
        "did": discord_id,
        "name": name,
        "exp": int(time.time()) + config.SESSION_TTL,
    }


# --------------------------------------------------------------------------
# Passwort
# --------------------------------------------------------------------------
def password_configured() -> bool:
    return bool(config.WEB_PASSWORD)


def check_password(candidate: str) -> bool:
    if not config.WEB_PASSWORD:
        return False
    return hmac.compare_digest(str(candidate or ""), config.WEB_PASSWORD)


# --------------------------------------------------------------------------
# Besitzer (Discord)
# --------------------------------------------------------------------------
def discord_configured() -> bool:
    return bool(config.DISCORD_CLIENT_ID and config.DISCORD_CLIENT_SECRET)


def get_owner() -> dict | None:
    with database.read_connection() as con:
        row = database.fetch_one(
            con, "SELECT owner_discord_id, owner_name, claimed_at FROM web_auth WHERE id = 1"
        )
    if row and row.get("owner_discord_id"):
        return row
    if config.OWNER_DISCORD_ID:
        return {"owner_discord_id": config.OWNER_DISCORD_ID, "owner_name": None,
                "claimed_at": None, "from_config": True}
    return None


def claim_owner(discord_id: str, name: str | None) -> None:
    """Trägt den Besitzer ein — aber nur, wenn noch keiner drinsteht."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with database.write_connection() as con:
        con.execute(
            "UPDATE web_auth SET owner_discord_id = ?, owner_name = ?, claimed_at = ? "
            "WHERE id = 1 AND (owner_discord_id IS NULL OR owner_discord_id = '')",
            (str(discord_id), name, now),
        )


def owner_matches(discord_id: str) -> bool:
    owner = get_owner()
    if not owner:
        return False
    return hmac.compare_digest(str(owner["owner_discord_id"]), str(discord_id))


def reset_owner() -> None:
    with database.write_connection() as con:
        con.execute(
            "UPDATE web_auth SET owner_discord_id = NULL, owner_name = NULL, claimed_at = NULL "
            "WHERE id = 1"
        )


# --------------------------------------------------------------------------
# OAuth-Zustand (schützt vor untergeschobenen Logins)
# --------------------------------------------------------------------------
_oauth_states: dict[str, float] = {}


def new_oauth_state() -> str:
    now = time.time()
    for key, created in list(_oauth_states.items()):
        if now - created > 600:
            _oauth_states.pop(key, None)
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = now
    return state


def consume_oauth_state(state: str) -> bool:
    return _oauth_states.pop(state or "", None) is not None


# --------------------------------------------------------------------------
# Abhängigkeiten für die Endpunkte
# --------------------------------------------------------------------------
@dataclass
class Caller:
    stage: str
    discord_id: str | None
    name: str | None
    tier: str

    @property
    def actor(self) -> str:
        return self.name or self.discord_id or "unbekannt"


def current_session(request: Request) -> dict | None:
    return _decode(request.cookies.get(COOKIE_NAME))


def require_login(request: Request) -> Caller:
    """Angemeldet heißt: Passwort ok UND — falls Discord eingerichtet ist —
    auch als Besitzer per Discord bestätigt."""
    if not password_configured():
        raise HTTPException(
            503,
            "Es ist kein Passwort gesetzt. Trage WEB_PASSWORD in die .env ein und starte neu — "
            "ohne Passwort bleibt die Seite aus Sicherheitsgründen gesperrt.",
        )
    session = current_session(request)
    if not session:
        raise HTTPException(401, "Bitte anmelden.")
    tier = netguard.client_tier(request)
    if tier == netguard.EXTERN:
        raise HTTPException(
            403,
            "Zugriff nur aus deinem Heimnetz oder über VPN. "
            f"Deine Adresse zählt gerade als {netguard.tier_label(tier)}.",
        )
    if discord_configured() and session.get("stage") != STAGE_FULL:
        raise HTTPException(401, "Zweiter Schritt fehlt: bitte mit Discord anmelden.")
    return Caller(
        stage=session.get("stage", STAGE_PASSWORD),
        discord_id=session.get("did"),
        name=session.get("name"),
        tier=tier,
    )


def require_critical(caller: Caller = Depends(require_login)) -> Caller:
    """Für die mächtigen Aktionen: Rollen vergeben, Kick, Bann, Massenaktionen."""
    if not netguard.allows(caller.tier, netguard.LAN):
        raise HTTPException(
            403,
            "Diese Aktion ist absichtlich auf dein Heimnetz beschränkt. "
            f"Du bist gerade über {netguard.tier_label(caller.tier)} verbunden. "
            "Die erlaubten Netzbereiche kannst du in den Einstellungen ändern.",
        )
    if discord_configured() and not (caller.discord_id and owner_matches(caller.discord_id)):
        raise HTTPException(403, "Diese Aktion darf nur der eingetragene Besitzer ausführen.")
    return caller


def status(request: Request) -> dict:
    session = current_session(request)
    owner = get_owner()
    tier = netguard.client_tier(request)
    return {
        "password_required": True,
        "password_configured": password_configured(),
        "discord_configured": discord_configured(),
        "stage": (session or {}).get("stage"),
        "discord_id": (session or {}).get("did"),
        "name": (session or {}).get("name"),
        "authenticated": bool(session) and (
            not discord_configured() or (session or {}).get("stage") == STAGE_FULL
        ),
        "owner_set": bool(owner),
        "owner_name": (owner or {}).get("owner_name"),
        "tier": tier,
        "tier_label": netguard.tier_label(tier),
        "may_critical": netguard.allows(tier, netguard.LAN),
        "version": config.VERSION,
    }
