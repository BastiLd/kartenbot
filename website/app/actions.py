"""Admin-Schreibaktionen des Dashboards.

Regeln:
- Nur parametrisierte SQL-Statements.
- Jede Aktion landet im Audit-Log ``dashboard_audit`` (gleiches Muster wie
  ``admin_dust_audit``); Dust-Aktionen werden zusätzlich in
  ``admin_dust_audit`` gespiegelt, damit die bestehende Bot-Auswertung sie
  sieht.
- Buchungslogik ist bewusst identisch zu services/user_data.py
  (atomare Upserts, Abbuchen nur bei ausreichendem Guthaben).
"""
from __future__ import annotations

import time

from .cards import is_known_card, normalize_card_name
from .database import DashboardDBError, fetch_all, write_connection

ACTOR = "dashboard"

GUILD_FLAGS = {"maintenance_mode", "beta_enabled", "alpha_enabled"}

MAX_AMOUNT = 1_000_000  # Schutz gegen Tippfehler-Beträge


def _ensure_audit_table(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS dashboard_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            action TEXT NOT NULL,
            target TEXT,
            amount INTEGER,
            detail TEXT
        )
        """
    )


def _audit(con, action: str, target: str, amount: int | None = None, detail: str = "") -> None:
    _ensure_audit_table(con)
    con.execute(
        "INSERT INTO dashboard_audit (created_at, action, target, amount, detail) VALUES (?, ?, ?, ?, ?)",
        (int(time.time()), action, str(target), amount, detail),
    )


def _check_amount(amount: int) -> int:
    amount = int(amount)
    if amount <= 0:
        raise DashboardDBError("Betrag muss größer als 0 sein.")
    if amount > MAX_AMOUNT:
        raise DashboardDBError(f"Betrag zu groß (max. {MAX_AMOUNT}).")
    return amount


def _mirror_dust_audit(con, target_id: int, action: str, requested: int, applied: int) -> None:
    con.execute(
        """
        INSERT INTO admin_dust_audit
            (actor_id, target_id, guild_id, channel_id, action, mode, requested_amount, applied_amount, created_at)
        VALUES (0, ?, 0, 0, ?, 'dashboard', ?, ?, ?)
        """,
        (int(target_id), action, requested, applied, int(time.time())),
    )


# ------------------------------------------------------------- Currency -----

def adjust_currency(kind: str, user_id: int, amount: int, action: str) -> dict:
    """kind: dust|units, action: give|remove."""
    table = {"dust": "user_infinitydust", "units": "user_units"}.get(kind)
    if not table:
        raise DashboardDBError("Unbekannte Währung.")
    if action not in {"give", "remove"}:
        raise DashboardDBError("Unbekannte Aktion.")
    amount = _check_amount(amount)
    user_id = int(user_id)

    with write_connection() as con:
        if action == "give":
            con.execute(
                f"INSERT INTO {table} (user_id, amount) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET amount = amount + excluded.amount",
                (user_id, amount),
            )
            applied = amount
        else:
            row = con.execute(f"SELECT amount FROM {table} WHERE user_id = ?", (user_id,)).fetchone()
            current = int(row["amount"] or 0) if row else 0
            applied = min(current, amount)
            if applied:
                con.execute(
                    f"UPDATE {table} SET amount = amount - ? WHERE user_id = ? AND amount >= ?",
                    (applied, user_id, applied),
                )
        _audit(con, f"{kind}_{action}", f"user:{user_id}", applied, f"angefragt={amount}")
        if kind == "dust":
            _mirror_dust_audit(con, user_id, action, amount, applied)
        new_row = con.execute(f"SELECT amount FROM {table} WHERE user_id = ?", (user_id,)).fetchone()
        new_amount = int(new_row["amount"] or 0) if new_row else 0

    return {"user_id": str(user_id), "applied": applied, "new_amount": new_amount}


# ---------------------------------------------------------------- Karten ----

def adjust_card(user_id: int, card_name: str, amount: int, action: str) -> dict:
    if action not in {"give", "remove"}:
        raise DashboardDBError("Unbekannte Aktion.")
    amount = _check_amount(amount)
    user_id = int(user_id)
    name = normalize_card_name(str(card_name or "").strip())
    if not name:
        raise DashboardDBError("Kartenname fehlt.")
    if not is_known_card(name):
        raise DashboardDBError(f"Unbekannte Karte: {card_name}")

    with write_connection() as con:
        if action == "give":
            con.execute(
                "INSERT INTO user_karten (user_id, karten_name, anzahl) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, karten_name) DO UPDATE SET anzahl = anzahl + excluded.anzahl",
                (user_id, name, amount),
            )
            applied = amount
        else:
            row = con.execute(
                "SELECT anzahl FROM user_karten WHERE user_id = ? AND karten_name = ?",
                (user_id, name),
            ).fetchone()
            current = int(row["anzahl"] or 0) if row else 0
            applied = min(current, amount)
            if applied >= current and current > 0:
                con.execute(
                    "DELETE FROM user_karten WHERE user_id = ? AND karten_name = ?",
                    (user_id, name),
                )
            elif applied:
                con.execute(
                    "UPDATE user_karten SET anzahl = anzahl - ? WHERE user_id = ? AND karten_name = ?",
                    (applied, user_id, name),
                )
        _audit(con, f"card_{action}", f"user:{user_id}", applied, name)

    return {"user_id": str(user_id), "card": name, "applied": applied}


# ------------------------------------------------------------ Tradingpost ---

def delete_trading_entry(code: str) -> dict:
    code = str(code or "").strip()
    if not code:
        raise DashboardDBError("Code fehlt.")
    with write_connection() as con:
        row = con.execute(
            "SELECT code, seller_id, card_name, preis FROM tradingpost WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            raise DashboardDBError(f"Trading-Post-Eintrag {code} nicht gefunden.")
        con.execute("DELETE FROM tradingpost WHERE code = ?", (code,))
        _audit(con, "tradingpost_delete", f"code:{code}", None,
               f"seller={row['seller_id']} card={row['card_name']} preis={row['preis']}")
    return {"deleted": code}


# ------------------------------------------------------------ Guild-Flags ---

def set_guild_flag(guild_id: int, flag: str, enabled: bool) -> dict:
    if flag not in GUILD_FLAGS:
        raise DashboardDBError(f"Unbekanntes Flag: {flag}")
    guild_id = int(guild_id)
    if guild_id <= 0:
        raise DashboardDBError("Ungültige Guild-ID.")
    value = 1 if enabled else 0
    with write_connection() as con:
        # Whitelist-geprüfter Spaltenname, Werte parametrisiert.
        con.execute(
            f"INSERT INTO guild_config (guild_id, {flag}) VALUES (?, ?) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {flag} = excluded.{flag}",
            (guild_id, value),
        )
        _audit(con, "guild_flag", f"guild:{guild_id}", value, flag)
    return {"guild_id": str(guild_id), "flag": flag, "enabled": bool(enabled)}


def list_guild_configs() -> list[dict]:
    with write_connection() as con:  # write conn, damit die Tabelle sicher lesbar bleibt
        rows = fetch_all(
            con,
            "SELECT guild_id, maintenance_mode, beta_enabled, alpha_enabled FROM guild_config ORDER BY guild_id",
        )
    return [{**r, "guild_id": str(r["guild_id"])} for r in rows]


# ---------------------------------------------------------------- Cleanup ---

STALE_SECONDS = 24 * 3600


def cleanup(what: str) -> dict:
    """Räumt beendete/veraltete Einträge auf.

    sessions: active_sessions mit Status != 'active' ODER älter als 24h
    threads:  managed_threads mit Status in (closed/archived/done) ODER älter als 24h
    afk:      afk_timers, deren letzte Aktion älter als 24h ist
    """
    cutoff = int(time.time()) - STALE_SECONDS
    with write_connection() as con:
        if what == "sessions":
            cur = con.execute(
                "DELETE FROM active_sessions WHERE COALESCE(status, '') NOT IN ('active', 'running', 'pending') "
                "OR COALESCE(updated_at, 0) < ?",
                (cutoff,),
            )
        elif what == "threads":
            cur = con.execute(
                "DELETE FROM managed_threads WHERE COALESCE(status, '') IN ('closed', 'archived', 'done', 'finished') "
                "OR COALESCE(updated_at, 0) < ?",
                (cutoff,),
            )
        elif what == "afk":
            cur = con.execute("DELETE FROM afk_timers WHERE last_action_at < ?", (cutoff,))
        else:
            raise DashboardDBError(f"Unbekanntes Cleanup-Ziel: {what}")
        removed = cur.rowcount
        _audit(con, f"cleanup_{what}", "db", removed, f"cutoff={cutoff}")
    return {"removed": removed}


# ------------------------------------------------------------------ Audit ---

def audit_log(limit: int = 100) -> list[dict]:
    with write_connection() as con:
        _ensure_audit_table(con)
        rows = fetch_all(
            con,
            "SELECT id, created_at, action, target, amount, detail FROM dashboard_audit "
            "ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        )
    return rows
