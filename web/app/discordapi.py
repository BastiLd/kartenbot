"""Zugriff auf die Discord-Schnittstelle.

Zwei Arten von Aufrufen:
  * mit dem Bot-Token — lesen (Server, Rollen, Kanäle, Mitglieder) und, wenn in
    den Einstellungen "Direkt" gewählt ist, auch schreiben.
  * mit einem Nutzer-Token aus dem Discord-Login — nur, um herauszufinden,
    wer sich da gerade anmeldet.

Discord bremst zu viele Anfragen aus (Antwort 429). Das wird hier zentral
abgefangen und mit der von Discord genannten Wartezeit wiederholt, damit es
nicht jeder Aufrufer selbst tun muss.
"""
from __future__ import annotations

import asyncio

import httpx

from . import config

API_BASE = "https://discord.com/api/v10"
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
MAX_RETRIES = 4


class DiscordError(Exception):
    """Fehler von Discord, bereits in verständliches Deutsch übersetzt."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _explain(status: int, body: dict | None) -> str:
    code = (body or {}).get("code")
    message = (body or {}).get("message") or ""
    if status == 401:
        return "Discord hat den Bot-Token abgelehnt. Stimmt BOT_TOKEN noch?"
    if status == 403:
        return ("Discord erlaubt das nicht. Meist fehlt dem Bot das Recht dafür, "
                "oder die Zielrolle steht über der Rolle des Bots.")
    if status == 404:
        return "Das gibt es auf Discord nicht (mehr) — Server, Kanal oder Mitglied wurde nicht gefunden."
    if code == 50013:
        return "Dem Bot fehlen die nötigen Rechte auf diesem Server."
    if code == 50001:
        return "Der Bot hat auf diesen Kanal keinen Zugriff."
    if status >= 500:
        return "Discord hat gerade selbst ein Problem. Bitte später noch einmal versuchen."
    return f"Discord meldet einen Fehler ({status}): {message or 'ohne nähere Angabe'}"


def bot_token_available() -> bool:
    return bool(config.BOT_TOKEN)


async def _request(method: str, path: str, *, token: str | None = None,
                   bot: bool = True, **kwargs) -> dict | list | None:
    if bot and not config.BOT_TOKEN:
        raise DiscordError("Es ist kein Bot-Token hinterlegt (BOT_TOKEN in der .env).")
    auth = f"Bot {config.BOT_TOKEN}" if bot else f"Bearer {token}"
    headers = {"Authorization": auth, "User-Agent": "KartenbotWeb (intern, 1.0)"}
    headers.update(kwargs.pop("headers", {}))

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.request(method, API_BASE + path, headers=headers, **kwargs)
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES - 1:
                    raise DiscordError(f"Discord ist nicht erreichbar: {exc}") from exc
                await asyncio.sleep(1.0 + attempt)
                continue

            if response.status_code == 429:
                try:
                    wait = float(response.json().get("retry_after", 1.0))
                except Exception:
                    wait = 1.0
                await asyncio.sleep(min(max(wait, 0.5), 30.0))
                continue

            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = None
                raise DiscordError(_explain(response.status_code, body), response.status_code)

            if response.status_code == 204 or not response.content:
                return None
            return response.json()

    raise DiscordError("Discord bremst gerade zu stark aus. Bitte gleich noch einmal versuchen.", 429)


# --------------------------------------------------------------------------
# Login mit Discord
# --------------------------------------------------------------------------
async def exchange_code(code: str, redirect_uri: str) -> str:
    data = {
        "client_id": config.DISCORD_CLIENT_ID,
        "client_secret": config.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(f"{API_BASE}/oauth2/token", data=data)
    if response.status_code >= 400:
        raise DiscordError("Der Discord-Login ist fehlgeschlagen. Bitte noch einmal versuchen.",
                           response.status_code)
    return response.json()["access_token"]


async def whoami(access_token: str) -> dict:
    user = await _request("GET", "/users/@me", token=access_token, bot=False)
    return user or {}


# --------------------------------------------------------------------------
# Lesen
# --------------------------------------------------------------------------
async def bot_user() -> dict:
    return await _request("GET", "/users/@me") or {}


async def guilds() -> list[dict]:
    result = await _request("GET", "/users/@me/guilds?with_counts=true")
    return result or []


async def guild(guild_id: str) -> dict:
    return await _request("GET", f"/guilds/{guild_id}?with_counts=true") or {}


async def roles(guild_id: str) -> list[dict]:
    return await _request("GET", f"/guilds/{guild_id}/roles") or []


async def channels(guild_id: str) -> list[dict]:
    return await _request("GET", f"/guilds/{guild_id}/channels") or []


async def member(guild_id: str, user_id: str) -> dict:
    return await _request("GET", f"/guilds/{guild_id}/members/{user_id}") or {}


async def members_page(guild_id: str, after: str = "0", limit: int = 1000) -> list[dict]:
    return await _request(
        "GET", f"/guilds/{guild_id}/members?limit={min(limit, 1000)}&after={after}"
    ) or []


async def all_members(guild_id: str, cap: int = 20000) -> list[dict]:
    """Alle Mitglieder eines Servers. Braucht das Server-Mitglieder-Recht
    (Server Members Intent) im Discord-Entwicklerportal."""
    out: list[dict] = []
    after = "0"
    while len(out) < cap:
        page = await members_page(guild_id, after=after)
        if not page:
            break
        out.extend(page)
        after = page[-1]["user"]["id"]
        if len(page) < 1000:
            break
    return out


async def audit_log(guild_id: str, action_type: int | None = None, limit: int = 100) -> dict:
    query = f"?limit={min(limit, 100)}"
    if action_type is not None:
        query += f"&action_type={action_type}"
    return await _request("GET", f"/guilds/{guild_id}/audit-logs{query}") or {}


async def channel_messages(channel_id: str, before: str | None = None, limit: int = 100) -> list[dict]:
    query = f"?limit={min(limit, 100)}"
    if before:
        query += f"&before={before}"
    return await _request("GET", f"/channels/{channel_id}/messages{query}") or []


# --------------------------------------------------------------------------
# Schreiben (nur im Modus "Direkt"; sonst macht das der Bot)
# --------------------------------------------------------------------------
def _reason(text: str) -> dict:
    return {"X-Audit-Log-Reason": text[:400]}


async def add_role(guild_id: str, user_id: str, role_id: str, reason: str) -> None:
    await _request("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
                   headers=_reason(reason))


async def remove_role(guild_id: str, user_id: str, role_id: str, reason: str) -> None:
    await _request("DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
                   headers=_reason(reason))


async def modify_member(guild_id: str, user_id: str, payload: dict, reason: str) -> dict:
    return await _request("PATCH", f"/guilds/{guild_id}/members/{user_id}",
                          json=payload, headers=_reason(reason)) or {}


async def kick(guild_id: str, user_id: str, reason: str) -> None:
    await _request("DELETE", f"/guilds/{guild_id}/members/{user_id}", headers=_reason(reason))


async def ban(guild_id: str, user_id: str, reason: str, delete_message_seconds: int = 0) -> None:
    await _request("PUT", f"/guilds/{guild_id}/bans/{user_id}",
                   json={"delete_message_seconds": delete_message_seconds},
                   headers=_reason(reason))


async def create_role(guild_id: str, payload: dict, reason: str) -> dict:
    return await _request("POST", f"/guilds/{guild_id}/roles",
                          json=payload, headers=_reason(reason)) or {}


async def modify_role(guild_id: str, role_id: str, payload: dict, reason: str) -> dict:
    return await _request("PATCH", f"/guilds/{guild_id}/roles/{role_id}",
                          json=payload, headers=_reason(reason)) or {}


# --------------------------------------------------------------------------
# Rangordnung der Rollen
# --------------------------------------------------------------------------
def highest_position(role_list: list[dict], role_ids: set[str]) -> int:
    positions = [r.get("position", 0) for r in role_list if str(r.get("id")) in role_ids]
    return max(positions) if positions else 0


async def manageable_roles(guild_id: str) -> dict:
    """Welche Rollen darf der Bot auf diesem Server vergeben?

    Discord erlaubt nur Rollen **unterhalb** der höchsten eigenen Rolle, und
    nur wenn der Bot überhaupt das Recht "Rollen verwalten" hat. Beides wird
    hier ermittelt und pro Rolle als `manageable` mitgeliefert, damit die
    Oberfläche es direkt anzeigen kann.
    """
    me = await bot_user()
    role_list, bot_member = await asyncio.gather(roles(guild_id), member(guild_id, me["id"]))

    bot_role_ids = {str(r) for r in bot_member.get("roles", [])}
    bot_top = highest_position(role_list, bot_role_ids)

    # "Rollen verwalten" steckt in Bit 28 der Rechtemaske; Administrator (Bit 3)
    # schließt es mit ein.
    permissions = 0
    for role in role_list:
        if str(role.get("id")) in bot_role_ids:
            permissions |= int(role.get("permissions", 0))
    may_manage = bool(permissions & (1 << 28)) or bool(permissions & (1 << 3))

    out = []
    for role in sorted(role_list, key=lambda r: r.get("position", 0), reverse=True):
        is_everyone = str(role.get("id")) == str(guild_id)
        blocked = None
        if role.get("managed"):
            blocked = "Diese Rolle gehört zu einer App oder Abo-Funktion und kann nicht vergeben werden."
        elif is_everyone:
            blocked = "Die @everyone-Rolle hat jeder automatisch."
        elif not may_manage:
            blocked = "Dem Bot fehlt das Recht „Rollen verwalten“ auf diesem Server."
        elif role.get("position", 0) >= bot_top:
            blocked = "Diese Rolle steht über der Rolle des Bots — Discord lässt das nicht zu."
        out.append({
            "id": str(role.get("id")),
            "name": role.get("name"),
            "color": role.get("color", 0),
            "position": role.get("position", 0),
            "managed": bool(role.get("managed")),
            "permissions": str(role.get("permissions", "0")),
            "manageable": blocked is None,
            "blocked_reason": blocked,
        })
    return {"roles": out, "bot_top_position": bot_top, "bot_may_manage_roles": may_manage,
            "bot_user_id": me.get("id")}
