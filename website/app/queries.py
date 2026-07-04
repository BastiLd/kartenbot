"""Alle Lese-Auswertungen des Dashboards (read-only Verbindung).

Zeitraum-Filter: ``today`` / ``7d`` / ``30d`` / ``all`` — Tagesgrenzen in der
konfigurierten Zeitzone (wie services/stats_export.py).
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .database import fetch_all, fetch_one, read_connection, scalar

TZ = ZoneInfo(config.TIMEZONE)

EVENT_FETCH_CAP = 200_000  # Sicherheitslimit für In-Python-Auswertungen


def range_to_since(range_key: str) -> int:
    """0 == alles."""
    now = datetime.now(TZ)
    if range_key == "today":
        return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if range_key == "7d":
        return int((now - timedelta(days=7)).timestamp())
    if range_key == "30d":
        return int((now - timedelta(days=30)).timestamp())
    return 0


def _day_text(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, TZ).strftime("%Y-%m-%d")


def _hour_text(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, TZ).strftime("%H:00")


def _payload(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _events(con, since: int, columns: str = "*", extra_where: str = "", params: tuple = ()) -> list[dict]:
    where = "WHERE created_at >= ?" if since else "WHERE 1=1"
    sql = (
        f"SELECT {columns} FROM analytics_events {where} {extra_where} "
        f"ORDER BY created_at ASC LIMIT {EVENT_FETCH_CAP}"
    )
    return fetch_all(con, sql, (since, *params) if since else params)


# ---------------------------------------------------------------- Health ----

def health_stats() -> dict:
    from .logparse import health_from_log

    info = health_from_log()
    db_path = Path(config.DB_PATH)
    with read_connection() as con:
        active_sessions = fetch_all(
            con, "SELECT session_id, kind, guild_id, status, updated_at FROM active_sessions ORDER BY updated_at DESC LIMIT 100"
        )
        managed_threads = fetch_all(
            con, "SELECT thread_id, guild_id, kind, status, updated_at FROM managed_threads ORDER BY updated_at DESC LIMIT 100"
        )
        open_fights = scalar(con, "SELECT COUNT(*) FROM fight_requests WHERE status = 'pending'")
        afk_timers = scalar(con, "SELECT COUNT(*) FROM afk_timers")
        total_events = scalar(con, "SELECT COUNT(*) FROM analytics_events")
        last_event = scalar(con, "SELECT MAX(created_at) FROM analytics_events", default=0)
        heartbeat_raw = scalar(con, "SELECT value FROM bot_settings WHERE key = 'heartbeat_at'", default=None)
        started_raw = scalar(con, "SELECT value FROM bot_settings WHERE key = 'started_at'", default=None)

    db_size = db_path.stat().st_size if db_path.exists() else 0
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            db_size += side.stat().st_size

    def _to_int(raw) -> int | None:
        try:
            value = int(str(raw).strip())
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None

    now = int(time.time())
    heartbeat_at = _to_int(heartbeat_raw)
    started_at = _to_int(started_raw)

    # Online-Status: Der Bot-Heartbeat (jede Minute in bot_settings) ist die
    # präzise Quelle. Fallback für alte Bot-Versionen ohne Heartbeat:
    # Log-Heuristik + letztes Analytics-Event (15-min-Fenster).
    if heartbeat_at:
        online = (now - heartbeat_at) < 180
        online_source = "heartbeat"
    else:
        online = bool(info["online_guess"]) or bool(last_event and now - int(last_event) < 900)
        online_source = "log"

    uptime_seconds = None
    if online and online_source == "heartbeat" and started_at:
        uptime_seconds = now - started_at
    elif online and info["last_startup_epoch"]:
        uptime_seconds = now - info["last_startup_epoch"]
        if uptime_seconds < 0:
            uptime_seconds = None
    last_startup = started_at if (online_source == "heartbeat" and started_at) else info["last_startup_epoch"]

    return {
        "online": online,
        "online_source": online_source,
        "heartbeat_at": heartbeat_at,
        "uptime_seconds": uptime_seconds,
        "last_startup_epoch": last_startup,
        "last_log_epoch": info["last_log_epoch"],
        "errors_24h": info["errors_24h"],
        "warnings_24h": info["warnings_24h"],
        "last_errors": info["last_errors"],
        "log_available": info["log_available"],
        "db_size_bytes": db_size,
        "db_path": str(db_path),
        "active_sessions": active_sessions,
        "active_session_count": len(active_sessions),
        "managed_threads": managed_threads,
        "managed_thread_count": len(managed_threads),
        "open_fight_requests": open_fights,
        "afk_timer_count": afk_timers,
        "total_events": total_events,
        "last_event_epoch": last_event or None,
        "server_time": int(time.time()),
    }


# -------------------------------------------------------------- Overview ----

def overview_stats(range_key: str) -> dict:
    since = range_to_since(range_key)
    with read_connection() as con:
        rows = _events(con, since, "created_at, event_type, actor_user_id, command_name")
        total_players = scalar(con, "SELECT COUNT(DISTINCT user_id) FROM user_karten")
        total_cards = scalar(con, "SELECT COALESCE(SUM(anzahl), 0) FROM user_karten")
        total_dust = scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM user_infinitydust")
        total_units = scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM user_units")

    actors = {r["actor_user_id"] for r in rows if r["actor_user_id"]}
    types = Counter(r["event_type"] for r in rows)
    fights = types.get("fight_result", 0)
    commands = types.get("command_used", 0)

    by_day: Counter[str] = Counter(_day_text(r["created_at"]) for r in rows if r["created_at"])
    timeline = [{"label": day, "value": count} for day, count in sorted(by_day.items())]

    return {
        "range": range_key,
        "events": len(rows),
        "active_players": len(actors),
        "fights": fights,
        "commands": commands,
        "attacks": types.get("attack_used", 0),
        "total_players": total_players,
        "total_cards": total_cards,
        "total_dust": total_dust,
        "total_units": total_units,
        "event_types": [{"label": k, "value": v} for k, v in types.most_common(12)],
        "timeline": timeline,
    }


# --------------------------------------------------- Spieler & Economy ------

def player_stats(range_key: str) -> dict:
    since = range_to_since(range_key)
    with read_connection() as con:
        top_dust = fetch_all(
            con, "SELECT user_id, amount FROM user_infinitydust ORDER BY amount DESC LIMIT 15"
        )
        top_units = fetch_all(
            con, "SELECT user_id, amount FROM user_units ORDER BY amount DESC LIMIT 15"
        )
        top_cards = fetch_all(
            con,
            "SELECT user_id, SUM(anzahl) AS total, COUNT(*) AS unique_cards "
            "FROM user_karten GROUP BY user_id ORDER BY total DESC LIMIT 15",
        )
        card_distribution = fetch_all(
            con,
            "SELECT karten_name, SUM(anzahl) AS total, COUNT(DISTINCT user_id) AS owners "
            "FROM user_karten GROUP BY karten_name ORDER BY total DESC LIMIT 20",
        )
        teams = fetch_all(con, "SELECT user_id, team FROM user_teams")
        daily = fetch_all(
            con,
            "SELECT user_id, last_daily, mission_count, used_invite FROM user_daily "
            "ORDER BY COALESCE(last_daily, 0) DESC LIMIT 200",
        )
        trading = fetch_all(
            con,
            "SELECT code, seller_id, card_name, preis, timestamp FROM tradingpost "
            "ORDER BY timestamp DESC LIMIT 50",
        )
        dust_holders = scalar(con, "SELECT COUNT(*) FROM user_infinitydust WHERE amount > 0")
        unit_holders = scalar(con, "SELECT COUNT(*) FROM user_units WHERE amount > 0")
        total_players = scalar(con, "SELECT COUNT(DISTINCT user_id) FROM user_karten")
        total_dust = scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM user_infinitydust")
        total_units = scalar(con, "SELECT COALESCE(SUM(amount), 0) FROM user_units")
        rows = _events(con, since, "created_at, actor_user_id, event_type")

    active_by_user: Counter[int] = Counter(
        r["actor_user_id"] for r in rows if r["actor_user_id"]
    )
    team_sizes = Counter()
    for row in teams:
        try:
            team = json.loads(row["team"] or "[]")
            team_sizes[len(team) if isinstance(team, list) else 0] += 1
        except (TypeError, ValueError):
            team_sizes[0] += 1

    now = int(time.time())
    daily_24h = sum(1 for row in daily if row["last_daily"] and now - int(row["last_daily"]) < 86400)

    return {
        "range": range_key,
        "total_players": total_players,
        "total_dust": total_dust,
        "total_units": total_units,
        "active_players": len(active_by_user),
        "most_active": [
            {"user_id": str(uid), "events": count} for uid, count in active_by_user.most_common(15)
        ],
        "top_dust": [{"user_id": str(r["user_id"]), "amount": r["amount"]} for r in top_dust],
        "top_units": [{"user_id": str(r["user_id"]), "amount": r["amount"]} for r in top_units],
        "top_cards": [
            {"user_id": str(r["user_id"]), "total": r["total"], "unique": r["unique_cards"]}
            for r in top_cards
        ],
        "card_distribution": card_distribution,
        "team_sizes": [{"label": f"{size} Karten", "value": count} for size, count in sorted(team_sizes.items())],
        "team_count": len(teams),
        "daily_users_24h": daily_24h,
        "daily_rows": len(daily),
        "dust_holders": dust_holders,
        "unit_holders": unit_holders,
        "tradingpost": [
            {**r, "seller_id": str(r["seller_id"])} for r in trading
        ],
    }


# ------------------------------------------------- Battles & Missionen ------

def battle_stats(range_key: str) -> dict:
    since = range_to_since(range_key)
    with read_connection() as con:
        rows = _events(
            con, since,
            "created_at, event_type, session_kind, actor_user_id, hero_name, attack_name, payload_json",
            extra_where="AND event_type IN ('attack_used', 'fight_result', 'hero_selected', 'fight_feedback_bug', 'fight_feedback_no_bug')",
        )
        fight_requests = fetch_all(
            con,
            "SELECT status, COUNT(*) AS count FROM fight_requests "
            + ("WHERE created_at >= ? " if since else "")
            + "GROUP BY status",
            (since,) if since else (),
        )
        mission_requests = fetch_all(
            con,
            "SELECT status, COUNT(*) AS count FROM mission_requests "
            + ("WHERE created_at >= ? " if since else "")
            + "GROUP BY status",
            (since,) if since else (),
        )
        afk = fetch_all(
            con,
            "SELECT kind, battle_id, active_player_id, round_number, last_action_at FROM afk_timers "
            "ORDER BY last_action_at DESC LIMIT 25",
        )
        sessions = fetch_all(
            con,
            "SELECT session_id, kind, status, updated_at FROM active_sessions ORDER BY updated_at DESC LIMIT 25",
        )

    hero_counter: Counter[str] = Counter()
    attack_counter: Counter[str] = Counter()
    kind_counter: Counter[str] = Counter()
    wins: Counter[str] = Counter()
    losses: Counter[str] = Counter()
    feedback = {"bug": 0, "no_bug": 0}
    fight_results = 0

    for row in rows:
        etype = row["event_type"]
        if etype == "attack_used":
            if row["hero_name"]:
                hero_counter[row["hero_name"]] += 1
            if row["hero_name"] and row["attack_name"]:
                attack_counter[f"{row['hero_name']} — {row['attack_name']}"] += 1
            if row["session_kind"]:
                kind_counter[row["session_kind"]] += 1
        elif etype == "fight_result":
            fight_results += 1
            payload = _payload(row["payload_json"])
            winner = str(payload.get("winner_hero") or "").strip()
            loser = str(payload.get("loser_hero") or "").strip()
            if winner:
                wins[winner] += 1
            if loser:
                losses[loser] += 1
        elif etype == "fight_feedback_bug":
            feedback["bug"] += 1
        elif etype == "fight_feedback_no_bug":
            feedback["no_bug"] += 1

    winrates = []
    for hero in sorted(set(wins) | set(losses)):
        total = wins[hero] + losses[hero]
        if total:
            winrates.append(
                {
                    "hero": hero,
                    "wins": wins[hero],
                    "losses": losses[hero],
                    "winrate": round(100 * wins[hero] / total, 1),
                }
            )
    winrates.sort(key=lambda item: (-item["winrate"], -(item["wins"] + item["losses"])))

    return {
        "range": range_key,
        "fight_results": fight_results,
        "attacks": sum(hero_counter.values()),
        "top_heroes": [{"label": k, "value": v} for k, v in hero_counter.most_common(10)],
        "top_attacks": [{"label": k, "value": v} for k, v in attack_counter.most_common(10)],
        "session_kinds": [{"label": k, "value": v} for k, v in kind_counter.most_common()],
        "winrates": winrates[:15],
        "feedback": feedback,
        "fight_requests": fight_requests,
        "mission_requests": mission_requests,
        "afk_timers": afk,
        "active_sessions": sessions,
    }


# ------------------------------------------- Commands & Invites -------------

def analytics_stats(range_key: str) -> dict:
    since = range_to_since(range_key)
    with read_connection() as con:
        rows = _events(con, since, "created_at, event_type, actor_user_id, command_name")
        invite_stats = fetch_all(
            con,
            "SELECT user_id, completed_invites FROM invite_stats ORDER BY completed_invites DESC LIMIT 15",
        )
        invite_pending = fetch_all(
            con,
            "SELECT id, inviter_id, invitee_id, status, created_at FROM invite_pending "
            "WHERE status = 'pending' ORDER BY created_at DESC LIMIT 25",
        )
        invite_history_daily = fetch_all(
            con,
            "SELECT status, COUNT(*) AS count FROM invite_history "
            + ("WHERE created_at >= ? " if since else "")
            + "GROUP BY status",
            (since,) if since else (),
        )
        dust_audit = fetch_all(
            con,
            "SELECT actor_id, target_id, action, mode, requested_amount, applied_amount, created_at "
            "FROM admin_dust_audit ORDER BY created_at DESC LIMIT 30",
        )

    command_counter = Counter(
        r["command_name"] for r in rows if r["event_type"] == "command_used" and r["command_name"]
    )
    type_counter = Counter(r["event_type"] for r in rows)
    by_day: Counter[str] = Counter(_day_text(r["created_at"]) for r in rows if r["created_at"])
    by_hour: Counter[str] = Counter(_hour_text(r["created_at"]) for r in rows if r["created_at"])

    return {
        "range": range_key,
        "top_commands": [{"label": k, "value": v} for k, v in command_counter.most_common(15)],
        "event_types": [{"label": k, "value": v} for k, v in type_counter.most_common(15)],
        "per_day": [{"label": day, "value": count} for day, count in sorted(by_day.items())],
        "per_hour": [
            {"label": f"{h:02d}:00", "value": by_hour.get(f"{h:02d}:00", 0)} for h in range(24)
        ],
        "invite_top": [
            {"user_id": str(r["user_id"]), "completed": r["completed_invites"]} for r in invite_stats
        ],
        "invite_pending": [
            {**r, "inviter_id": str(r["inviter_id"]), "invitee_id": str(r["invitee_id"])}
            for r in invite_pending
        ],
        "invite_by_status": invite_history_daily,
        "dust_audit": [
            {**r, "actor_id": str(r["actor_id"]), "target_id": str(r["target_id"])} for r in dust_audit
        ],
    }


# ----------------------------------------------------------- Userdetail -----

def user_detail(user_id: int) -> dict:
    with read_connection() as con:
        cards = fetch_all(
            con,
            "SELECT karten_name, anzahl FROM user_karten WHERE user_id = ? ORDER BY anzahl DESC",
            (user_id,),
        )
        dust = scalar(con, "SELECT amount FROM user_infinitydust WHERE user_id = ?", (user_id,))
        units = scalar(con, "SELECT amount FROM user_units WHERE user_id = ?", (user_id,))
        team_row = fetch_one(con, "SELECT team FROM user_teams WHERE user_id = ?", (user_id,))
        daily = fetch_one(
            con,
            "SELECT last_daily, mission_count, used_invite FROM user_daily WHERE user_id = ?",
            (user_id,),
        )
        buffs = fetch_all(
            con,
            "SELECT card_name, buff_type, attack_number, buff_amount FROM user_card_buffs WHERE user_id = ?",
            (user_id,),
        )
    team = []
    if team_row and team_row.get("team"):
        try:
            team = json.loads(team_row["team"])
        except (TypeError, ValueError):
            team = []
    return {
        "user_id": str(user_id),
        "cards": cards,
        "dust": dust,
        "units": units,
        "team": team,
        "daily": daily,
        "buffs": buffs,
    }


# ---------------------------------------------------- Ultra-Detail ----------

def user_full(user_id: int) -> dict:
    """Tiefenanalyse eines Spielers: alles, was die DB über ihn weiß."""
    base = user_detail(user_id)
    with read_connection() as con:
        events = fetch_all(
            con,
            "SELECT created_at, event_type, guild_id, session_kind, command_name, "
            "hero_name, attack_name, actor_user_id, target_user_id, payload_json "
            "FROM analytics_events WHERE actor_user_id = ? OR target_user_id = ? "
            "ORDER BY created_at DESC LIMIT 2000",
            (user_id, user_id),
        )
        events_total = scalar(
            con,
            "SELECT COUNT(*) FROM analytics_events WHERE actor_user_id = ? OR target_user_id = ?",
            (user_id, user_id),
        )
        first_seen = scalar(
            con,
            "SELECT MIN(created_at) FROM analytics_events WHERE actor_user_id = ? OR target_user_id = ?",
            (user_id, user_id), default=None,
        )
        missions = fetch_all(
            con,
            "SELECT id, guild_id, status, created_at, mission_data FROM mission_requests "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
            (user_id,),
        )
        fight_reqs = fetch_all(
            con,
            "SELECT id, guild_id, challenger_id, challenged_id, challenger_card, status, created_at "
            "FROM fight_requests WHERE challenger_id = ? OR challenged_id = ? "
            "ORDER BY created_at DESC LIMIT 100",
            (user_id, user_id),
        )
        invites_completed = scalar(
            con, "SELECT completed_invites FROM invite_stats WHERE user_id = ?", (user_id,)
        )
        invite_history = fetch_all(
            con,
            "SELECT inviter_id, invitee_id, status, created_at FROM invite_history "
            "WHERE inviter_id = ? OR invitee_id = ? ORDER BY created_at DESC LIMIT 50",
            (user_id, user_id),
        )
        trading = fetch_all(
            con,
            "SELECT code, card_name, preis, timestamp FROM tradingpost "
            "WHERE seller_id = ? ORDER BY timestamp DESC LIMIT 50",
            (user_id,),
        )
        dust_audit = fetch_all(
            con,
            "SELECT actor_id, target_id, action, mode, applied_amount, created_at "
            "FROM admin_dust_audit WHERE actor_id = ? OR target_id = ? "
            "ORDER BY created_at DESC LIMIT 50",
            (user_id, user_id),
        )

    commands: Counter[str] = Counter()
    heroes: Counter[str] = Counter()
    attacks: Counter[str] = Counter()
    types: Counter[str] = Counter()
    by_day: Counter[str] = Counter()
    fights: list[dict] = []
    wins = losses = 0
    guilds_seen: set[int] = set()

    for row in events:  # DESC — für Timeline unten wieder sortieren
        types[row["event_type"]] += 1
        if row["guild_id"]:
            guilds_seen.add(int(row["guild_id"]))
        if row["created_at"]:
            by_day[_day_text(row["created_at"])] += 1
        etype = row["event_type"]
        is_actor = row["actor_user_id"] == user_id
        if etype == "command_used" and is_actor and row["command_name"]:
            commands[row["command_name"]] += 1
        elif etype == "attack_used" and is_actor:
            if row["hero_name"]:
                heroes[row["hero_name"]] += 1
            if row["hero_name"] and row["attack_name"]:
                attacks[f"{row['hero_name']} — {row['attack_name']}"] += 1
        elif etype == "fight_result":
            payload = _payload(row["payload_json"])
            won = int(payload.get("winner_id") or 0) == user_id or (is_actor and "winner_id" not in payload)
            if won:
                wins += 1
            else:
                losses += 1
            own_hero = payload.get("winner_hero") if won else payload.get("loser_hero")
            opp_hero = payload.get("loser_hero") if won else payload.get("winner_hero")
            opp_id = payload.get("loser_id") if won else payload.get("winner_id")
            fights.append({
                "created_at": row["created_at"],
                "won": won,
                "own_hero": own_hero or "?",
                "opp_hero": opp_hero or "?",
                "opponent_id": str(opp_id or 0),
                "rounds": payload.get("rounds"),
                "kind": row["session_kind"] or "?",
            })

    total_fights = wins + losses
    mission_rows = []
    for m in missions:
        name = None
        data = _payload(m.get("mission_data"))
        if data:
            name = data.get("name") or data.get("mission") or data.get("title")
        mission_rows.append({
            "id": m["id"],
            "guild_id": str(m["guild_id"] or 0),
            "status": m["status"],
            "created_at": m["created_at"],
            "name": name,
        })

    # Timeline aufsteigend, nur die letzten 60 Tage mit Daten
    timeline = [{"label": day, "value": count} for day, count in sorted(by_day.items())][-60:]

    return {
        **base,
        "events_total": events_total,
        "events_fetched": len(events),
        "first_seen": first_seen,
        "last_seen": events[0]["created_at"] if events else None,
        "timeline": timeline,
        "top_commands": [{"label": k, "value": v} for k, v in commands.most_common(15)],
        "top_heroes": [{"label": k, "value": v} for k, v in heroes.most_common(10)],
        "top_attacks": [{"label": k, "value": v} for k, v in attacks.most_common(10)],
        "event_types": [{"label": k, "value": v} for k, v in types.most_common(15)],
        "fights": fights[:100],
        "wins": wins,
        "losses": losses,
        "winrate": round(100 * wins / total_fights, 1) if total_fights else None,
        "missions": mission_rows,
        "mission_count": len(missions),
        "fight_requests": [
            {**r, "challenger_id": str(r["challenger_id"] or 0), "challenged_id": str(r["challenged_id"] or 0)}
            for r in fight_reqs
        ],
        "invites_completed": invites_completed or 0,
        "invite_history": [
            {**r, "inviter_id": str(r["inviter_id"]), "invitee_id": str(r["invitee_id"])}
            for r in invite_history
        ],
        "trading": trading,
        "dust_audit": [
            {**r, "actor_id": str(r["actor_id"]), "target_id": str(r["target_id"])} for r in dust_audit
        ],
        "guilds_seen": [str(g) for g in sorted(guilds_seen)],
        "recent_events": [
            {
                "created_at": r["created_at"],
                "event_type": r["event_type"],
                "command_name": r["command_name"],
                "hero_name": r["hero_name"],
                "attack_name": r["attack_name"],
                "session_kind": r["session_kind"],
                "guild_id": str(r["guild_id"] or 0),
            }
            for r in events[:150]
        ],
    }
