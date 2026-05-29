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

# Standard-Fallback, falls mission_dust_config.py fehlt: jede Welle 1 Staub.
_DEFAULT_WAVE_REWARDS = {1: 1, 2: 1, 3: 1, 4: 1}


def _wave_config() -> dict:
    """Lädt ``mission_dust_config.WAVE_DUST_REWARDS`` defensiv."""
    try:
        import mission_dust_config as _cfg  # type: ignore[import-not-found]
        raw = getattr(_cfg, "WAVE_DUST_REWARDS", None)
        if isinstance(raw, dict):
            return raw
    except Exception:
        logger.warning("mission_dust_config.py nicht ladbar - Standardwerte aktiv")
    return {w: {"enabled": True, "amount": a} for w, a in _DEFAULT_WAVE_REWARDS.items()}


def wave_dust_reward(wave_num: int) -> int:
    """Liefert den konfigurierten Staub-Betrag für eine Welle (0 wenn deaktiviert)."""
    entry = _wave_config().get(int(wave_num))
    if not isinstance(entry, dict) or not entry.get("enabled", False):
        return 0
    try:
        return max(0, int(entry.get("amount", 0) or 0))
    except (TypeError, ValueError):
        return 0


def daily_duplicate_bonus() -> int:
    """Liefert den konfigurierten Daily-Duplikat-Bonus (0 wenn deaktiviert)."""
    try:
        import mission_dust_config as _cfg  # type: ignore[import-not-found]
        if not bool(getattr(_cfg, "DAILY_DUPLICATE_BONUS_ENABLED", True)):
            return 0
        return max(0, int(getattr(_cfg, "DAILY_DUPLICATE_BONUS_AMOUNT", 1) or 0))
    except Exception:
        return 1


def max_mission_total() -> int:
    """Maximal möglicher Mission-Staub (Summe aller Wellen + Daily-Bonus)."""
    cfg = _wave_config()
    total = 0
    for entry in cfg.values():
        if isinstance(entry, dict) and entry.get("enabled", False):
            try:
                total += max(0, int(entry.get("amount", 0) or 0))
            except (TypeError, ValueError):
                pass
    return total + daily_duplicate_bonus()


# Rückwärtskompatibler Default-Cap (Standard-Mission: 3 Lakeien + Boss + Daily = 5).
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
        """Gesamtsumme inkl. Daily-Bonus, gedeckelt auf den konfigurierten Maximalwert."""
        raw = self.infinitydust + (daily_duplicate_bonus() if self.daily_card_bonus_pending else 0)
        return min(raw, max_mission_total())


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
