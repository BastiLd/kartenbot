"""Konfiguration des Dashboards — alles über Env-Variablen steuerbar.

Liest zusätzlich eine lokale website/.env (falls vorhanden), damit das
Dashboard unabhängig vom Bot konfiguriert werden kann. Secrets werden nie
hartkodiert.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

WEBSITE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = WEBSITE_DIR.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


# Reihenfolge: echte Env-Variablen > website/.env > Projekt-.env (nur DB-Pfad relevant)
_load_dotenv(WEBSITE_DIR / ".env")
_load_dotenv(PROJECT_ROOT / ".env")


def _resolve_db_path() -> str:
    raw = os.getenv("KARTENBOT_DB_PATH", "kartenbot.db")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


DB_PATH: str = _resolve_db_path()
LOG_PATH: str = os.getenv("KARTENBOT_LOG_PATH", str(PROJECT_ROOT / "bot.log"))

# Passwort für Admin-Aktionen. Ohne gesetztes Passwort bleiben alle
# Schreib-Endpunkte deaktiviert (read-only Dashboard).
DASHBOARD_PASSWORD: str | None = os.getenv("DASHBOARD_PASSWORD") or None

# Secret für die Signatur des Session-Cookies. Wenn nicht gesetzt, wird bei
# jedem Start ein zufälliges erzeugt (Logins überleben dann keinen Neustart —
# für lokalen Betrieb völlig ok).
SESSION_SECRET: str = os.getenv("DASHBOARD_SESSION_SECRET") or secrets.token_hex(32)

# Session-Lebensdauer in Sekunden (Standard: 12 Stunden)
SESSION_TTL: int = int(os.getenv("DASHBOARD_SESSION_TTL", "43200"))

HOST: str = os.getenv("DASHBOARD_HOST", "127.0.0.1")
PORT: int = int(os.getenv("DASHBOARD_PORT", "8080"))

# Zeitzone für Tages-/Stunden-Auswertungen (wie stats_export.py)
TIMEZONE: str = os.getenv("DASHBOARD_TZ", "Europe/Vienna")

# Bot-Token (nur lesend genutzt) für die Auflösung von User-/Gilden-Namen über
# die Discord-API. Ohne Token zeigt das Dashboard weiterhin nur IDs an.
# Fallback wie beim Bot (config.py): bot_token.txt / token.txt im Projekt-Root.
def _resolve_bot_token() -> str | None:
    token = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_TOKEN")
    if token:
        return token
    for filename in ("bot_token.txt", "token.txt"):
        path = PROJECT_ROOT / filename
        if path.exists():
            token = path.read_text(encoding="utf-8").strip().strip("\"'")
            if token:
                return token
    return None


BOT_TOKEN: str | None = _resolve_bot_token()
