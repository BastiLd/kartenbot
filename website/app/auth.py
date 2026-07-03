"""Login-Schutz für Schreib-Endpunkte.

Einfaches Passwort aus der Env (DASHBOARD_PASSWORD) + HMAC-signiertes
Session-Cookie mit Ablaufzeit. Kein Passwort gesetzt => Schreib-Endpunkte
sind komplett deaktiviert.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, Response

from . import config

COOKIE_NAME = "dash_session"


def _sign(expires: int) -> str:
    payload = str(expires)
    mac = hmac.new(config.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def _verify(token: str) -> bool:
    try:
        payload, mac = token.split(".", 1)
        expires = int(payload)
    except (ValueError, AttributeError):
        return False
    expected = hmac.new(config.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return False
    return expires > int(time.time())


def admin_enabled() -> bool:
    return bool(config.DASHBOARD_PASSWORD)


def check_password(password: str) -> bool:
    if not admin_enabled():
        return False
    return hmac.compare_digest(str(password or ""), str(config.DASHBOARD_PASSWORD))


def create_session(response: Response) -> None:
    expires = int(time.time()) + config.SESSION_TTL
    response.set_cookie(
        COOKIE_NAME,
        _sign(expires),
        max_age=config.SESSION_TTL,
        httponly=True,
        samesite="strict",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME, "")
    return bool(token) and _verify(token)


def require_admin(request: Request) -> None:
    """Dependency für alle schreibenden Endpunkte."""
    if not admin_enabled():
        raise HTTPException(status_code=503, detail="Admin-Aktionen deaktiviert: DASHBOARD_PASSWORD ist nicht gesetzt.")
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Nicht eingeloggt.")
