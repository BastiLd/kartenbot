"""Konfiguration von Kartenbot Web.

Alles über Env-Variablen steuerbar; zusätzlich wird eine lokale web/.env
gelesen. Werte, die du später in der Oberfläche ändern kannst (Ollama-Adresse,
Modell, Wortlisten …), liegen NICHT hier, sondern in der Tabelle web_settings —
siehe settings.py. Hier steht nur, was beim Start feststehen muss.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = WEB_DIR.parent


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


_load_dotenv(WEB_DIR / ".env")
_load_dotenv(PROJECT_ROOT / ".env")


def _resolve_db_path() -> str:
    raw = os.getenv("KARTENBOT_DB_PATH", "kartenbot.db")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


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


def _csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [part.strip() for part in raw.split(",") if part.strip()]


DB_PATH: str = _resolve_db_path()
LOG_PATH: str = os.getenv("KARTENBOT_LOG_PATH", str(PROJECT_ROOT / "bot.log"))

HOST: str = os.getenv("WEB_HOST", "127.0.0.1")
PORT: int = int(os.getenv("WEB_PORT", "8090"))
TIMEZONE: str = os.getenv("WEB_TZ", "Europe/Vienna")

# Stufe 1 des Zugangs: ohne Passwort ist die Seite komplett gesperrt.
WEB_PASSWORD: str | None = os.getenv("WEB_PASSWORD") or None

SESSION_SECRET: str = os.getenv("WEB_SESSION_SECRET") or secrets.token_hex(32)
SESSION_TTL: int = int(os.getenv("WEB_SESSION_TTL", "43200"))

BOT_TOKEN: str | None = _resolve_bot_token()

# Stufe 2 des Zugangs: Discord-Login. Ohne Client-ID/Secret bleibt es beim
# Passwort allein — dann sind kritische Aktionen gesperrt (siehe auth.py).
DISCORD_CLIENT_ID: str | None = os.getenv("DISCORD_CLIENT_ID") or None
DISCORD_CLIENT_SECRET: str | None = os.getenv("DISCORD_CLIENT_SECRET") or None

# Besitzer-Discord-ID. Standard ist die ID, die im Bot hinterlegt ist.
# Ist sie leer, beansprucht der erste erfolgreiche Login den Zugang dauerhaft.
OWNER_DISCORD_ID: str | None = os.getenv("WEB_OWNER_DISCORD_ID", "965593518745731152") or None

# Stufe 3 des Zugangs: Netzbereiche.
#   LAN  = darf alles, auch kritische Aktionen (Rollen, Kick, Bann)
#   VPN  = darf alles außer kritischen Aktionen
TRUSTED_LAN: list[str] = _csv(
    "WEB_TRUSTED_LAN", "127.0.0.0/8,::1/128,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12"
)
TRUSTED_VPN: list[str] = _csv("WEB_TRUSTED_VPN", "100.64.0.0/10,fd7a:115c:a1e0::/48")

# Läuft hinter Caddy/WebHafen? Dann kommt die echte Client-IP aus dem
# Header X-Forwarded-For — den darf man nur glauben, wenn der direkte
# Absender selbst vertrauenswürdig ist (siehe netguard.py).
TRUST_PROXY: bool = os.getenv("WEB_TRUST_PROXY", "1") not in ("0", "false", "False", "")

OLLAMA_URL_DEFAULT: str = os.getenv("OLLAMA_URL", "http://192.168.68.10:11434")

VERSION: str = (WEB_DIR / "VERSION").read_text(encoding="utf-8").strip() if (
    WEB_DIR / "VERSION"
).exists() else "0.0.0"
