"""Alle Lese-Auswertungen (read-only Verbindung).

Zeitraum-Schlüssel: today / 7d / 30d / 90d / all — Tagesgrenzen in der
eingestellten Zeitzone, genau wie services/stats_export.py im Bot.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .database import fetch_all, fetch_one, read_connection, scalar

TZ = ZoneInfo(config.TIMEZONE)
EVENT_CAP = 200_000

RANGES = {
    "today": "Heute", "7d": "7 Tage", "30d": "30 Tage",
    "90d": "90 Tage", "all": "Gesamt",
}


def range_to_since(range_key: str) -> int:
    """Startzeitpunkt als Unix-Zeit. 0 bedeutet: alles."""
    now = datetime.now(TZ)
    if range_key == "today":
        return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    for key, days in (("7d", 7), ("30d", 30), ("90d", 90)):
        if range_key == key:
            return int((now - timedelta(days=days)).timestamp())
    return 0


def _payload(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _events(con, since: int, where: str = "", params: tuple = ()) -> list[dict]:
    clause = "WHERE created_at >= ?" if since else "WHERE 1=1"
    sql = (f"SELECT * FROM analytics_events {clause} {where} "
           f"ORDER BY created_at ASC LIMIT {EVENT_CAP}")
    return fetch_all(con, sql, (since, *params) if since else params)


def _day(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, TZ).strftime("%Y-%m-%d")


def _top(counter: Counter, limit: int = 15) -> list[dict]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


# ------------------------------------------------------------------ Zustand --
def overview() -> dict:
    from .logparse import health_from_log

    health = health_from_log()
    db_file = Path(config.DB_PATH)
    with read_connection() as con:
        heartbeat = fetch_one(con, "SELECT value FROM bot_settings WHERE key = 'heartbeat_at'")
        started = fetch_one(con, "SELECT value FROM bot_settings WHERE key = 'started_at'")
        counts = {
            "spieler": scalar(con, "SELECT COUNT(DISTINCT user_id) FROM user_karten"),
            "karten_gesamt": scalar(con, "SELECT COALESCE(SUM(anzahl), 0) FROM user_karten"),
            "dust_gesamt": scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM user_infinitydust"),
            "units_gesamt": scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM user_units"),
            "teams": scalar(con, "SELECT COUNT(*) FROM user_teams"),
            "server": scalar(con, "SELECT COUNT(*) FROM guild_config"),
            "sitzungen": scalar(con, "SELECT COUNT(*) FROM active_sessions"),
        }
        letzte_aktionen = fetch_all(
            con, "SELECT created_at, event_type, command_name, actor_user_id, guild_id "
                 "FROM analytics_events ORDER BY created_at DESC LIMIT 20")

    now = datetime.now(TZ).timestamp()
    beat = int(heartbeat["value"]) if heartbeat and str(heartbeat["value"]).isdigit() else 0
    start = int(started["value"]) if started and str(started["value"]).isdigit() else 0
    online = bool(beat) and (now - beat) < 180

    return {
        "online": online,
        "letzter_lebenszeichen": beat or None,
        "gestartet_am": start or None,
        "laufzeit_sekunden": int(now - start) if start else None,
        "db_groesse": db_file.stat().st_size if db_file.exists() else 0,
        "log": health,
        "zahlen": counts,
        "letzte_aktionen": letzte_aktionen,
        "version": config.VERSION,
    }


# ------------------------------------------------------------------ Spieler --
def players(limit: int = 50, search: str = "") -> dict:
    with read_connection() as con:
        where, params = "", ()
        if search.strip():
            where = "WHERE user_id LIKE ?"
            params = (f"%{search.strip()}%",)
        top_karten = fetch_all(
            con, f"SELECT user_id, SUM(anzahl) AS wert FROM user_karten {where} "
                 f"GROUP BY user_id ORDER BY wert DESC LIMIT ?", params + (limit,))
        top_dust = fetch_all(
            con, f"SELECT user_id, amount AS wert FROM user_infinitydust {where} "
                 f"ORDER BY amount DESC LIMIT ?", params + (limit,))
        top_units = fetch_all(
            con, f"SELECT user_id, amount AS wert FROM user_units {where} "
                 f"ORDER BY amount DESC LIMIT ?", params + (limit,))
        verteilung = fetch_all(
            con, "SELECT anzahl AS besitz, COUNT(*) AS spieler FROM ("
                 " SELECT user_id, COUNT(*) AS anzahl FROM user_karten GROUP BY user_id"
                 ") GROUP BY anzahl ORDER BY anzahl")
    return {"top_karten": top_karten, "top_dust": top_dust, "top_units": top_units,
            "kartenverteilung": verteilung}


def player_detail(user_id: str | int) -> dict:
    # Der Bot legt user_id als Zahl ab. SQLite vergleicht Zahl und Text nicht
    # miteinander — mit einem String als Parameter fände man nie etwas.
    uid = int(user_id)
    with read_connection() as con:
        karten = fetch_all(
            con, "SELECT karten_name, anzahl FROM user_karten WHERE user_id = ? "
                 "ORDER BY anzahl DESC, karten_name", (uid,))
        dust = scalar(con, "SELECT amount FROM user_infinitydust WHERE user_id = ?", (uid,))
        units = scalar(con, "SELECT amount FROM user_units WHERE user_id = ?", (uid,))
        team = fetch_one(con, "SELECT * FROM user_teams WHERE user_id = ?", (uid,))
        buffs = fetch_all(con, "SELECT * FROM user_card_buffs WHERE user_id = ?", (uid,))
        daily = fetch_one(con, "SELECT * FROM user_daily WHERE user_id = ?", (uid,))
        einladungen = fetch_one(con, "SELECT * FROM invite_stats WHERE user_id = ?", (uid,))
        ereignisse = fetch_all(
            con, "SELECT created_at, event_type, command_name, guild_id, hero_name, attack_name "
                 "FROM analytics_events WHERE actor_user_id = ? OR target_user_id = ? "
                 "ORDER BY created_at DESC LIMIT 150", (uid, uid))
        dust_protokoll = fetch_all(
            con, "SELECT * FROM admin_dust_audit WHERE target_id = ? "
                 "ORDER BY created_at DESC LIMIT 50", (uid,))
        # Die Tabellen dieser Website legen IDs als Text ab — daher hier str().
        profile = fetch_all(
            con, "SELECT guild_id, stats_json, tags_json, ai_summary, updated_at "
                 "FROM member_profiles WHERE user_id = ?", (str(uid),))
        moderation = fetch_all(
            con, "SELECT * FROM mod_events WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (str(uid),))
        rollen_verlauf = fetch_all(
            con, "SELECT * FROM role_grants WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (str(uid),))

    for row in profile:
        row["stats"] = _payload(row.pop("stats_json", None))
        try:
            row["tags"] = json.loads(row.pop("tags_json", None) or "[]")
        except ValueError:
            row["tags"] = []

    return {
        "user_id": uid,
        "karten": karten,
        "karten_gesamt": sum(k["anzahl"] for k in karten),
        "karten_verschieden": len(karten),
        "infinitydust": dust,
        "units": units,
        "team": team,
        "buffs": buffs,
        "daily": daily,
        "einladungen": einladungen,
        "ereignisse": ereignisse,
        "dust_protokoll": dust_protokoll,
        "profile": profile,
        "moderation": moderation,
        "rollen_verlauf": rollen_verlauf,
    }


# ---------------------------------------------------------------- Statistik --
def statistics(range_key: str = "30d") -> dict:
    since = range_to_since(range_key)
    with read_connection() as con:
        events = _events(con, since)
        karten_top = fetch_all(
            con, "SELECT karten_name, SUM(anzahl) AS anzahl FROM user_karten "
                 "GROUP BY karten_name ORDER BY anzahl DESC LIMIT 20")
        einladungen = fetch_all(
            con, "SELECT user_id, invited_count FROM invite_stats "
                 "ORDER BY invited_count DESC LIMIT 15")

    pro_tag: Counter = Counter()
    pro_stunde = [0] * 24
    befehle: Counter = Counter()
    typen: Counter = Counter()
    helden: Counter = Counter()
    angriffe: Counter = Counter()
    siege: defaultdict = defaultdict(lambda: {"siege": 0, "kaempfe": 0})

    for event in events:
        ts = event.get("created_at") or 0
        pro_tag[_day(ts)] += 1
        pro_stunde[datetime.fromtimestamp(ts, TZ).hour] += 1
        typen[event.get("event_type") or "?"] += 1
        if event.get("command_name"):
            befehle[event["command_name"]] += 1
        if event.get("hero_name"):
            helden[event["hero_name"]] += 1
        if event.get("attack_name"):
            angriffe[event["attack_name"]] += 1
        if event.get("event_type") == "fight_result":
            data = _payload(event.get("payload_json"))
            gewinner, verlierer = data.get("winner_hero"), data.get("loser_hero")
            if gewinner:
                siege[gewinner]["siege"] += 1
                siege[gewinner]["kaempfe"] += 1
            if verlierer:
                siege[verlierer]["kaempfe"] += 1

    siegquote = sorted(
        ({"held": held, "siege": w["siege"], "kaempfe": w["kaempfe"],
          "quote": round(w["siege"] * 100 / w["kaempfe"]) if w["kaempfe"] else 0}
         for held, w in siege.items() if w["kaempfe"] >= 3),
        key=lambda r: (-r["quote"], -r["kaempfe"]),
    )[:15]

    return {
        "zeitraum": range_key,
        "ereignisse_gesamt": len(events),
        "pro_tag": [{"tag": tag, "anzahl": n} for tag, n in sorted(pro_tag.items())],
        "pro_stunde": [{"stunde": f"{h:02d}:00", "anzahl": n} for h, n in enumerate(pro_stunde)],
        "top_befehle": _top(befehle),
        "ereignistypen": _top(typen, 20),
        "top_helden": _top(helden),
        "top_angriffe": _top(angriffe),
        "siegquote": siegquote,
        "top_karten": karten_top,
        "top_einlader": einladungen,
    }


# -------------------------------------------------------------------- Server --
def guild_settings(guild_id: str | None = None) -> list[dict]:
    with read_connection() as con:
        where = "WHERE guild_id = ?" if guild_id else ""
        # Bot-Tabellen: guild_id als Zahl. Eigene Tabellen: als Text.
        bot_params = (int(guild_id),) if guild_id else ()
        web_params = (str(guild_id),) if guild_id else ()
        guilds = fetch_all(con, f"SELECT * FROM guild_config {where}", bot_params)
        kanaele = fetch_all(con, f"SELECT * FROM guild_allowed_channels {where}", bot_params)
        sichtbarkeit = fetch_all(con, f"SELECT * FROM guild_message_visibility {where}", bot_params)
        schalter = fetch_all(con, f"SELECT * FROM guild_feature_toggles {where}", web_params)

    nach_server = defaultdict(list)
    for row in kanaele:
        nach_server[str(row["guild_id"])].append(row["channel_id"])
    sicht = defaultdict(dict)
    for row in sichtbarkeit:
        sicht[str(row["guild_id"])][row["message_key"]] = row["visibility"]
    flags = defaultdict(dict)
    for row in schalter:
        flags[str(row["guild_id"])][row["feature_key"]] = bool(row["enabled"])

    out = []
    for row in guilds:
        gid = str(row["guild_id"])
        out.append({
            "guild_id": gid,
            "wartungsmodus": bool(row.get("maintenance_mode")),
            "beta": bool(row.get("beta_enabled")),
            "alpha": bool(row.get("alpha_enabled")),
            "erlaubte_kanaele": nach_server.get(gid, []),
            "sichtbarkeit": sicht.get(gid, {}),
            "schalter": flags.get(gid, {}),
        })
    return out


def bot_settings() -> dict:
    with read_connection() as con:
        rows = fetch_all(con, "SELECT key, value FROM bot_settings")
    return {row["key"]: row["value"] for row in rows}


# ------------------------------------------------------------------ Analyse --
def scan_runs(guild_id: str | None = None, limit: int = 25) -> list[dict]:
    sql = "SELECT * FROM scan_runs"
    params: tuple = ()
    if guild_id:
        sql += " WHERE guild_id = ?"
        params = (str(guild_id),)
    sql += " ORDER BY id DESC LIMIT ?"
    with read_connection() as con:
        rows = fetch_all(con, sql, params + (int(limit),))
    for row in rows:
        row["summary"] = _payload(row.pop("summary_json", None))
        try:
            row["channels"] = json.loads(row.pop("channels_json", None) or "[]")
        except ValueError:
            row["channels"] = []
    return rows


def member_profiles(guild_id: str, limit: int = 500, tag: str = "") -> list[dict]:
    with read_connection() as con:
        rows = fetch_all(
            con, "SELECT * FROM member_profiles WHERE guild_id = ? "
                 "ORDER BY updated_at DESC LIMIT ?", (str(guild_id), int(limit)))
    out = []
    for row in rows:
        row["stats"] = _payload(row.pop("stats_json", None))
        try:
            row["tags"] = json.loads(row.pop("tags_json", None) or "[]")
        except ValueError:
            row["tags"] = []
        if tag and tag not in row["tags"]:
            continue
        out.append(row)
    return out


def mod_events(guild_id: str, limit: int = 200) -> list[dict]:
    with read_connection() as con:
        return fetch_all(
            con, "SELECT * FROM mod_events WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
            (str(guild_id), int(limit)))


def role_history(guild_id: str, limit: int = 200) -> list[dict]:
    with read_connection() as con:
        return fetch_all(
            con, "SELECT * FROM role_grants WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (str(guild_id), int(limit)))
