"""Test-Harness zum Instanziieren und Antreiben der Kampf-Views aus bot.py.

Hintergrund (Audit D4): `BattleView` (PvP) und `MissionBattleView` (PvE) sind große,
bisher ungetestete View-Klassen. Vor jeder Vereinheitlichung brauchen sie ein
Sicherheitsnetz. Beide lassen sich offline konstruieren – ihr `__init__` ist reiner
State-Aufbau ohne DB/Netzwerk. Dieses Harness baut echte Views mit echten Karten und
neutralisiert die wenigen externen I/O-Punkte (Discord-Sends, Session-Persistenz,
Analytics), sodass ein kompletter Zug ohne laufenden Bot/DB durchläuft.

Wichtig: Tests, die einen Zug treiben, müssen `await close_db()` im finally aufrufen
(siehe `run_view_coro`), sonst hält die aiosqlite-Hintergrundverbindung den Prozess offen.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import bot
from db import close_db
from karten import karten
from services.card_variants import build_runtime_card


def make_cards(name1: str = "Black Widow", name2: str = "Wolverine"):
    return build_runtime_card(name1, cards=karten), build_runtime_card(name2, cards=karten)


def _neutralize_io(view):
    """Externe I/O-Grenzen abklemmen – Kampf-Logik bleibt unangetastet."""
    view.persist_session = AsyncMock()
    view.battle_log_message = None  # _safe_edit_battle_log wird damit zum No-Op
    if hasattr(view, "_log_mission_attack_event"):
        view._log_mission_attack_event = AsyncMock()
    return view


def make_battle_view(p1: str = "Black Widow", p2: str = "Wolverine", p1_id: int = 111, p2_id: int = 222):
    c1, c2 = make_cards(p1, p2)
    return _neutralize_io(bot.BattleView(c1, c2, p1_id, p2_id, hp_view=None))


def make_mission_view(
    player: str = "Black Widow",
    boss: str = "Wolverine",
    user_id: int = 111,
    wave: int = 1,
    total: int = 4,
    mission_data: dict | None = None,
    selected: str | None = None,
):
    c1, c2 = make_cards(player, boss)
    mv = bot.MissionBattleView(
        c1, c2, user_id, wave, total,
        mission_data=mission_data if mission_data is not None else {},
        selected_card_name=selected or player,
    )
    return _neutralize_io(mv)


def make_interaction(user_id: int):
    """Minimaler Stand-in für discord.Interaction (kein Thread -> AFK-Pfade übersprungen)."""
    it = MagicMock(name="interaction")
    it.user.id = user_id
    it.guild = None
    it.channel = MagicMock(name="channel")
    it.message = MagicMock(name="message")
    it.message.edit = AsyncMock()
    it.response.send_message = AsyncMock()
    it.response.defer = AsyncMock()
    it.response.edit_message = AsyncMock()
    it.followup.send = AsyncMock()
    return it


def run_view_coro(coro_factory, *, timeout: float = 20.0):
    """Führt eine View-Coroutine aus und räumt die DB-Verbindung sauber ab.

    `coro_factory` ist eine 0-Argument-Funktion, die die zu treibende Coroutine liefert
    (z. B. ``lambda: view.execute_attack(it, 0)``).
    """

    async def _wrap():
        try:
            return await asyncio.wait_for(coro_factory(), timeout=timeout)
        finally:
            await close_db()

    return asyncio.run(_wrap())
