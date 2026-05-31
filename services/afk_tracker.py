"""AFK-Markierungssystem für Challenges und Kämpfe (Req. 13).

Kernstück ist die **pure function** :func:`evaluate_pings`, die für einen
gegebenen :class:`AfkState` und einen Zeitpunkt ``now`` deterministisch die
fälligen Pings zurückgibt. Dadurch sind Idempotenz- und Restart-Eigenschaften
testbar (siehe ``tests/test_afk_tracker_invariants.py``).

Die Persistenz läuft über die SQLite-Tabelle ``afk_timers`` (siehe
``services/db.py``). ``pings_sent_mask`` ist ein Bitfeld pro Runde:

    bit0 = erster Schwellwert  (2h aktiv / 4h aktiv-bzw-acceptor)
    bit1 = 3h beide
    bit2 = 4h aktiv
    bit3 = 6h beide

Ping-Schwellen (Req. 13.1–13.5):
    * Offene Challenge: 4h -> Acceptor (einmalig).
    * Kampf Runde 1+2: 4h -> aktiver Spieler (einmalig).
    * Kampf ab Runde 3: 2h aktiv, 3h beide, 4h aktiv, 6h beide (max 4 / Runde).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

HOUR = 3600

# (threshold_seconds, scope, bit)
PING_THRESHOLDS_CHALLENGE: list[tuple[int, str, int]] = [(4 * HOUR, "acceptor", 0)]
PING_THRESHOLDS_R1_R2: list[tuple[int, str, int]] = [(4 * HOUR, "active", 0)]
PING_THRESHOLDS_R3PLUS: list[tuple[int, str, int]] = [
    (2 * HOUR, "active", 0),
    (3 * HOUR, "both", 1),
    (4 * HOUR, "active", 2),
    (6 * HOUR, "both", 3),
]


@dataclass
class AfkState:
    kind: Literal["challenge", "battle", "mission"]
    battle_id: str
    thread_id: int
    challenger_id: int
    acceptor_id: int
    active_player_id: int | None
    round_number: int  # 0 = Challenge offen, >=1 = Kampfrunde
    round_started_at: int
    last_action_at: int
    pings_sent_mask: int

    def other_player(self, actor_id: int) -> int:
        """Liefert den jeweils anderen Teilnehmer."""
        return self.acceptor_id if actor_id == self.challenger_id else self.challenger_id


@dataclass(frozen=True)
class Ping:
    bit: int
    threshold_seconds: int
    scope: str            # "active" | "both" | "acceptor"
    recipients: tuple[int, ...]


def _thresholds_for(state: AfkState) -> list[tuple[int, str, int]]:
    if state.kind == "challenge":
        return PING_THRESHOLDS_CHALLENGE
    if state.round_number >= 3:
        return PING_THRESHOLDS_R3PLUS
    return PING_THRESHOLDS_R1_R2


def _recipients(state: AfkState, scope: str) -> tuple[int, ...]:
    if scope == "both":
        return (state.challenger_id, state.acceptor_id)
    if scope == "acceptor":
        return (state.acceptor_id,)
    # "active": aktiver Spieler, fällt auf Acceptor zurück, falls (noch) nicht gesetzt.
    target = state.active_player_id if state.active_player_id is not None else state.acceptor_id
    return (target,)


def evaluate_pings(state: AfkState, now: int) -> list[Ping]:
    """Pure function: gibt alle Pings zurück, deren Schwelle bereits erreicht ist.

    Die Inaktivität wird ab Rundenbeginn (``round_started_at``) gemessen; für
    offene Challenges ab der letzten Aktivität (``last_action_at``). Das Ergebnis
    hängt ausschließlich von ``(state, now)`` ab – mehrfache Aufrufe liefern
    dieselbe Menge (Idempotenz, Req.-Property 1).
    """
    reference = state.last_action_at if state.kind == "challenge" else state.round_started_at
    elapsed = now - reference
    pings: list[Ping] = []
    for threshold, scope, bit in _thresholds_for(state):
        if elapsed >= threshold:
            pings.append(
                Ping(
                    bit=bit,
                    threshold_seconds=threshold,
                    scope=scope,
                    recipients=_recipients(state, scope),
                )
            )
    return pings


def pending_pings(state: AfkState, now: int) -> list[Ping]:
    """Fällige Pings, deren Bit noch nicht gesetzt ist (für :func:`tick`)."""
    return [p for p in evaluate_pings(state, now) if not (state.pings_sent_mask & (1 << p.bit))]


def on_action(state: AfkState, actor_id: int, now: int) -> AfkState:
    """Reset bei neuem Zug: neue Runde, Marker konsumiert (Req. 13.6, Property 3)."""
    state.round_number += 1
    state.round_started_at = now
    state.last_action_at = now
    state.pings_sent_mask = 0
    state.active_player_id = state.other_player(actor_id)
    return state


# --------------------------------------------------------------------------- #
# Persistenz (SQLite ``afk_timers``)
# --------------------------------------------------------------------------- #

def state_to_row(state: AfkState, created_at: int | None = None) -> dict:
    return {
        "kind": state.kind,
        "battle_id": state.battle_id,
        "thread_id": state.thread_id,
        "challenger_id": state.challenger_id,
        "acceptor_id": state.acceptor_id,
        "active_player_id": state.active_player_id,
        "round_number": state.round_number,
        "round_started_at": state.round_started_at,
        "last_action_at": state.last_action_at,
        "pings_sent_mask": state.pings_sent_mask,
        "created_at": created_at if created_at is not None else int(time.time()),
    }


def state_from_row(row) -> AfkState:
    return AfkState(
        kind=row["kind"],
        battle_id=row["battle_id"],
        thread_id=int(row["thread_id"] or 0),
        challenger_id=int(row["challenger_id"]),
        acceptor_id=int(row["acceptor_id"]),
        active_player_id=(int(row["active_player_id"]) if row["active_player_id"] is not None else None),
        round_number=int(row["round_number"] or 0),
        round_started_at=int(row["round_started_at"]),
        last_action_at=int(row["last_action_at"]),
        pings_sent_mask=int(row["pings_sent_mask"] or 0),
    )


async def persist(state: AfkState) -> None:
    """Speichert (UPSERT) den Zustand anhand der eindeutigen ``battle_id``."""
    from services.db import db_context

    row = state_to_row(state)
    async with db_context() as db:
        await db.execute(
            """
            INSERT INTO afk_timers
                (kind, battle_id, thread_id, challenger_id, acceptor_id, active_player_id,
                 round_number, round_started_at, last_action_at, pings_sent_mask, created_at)
            VALUES (:kind, :battle_id, :thread_id, :challenger_id, :acceptor_id, :active_player_id,
                    :round_number, :round_started_at, :last_action_at, :pings_sent_mask, :created_at)
            ON CONFLICT(battle_id) DO UPDATE SET
                kind=excluded.kind,
                thread_id=excluded.thread_id,
                challenger_id=excluded.challenger_id,
                acceptor_id=excluded.acceptor_id,
                active_player_id=excluded.active_player_id,
                round_number=excluded.round_number,
                round_started_at=excluded.round_started_at,
                last_action_at=excluded.last_action_at,
                pings_sent_mask=excluded.pings_sent_mask
            """,
            row,
        )
        await db.commit()


async def delete_state(battle_id: str) -> None:
    """Entfernt den Timer (Cancel / Battle-Ende / Challenge-Annahme, Req. 12.5)."""
    from services.db import db_context

    async with db_context() as db:
        await db.execute("DELETE FROM afk_timers WHERE battle_id = ?", (str(battle_id),))
        await db.commit()


async def restore_all_states() -> list[AfkState]:
    """Lädt alle persistierten Zustände beim Bot-Start (Req. 13.9)."""
    from services.db import db_context

    async with db_context() as db:
        cursor = await db.execute("SELECT * FROM afk_timers")
        rows = await cursor.fetchall()
    return [state_from_row(row) for row in rows]


async def create_challenge_state(battle_id: str, thread_id: int, challenger_id: int,
                                 acceptor_id: int, now: int | None = None) -> AfkState:
    now = int(now if now is not None else time.time())
    state = AfkState(
        kind="challenge",
        battle_id=str(battle_id),
        thread_id=int(thread_id or 0),
        challenger_id=int(challenger_id),
        acceptor_id=int(acceptor_id),
        active_player_id=None,
        round_number=0,
        round_started_at=now,
        last_action_at=now,
        pings_sent_mask=0,
    )
    await persist(state)
    return state


async def create_battle_state(battle_id: str, thread_id: int, challenger_id: int,
                              acceptor_id: int, active_player_id: int,
                              now: int | None = None) -> AfkState:
    now = int(now if now is not None else time.time())
    state = AfkState(
        kind="battle",
        battle_id=str(battle_id),
        thread_id=int(thread_id or 0),
        challenger_id=int(challenger_id),
        acceptor_id=int(acceptor_id),
        active_player_id=int(active_player_id),
        round_number=1,
        round_started_at=now,
        last_action_at=now,
        pings_sent_mask=0,
    )
    await persist(state)
    return state


async def create_mission_state(battle_id: str, thread_id: int, user_id: int,
                               now: int | None = None) -> AfkState:
    """AFK-Timer für eine Solo-Mission (gegen den Bot) im Thread (Req. 13, erweitert).

    Es gibt nur einen Teilnehmer (den Spieler), daher sind challenger/acceptor/aktiv
    alle gleich. Gepingt wird nach Inaktivität ab Rundenbeginn (Standard: 4h in
    Runde 1/2). Bei Aktivität wird der Timer neu angelegt (UPSERT, ``round_started_at``
    zurückgesetzt)."""
    now = int(now if now is not None else time.time())
    state = AfkState(
        kind="mission",
        battle_id=str(battle_id),
        thread_id=int(thread_id or 0),
        challenger_id=int(user_id),
        acceptor_id=int(user_id),
        active_player_id=int(user_id),
        round_number=1,
        round_started_at=now,
        last_action_at=now,
        pings_sent_mask=0,
    )
    await persist(state)
    return state


async def _send_ping(bot, state: AfkState, ping: Ping) -> None:
    channel = None
    try:
        channel = bot.get_channel(state.thread_id) if state.thread_id else None
        if channel is None and state.thread_id:
            channel = await bot.fetch_channel(state.thread_id)
    except Exception:
        logger.exception("AFK-Ping: Kanal %s nicht erreichbar", state.thread_id)
        return
    if channel is None:
        return
    # Doppelte Empfänger zusammenfassen (z. B. Solo-Mission: challenger == acceptor).
    unique_recipients = list(dict.fromkeys(ping.recipients))
    mentions = " ".join(f"<@{uid}>" for uid in unique_recipients)
    try:
        await channel.send(f"{mentions} ⏰ Erinnerung: Du bist am Zug.")
    except Exception:
        logger.exception("AFK-Ping konnte nicht gesendet werden (battle=%s)", state.battle_id)


async def tick(bot, state: AfkState, now: int | None = None) -> AfkState:
    """Sendet fällige Pings, setzt das jeweilige Bit und persistiert (Req. 13.1–13.5)."""
    now = int(now if now is not None else time.time())
    changed = False
    for ping in evaluate_pings(state, now):
        bit_value = 1 << ping.bit
        if state.pings_sent_mask & bit_value:
            continue
        await _send_ping(bot, state, ping)
        state.pings_sent_mask |= bit_value
        changed = True
    if changed:
        await persist(state)
    return state
