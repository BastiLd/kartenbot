"""Infinitydust-Belohnungs-Akkumulator für Missionen (Req. 7).

Der :class:`MissionRewardAccumulator` sammelt während einer laufenden Mission
die Infinitydust-Belohnungen auf (1 pro besiegtem Lakei, 1 pro besiegtem Boss,
optional +1 wenn eine als Reward verknüpfte Daily-Karte bereits im Besitz war).
Ausgezahlt wird erst beim erfolgreichen Mission-Abschluss
(:func:`commit_on_mission_success`); bei Abbruch oder Niederlage wird nichts
gutgeschrieben (:func:`discard_on_mission_failure`).

Der Akkumulator lebt im ``mission_state``-Dict und besitzt keine eigene Tabelle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Obergrenze für eine voll abgeschlossene Standard-Mission:
# 3 Lakeien + 1 Boss + 1 Daily-Duplikat-Bonus = 5 (Req. 7.7).
MISSION_INFINITYDUST_CAP = 5


@dataclass
class MissionRewardAccumulator:
    """Sammelt Infinitydust-Belohnungen über eine Mission hinweg."""

    user_id: int
    mission_id: str
    infinitydust: int = 0
    daily_card_bonus_pending: bool = False

    def on_lakai_defeated(self) -> None:
        """+1 Infinitydust für einen besiegten Lakei (Req. 7.1)."""
        self.infinitydust += 1

    def on_boss_defeated(self) -> None:
        """+1 Infinitydust für einen besiegten Boss (Req. 7.2)."""
        self.infinitydust += 1

    def on_daily_card_already_owned(self) -> None:
        """Markiert den +1-Bonus für eine bereits besessene Daily-Reward-Karte (Req. 7.7)."""
        self.daily_card_bonus_pending = True

    def total(self) -> int:
        """Gesamtsumme inkl. Daily-Bonus, gedeckelt auf den Mission-Cap."""
        raw = self.infinitydust + (1 if self.daily_card_bonus_pending else 0)
        return min(raw, MISSION_INFINITYDUST_CAP)


async def commit_on_mission_success(acc: MissionRewardAccumulator, *, add_infinitydust) -> int:
    """Zahlt die akkumulierte Belohnung beim Mission-Erfolg aus (Req. 7.5).

    ``add_infinitydust`` ist die async-Funktion ``add_infinitydust(user_id, amount)``.
    Gibt die tatsächlich ausgezahlte Menge zurück.
    """
    payout = acc.total()
    if payout > 0:
        try:
            await add_infinitydust(acc.user_id, payout)
        except Exception:  # pragma: no cover - defensiv, Mission soll trotzdem enden
            logger.exception(
                "Infinitydust-Auszahlung fehlgeschlagen (user=%s, mission=%s, amount=%s)",
                acc.user_id,
                acc.mission_id,
                payout,
            )
            return 0
    return payout


async def discard_on_mission_failure(acc: MissionRewardAccumulator) -> None:
    """Bei Abbruch oder Niederlage wird nichts ausgezahlt (Req. 7.6)."""
    return None
