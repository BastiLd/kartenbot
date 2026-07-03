"""Auflösung von Discord-IDs zu echten Namen (User & Gilden).

Quelle ist die Discord-REST-API mit dem Bot-Token (``BOT_TOKEN``); aufgelöste
Namen werden in der Tabelle ``dashboard_name_cache`` in der Bot-DB persistiert,
damit sie Container-Neustarts überleben und die Namenssuche darauf laufen kann.
Ohne Token (oder bei read-only DB) bleibt das Dashboard einfach bei IDs.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from . import config
from .database import DashboardDBError, fetch_all, read_connection, write_connection

API_BASE = "https://discord.com/api/v10"
NAME_TTL = 3 * 24 * 3600   # erfolgreich aufgelöste Namen alle 3 Tage auffrischen
NEG_TTL = 3600             # Fehlschläge (404, Netzfehler) 1 h nicht erneut versuchen
MAX_FETCH_PER_CALL = 25    # Latenz-Schutz: max. Discord-Requests pro /api/names-Aufruf
TIMEOUT = 4.0
MAX_IDS = 200

_lock = threading.Lock()
# (kind, id) -> (name | None, fetched_at) — Negativ-Cache nur im Speicher
_mem: dict[tuple[str, int], tuple[str | None, int]] = {}
_table_ready = False
_rate_limited_until = 0.0


def enabled() -> bool:
    return bool(config.BOT_TOKEN)


def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    try:
        with write_connection() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS dashboard_name_cache ("
                " kind TEXT NOT NULL, id INTEGER NOT NULL, name TEXT NOT NULL,"
                " updated_at INTEGER NOT NULL, PRIMARY KEY (kind, id))"
            )
        _table_ready = True
    except DashboardDBError:
        pass  # read-only DB → nur In-Memory-Cache


def _store(kind: str, id_: int, name: str) -> None:
    _ensure_table()
    if not _table_ready:
        return
    try:
        with write_connection() as con:
            con.execute(
                "INSERT INTO dashboard_name_cache (kind, id, name, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, id) DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at",
                (kind, id_, name, int(time.time())),
            )
    except DashboardDBError:
        pass


def _load_cached(kind: str, ids: list[int]) -> dict[int, tuple[str, int]]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    try:
        with read_connection() as con:
            rows = fetch_all(
                con,
                f"SELECT id, name, updated_at FROM dashboard_name_cache "
                f"WHERE kind = ? AND id IN ({placeholders})",
                (kind, *ids),
            )
    except DashboardDBError:
        return {}
    return {int(r["id"]): (r["name"], int(r["updated_at"])) for r in rows}


def _discord_get(path: str) -> tuple[dict | None, int]:
    req = urllib.request.Request(
        API_BASE + path,
        headers={"Authorization": f"Bot {config.BOT_TOKEN}", "User-Agent": "KartenbotDashboard/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return json.loads(res.read().decode("utf-8")), res.status
    except urllib.error.HTTPError as exc:
        return None, exc.code
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None, 0


def _fetch_name(kind: str, id_: int) -> tuple[str | None, int]:
    if kind == "user":
        data, status = _discord_get(f"/users/{id_}")
        if data:
            return str(data.get("global_name") or data.get("username") or "").strip() or None, status
        return None, status
    data, status = _discord_get(f"/guilds/{id_}")
    if data:
        return str(data.get("name") or "").strip() or None, status
    return None, status


def _clean_ids(raw: list) -> list[int]:
    out: list[int] = []
    for value in raw[: MAX_IDS * 2]:
        try:
            i = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in out:
            out.append(i)
    return out[:MAX_IDS]


def resolve(user_ids: list, guild_ids: list) -> dict:
    """Gibt nur erfolgreich aufgelöste Namen zurück; alles andere bleibt ID."""
    global _rate_limited_until
    result: dict[str, dict[str, str]] = {"users": {}, "guilds": {}}
    fetch_budget = MAX_FETCH_PER_CALL

    for kind, raw, out_key in (("user", user_ids, "users"), ("guild", guild_ids, "guilds")):
        ids = _clean_ids(raw)
        if not ids:
            continue
        cached = _load_cached(kind, ids)
        now = int(time.time())
        to_fetch: list[int] = []
        for i in ids:
            name, ts = cached.get(i, (None, 0))
            if name:
                result[out_key][str(i)] = name
                if now - ts < NAME_TTL:
                    continue
            with _lock:
                mem = _mem.get((kind, i))
            if mem and mem[0] is None and now - mem[1] < NEG_TTL:
                continue  # kürzlich fehlgeschlagen — nicht erneut hämmern
            to_fetch.append(i)

        if not enabled():
            continue
        for i in to_fetch:
            if fetch_budget <= 0 or time.time() < _rate_limited_until:
                break
            fetch_budget -= 1
            name, status = _fetch_name(kind, i)
            with _lock:
                _mem[(kind, i)] = (name, int(time.time()))
            if status == 429:
                _rate_limited_until = time.time() + 30
                break
            if name:
                result[out_key][str(i)] = name
                _store(kind, i, name)

    return result


def search(query: str, limit: int = 20) -> list[dict]:
    """Sucht im Namens-Cache (User & Gilden) — Basis für die Suche per Name."""
    q = str(query or "").strip()
    if len(q) < 2:
        return []
    try:
        with read_connection() as con:
            rows = fetch_all(
                con,
                "SELECT kind, id, name FROM dashboard_name_cache WHERE name LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (f"%{q}%", max(1, min(int(limit), 50))),
            )
    except DashboardDBError:
        return []
    return [{"kind": r["kind"], "id": str(r["id"]), "name": r["name"]} for r in rows]
