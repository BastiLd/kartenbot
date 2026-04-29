from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SimulationMode(str, Enum):
    ORIGINAL = "original"
    LIGHT = "light"
    MAX = "max"


class Playstyle(str, Enum):
    OPTIMAL = "optimal"
    AVERAGE = "average"
    BOTH = "both"


MODE_SHEET_NAMES: dict[SimulationMode, str] = {
    SimulationMode.ORIGINAL: "Original",
    SimulationMode.LIGHT: "Leicht verbessert",
    SimulationMode.MAX: "Maximal verbessert",
}

DEFAULT_FIGHTS_PER_MATCHUP = 200
DEFAULT_AVERAGE_MISTAKE_RATE = 0.35
DEFAULT_OUTPUT = "simulation_results.xlsx"
DEFAULT_MATRIX_OUTPUT = "simulation_matchups.xlsx"


def modes_from_flag(value: str) -> list[SimulationMode]:
    normalized = str(value or "all").strip().lower()
    if normalized == "all":
        return [SimulationMode.ORIGINAL, SimulationMode.LIGHT, SimulationMode.MAX]
    if normalized == "original":
        return [SimulationMode.ORIGINAL]
    if normalized == "light":
        return [SimulationMode.LIGHT]
    if normalized == "max":
        return [SimulationMode.MAX]
    raise ValueError(f"Unsupported mode flag: {value}")


@dataclass(slots=True)
class SimulationMetadata:
    playstyle: str
    fights_per_matchup: int
    seed: int | None
    average_mistake_rate: float
