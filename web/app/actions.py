"""Schreibende Aktionen auf der Bot-Datenbank.

Alles hier verbucht **exakt so wie der Bot selbst** (siehe services/user_data.py):
gleiche Tabellen, gleiche Upsert-Logik, gleiche Protokolleinträge. Sonst würden
Website und Bot mit der Zeit auseinanderlaufen.

Discord-Aktionen stehen NICHT hier — die laufen über die Auftragstabelle
(jobs.py) oder direkt über discordapi.py.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import cards, database, schema

MAX_AMOUNT = 1_000_000     # Schutz vor Vertippern ("10000000 statt 100")


class ActionError(Exception):
    """Aktion nicht ausführbar — Text ist für dich gedacht, nicht für Entwickler."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_amount(amount: int) -> int:
    try:
        value = int(amount)
    except (TypeError, ValueError):
        raise ActionError("Die Menge muss eine ganze Zahl sein.") from None
    if value <= 0:
        raise ActionError("Die Menge muss größer als 0 sein.")
    if value > MAX_AMOUNT:
        raise ActionError(f"Das sind sehr viele auf einmal (Grenze: {MAX_AMOUNT:,}). "
                          "Bitte in kleineren Schritten.".replace(",", "."))
    return value


def _check_user(user_id) -> int:
    try:
        value = int(str(user_id).strip())
    except (TypeError, ValueError):
        raise ActionError("Das ist keine gültige Discord-Nutzer-ID.") from None
    if value <= 0:
        raise ActionError("Das ist keine gültige Discord-Nutzer-ID.")
    return value


# --------------------------------------------------------------------------
# Währungen
# --------------------------------------------------------------------------
CURRENCIES = {
    "infinitydust": ("user_infinitydust", "Infinitydust"),
    "units": ("user_units", "Units"),
}


def adjust_currency(*, currency: str, user_id, amount: int, remove: bool,
                    actor_id: int = 0, guild_id: int = 0) -> dict:
    if currency not in CURRENCIES:
        raise ActionError("Unbekannte Währung.")
    table, label = CURRENCIES[currency]
    uid = _check_user(user_id)
    wanted = _check_amount(amount)

    with database.write_connection() as con:
        schema.init_schema(con)
        row = con.execute(f"SELECT amount FROM {table} WHERE user_id = ?", (uid,)).fetchone()
        before = (row["amount"] if row and row["amount"] else 0) or 0

        if remove:
            applied = min(wanted, before)          # nie unter null buchen
            after = before - applied
            if row:
                con.execute(f"UPDATE {table} SET amount = ? WHERE user_id = ?", (after, uid))
            else:
                con.execute(f"INSERT INTO {table} (user_id, amount) VALUES (?, 0)", (uid,))
        else:
            applied = wanted
            after = before + applied
            # Gleicher Upsert wie im Bot: verträgt gleichzeitige Buchungen.
            con.execute(
                f"INSERT INTO {table} (user_id, amount) VALUES (?, ?) "
                f"ON CONFLICT(user_id) DO UPDATE SET amount = amount + excluded.amount",
                (uid, applied),
            )

        # Für Infinitydust führt der Bot ein eigenes Protokoll — dort mitschreiben,
        # damit /dust-Auswertungen die Website-Buchungen ebenfalls sehen.
        if currency == "infinitydust":
            con.execute(
                "INSERT INTO admin_dust_audit (actor_id, target_id, guild_id, channel_id, "
                "action, mode, requested_amount, applied_amount, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'web', ?, ?, ?)",
                (int(actor_id or 0), uid, int(guild_id or 0), 0,
                 "remove" if remove else "give", wanted, applied, int(time.time())),
            )

    return {"waehrung": label, "user_id": uid, "vorher": before, "nachher": after,
            "gewuenscht": wanted, "gebucht": applied,
            "hinweis": ("Es war weniger vorhanden als angegeben — es wurde auf 0 gesetzt."
                        if remove and applied < wanted else None)}


# --------------------------------------------------------------------------
# Karten
# --------------------------------------------------------------------------
def adjust_card(*, user_id, card_name: str, amount: int, remove: bool) -> dict:
    uid = _check_user(user_id)
    wanted = _check_amount(amount)
    resolved = cards.resolve(card_name)
    if not resolved:
        if not cards.available():
            raise ActionError("Die Kartenliste des Bots lässt sich gerade nicht laden — "
                              "aus Sicherheitsgründen wird nichts verbucht.")
        raise ActionError(f"Die Karte „{card_name}“ gibt es nicht. Bitte aus der Liste wählen.")

    with database.write_connection() as con:
        row = con.execute(
            "SELECT anzahl FROM user_karten WHERE user_id = ? AND karten_name = ?",
            (uid, resolved)).fetchone()
        before = (row["anzahl"] if row and row["anzahl"] else 0) or 0

        if remove:
            applied = min(wanted, before)
            after = before - applied
            if after <= 0:
                con.execute("DELETE FROM user_karten WHERE user_id = ? AND karten_name = ?",
                            (uid, resolved))
                after = 0
            else:
                con.execute(
                    "UPDATE user_karten SET anzahl = ? WHERE user_id = ? AND karten_name = ?",
                    (after, uid, resolved))
        else:
            applied = wanted
            after = before + applied
            con.execute(
                "INSERT INTO user_karten (user_id, karten_name, anzahl) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, karten_name) DO UPDATE SET anzahl = anzahl + excluded.anzahl",
                (uid, resolved, applied))

    return {"user_id": uid, "karte": resolved, "vorher": before, "nachher": after,
            "gewuenscht": wanted, "gebucht": applied,
            "hinweis": ("Es waren weniger vorhanden als angegeben." if remove and applied < wanted
                        else None)}


def adjust_rarity_group(*, user_id, rarity: str, remove: bool) -> dict:
    gruppen = cards.rarities()
    key = str(rarity or "").strip().lower()
    if key not in gruppen:
        raise ActionError(f"Die Seltenheit „{rarity}“ gibt es nicht. "
                          f"Möglich: {', '.join(sorted(gruppen))}")
    ergebnisse = [adjust_card(user_id=user_id, card_name=name, amount=1, remove=remove)
                  for name in gruppen[key]]
    return {"seltenheit": key, "karten": len(ergebnisse), "einzeln": ergebnisse}


# --------------------------------------------------------------------------
# Schalter pro Server
# --------------------------------------------------------------------------
CORE_FLAGS = {
    "maintenance_mode": "Wartungsmodus",
    "beta_enabled": "Beta-Phase",
    "alpha_enabled": "Alpha-Phase",
}


def set_core_flag(*, guild_id, flag: str, enabled: bool) -> dict:
    if flag not in CORE_FLAGS:
        raise ActionError("Unbekannter Schalter.")
    gid = _check_user(guild_id)       # gleiche Prüfung: muss eine Discord-ID sein
    with database.write_connection() as con:
        con.execute(
            f"INSERT INTO guild_config (guild_id, {flag}) VALUES (?, ?) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {flag} = excluded.{flag}",
            (gid, 1 if enabled else 0))
    return {"guild_id": gid, "schalter": flag, "bezeichnung": CORE_FLAGS[flag],
            "aktiv": bool(enabled)}


def set_feature_toggle(*, guild_id, key: str, enabled: bool) -> dict:
    clean = str(key or "").strip()
    if not clean or len(clean) > 64:
        raise ActionError("Der Name des Schalters fehlt oder ist zu lang.")
    gid = _check_user(guild_id)
    with database.write_connection() as con:
        schema.init_schema(con)
        con.execute(
            "INSERT INTO guild_feature_toggles (guild_id, feature_key, enabled, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, feature_key) DO UPDATE SET "
            "enabled = excluded.enabled, updated_at = excluded.updated_at",
            (str(gid), clean, 1 if enabled else 0, _now_iso()))
    return {"guild_id": str(gid), "schalter": clean, "aktiv": bool(enabled)}


def set_allowed_channel(*, guild_id, channel_id, allow: bool) -> dict:
    gid = _check_user(guild_id)
    cid = _check_user(channel_id)
    with database.write_connection() as con:
        if allow:
            con.execute(
                "INSERT OR IGNORE INTO guild_allowed_channels (guild_id, channel_id) VALUES (?, ?)",
                (gid, cid))
        else:
            con.execute(
                "DELETE FROM guild_allowed_channels WHERE guild_id = ? AND channel_id = ?",
                (gid, cid))
    return {"guild_id": gid, "channel_id": cid, "freigegeben": bool(allow)}


# --------------------------------------------------------------------------
# Spielerdaten löschen (wie /entwicklerpanel → delete_user im Bot)
# --------------------------------------------------------------------------
_USER_TABLES = (
    ("user_karten", "user_id"), ("user_teams", "user_id"), ("user_daily", "user_id"),
    ("user_infinitydust", "user_id"), ("user_units", "user_id"),
    ("user_card_buffs", "user_id"), ("user_seen_channels", "user_id"),
    ("tradingpost", "seller_id"),
)


def delete_player(user_id) -> dict:
    uid = _check_user(user_id)
    geloescht = {}
    with database.write_connection() as con:
        for table, column in _USER_TABLES:
            try:
                cursor = con.execute(f"DELETE FROM {table} WHERE {column} = ?", (uid,))
                geloescht[table] = cursor.rowcount
            except Exception:                                       # noqa: BLE001
                geloescht[table] = 0
    return {"user_id": uid, "geloescht": geloescht,
            "gesamt": sum(v for v in geloescht.values() if v > 0)}
