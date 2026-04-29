from __future__ import annotations

from dataclasses import dataclass

from simulation.config import MODE_SHEET_NAMES, SimulationMode
from simulation.engine import HeroAggregateResult, MatchupResult, ModeSimulationResult, PlaystyleSimulationResult


@dataclass(slots=True)
class RankingRow:
    rank: int
    hero: str
    wins: int
    losses: int
    winrate: float


@dataclass(slots=True)
class ModeReport:
    mode: SimulationMode
    sheet_name: str
    playstyle: str
    rows: list[RankingRow]
    matrix: dict[str, dict[str, float | None]]


def build_ranking_rows(hero_results: list[HeroAggregateResult]) -> list[RankingRow]:
    sorted_results = sorted(
        hero_results,
        key=lambda item: (-item.winrate, -item.wins, item.losses, item.hero.lower()),
    )
    return [
        RankingRow(rank=index, hero=result.hero, wins=result.wins, losses=result.losses, winrate=result.winrate)
        for index, result in enumerate(sorted_results, start=1)
    ]


def build_matchup_matrix(matchup_results: list[MatchupResult]) -> dict[str, dict[str, float | None]]:
    heroes = sorted({matchup.hero_a for matchup in matchup_results} | {matchup.hero_b for matchup in matchup_results})
    matrix: dict[str, dict[str, float | None]] = {
        hero: {other: (None if hero == other else 0.0) for other in heroes}
        for hero in heroes
    }
    for matchup in matchup_results:
        total = max(1, matchup.fights)
        matrix[matchup.hero_a][matchup.hero_b] = matchup.wins_a / total
        matrix[matchup.hero_b][matchup.hero_a] = matchup.wins_b / total
    return matrix


def build_mode_report(result: ModeSimulationResult) -> ModeReport:
    return ModeReport(
        mode=result.mode,
        sheet_name=MODE_SHEET_NAMES[result.mode],
        playstyle=result.metadata.playstyle,
        rows=build_ranking_rows(result.hero_results),
        matrix=build_matchup_matrix(result.matchup_results),
    )


def build_playstyle_reports(result: PlaystyleSimulationResult) -> dict[SimulationMode, ModeReport]:
    return {mode: build_mode_report(mode_result) for mode, mode_result in result.mode_results.items()}


def ranking_rows_as_dicts(rows: list[RankingRow]) -> list[dict[str, object]]:
    return [
        {
            "Rang": row.rank,
            "Held": row.hero,
            "Wins": row.wins,
            "Losses": row.losses,
            "Winrate %": row.winrate,
        }
        for row in rows
    ]
