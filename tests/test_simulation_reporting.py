from __future__ import annotations

from simulation.config import SimulationMetadata, SimulationMode
from simulation.engine import HeroAggregateResult, MatchupResult, ModeSimulationResult
from simulation.reporting import build_matchup_matrix, build_mode_report, build_ranking_rows, ranking_rows_as_dicts


def test_build_ranking_rows_sorts_by_requested_priority() -> None:
    rows = build_ranking_rows(
        [
            HeroAggregateResult(hero="Beta", wins=8, losses=2, draws=0, total_fights=10),
            HeroAggregateResult(hero="Alpha", wins=8, losses=1, draws=0, total_fights=10),
            HeroAggregateResult(hero="Gamma", wins=7, losses=3, draws=0, total_fights=10),
        ]
    )

    assert [row.hero for row in rows] == ["Alpha", "Beta", "Gamma"]
    assert rows[0].winrate == 0.8


def test_matchup_matrix_uses_per_pair_winrates() -> None:
    matrix = build_matchup_matrix(
        [
            MatchupResult(hero_a="Alpha", hero_b="Beta", fights=10, wins_a=7, wins_b=2, draws=1),
            MatchupResult(hero_a="Alpha", hero_b="Gamma", fights=10, wins_a=4, wins_b=6, draws=0),
        ]
    )

    assert matrix["Alpha"]["Alpha"] is None
    assert matrix["Alpha"]["Beta"] == 0.7
    assert matrix["Beta"]["Alpha"] == 0.2


def test_mode_report_prepares_excel_schema() -> None:
    result = ModeSimulationResult(
        mode=SimulationMode.ORIGINAL,
        metadata=SimulationMetadata(playstyle="optimal", fights_per_matchup=10, seed=1, average_mistake_rate=0.35),
        matchup_results=[MatchupResult(hero_a="Alpha", hero_b="Beta", fights=10, wins_a=7, wins_b=3, draws=0)],
        hero_results=[
            HeroAggregateResult(hero="Alpha", wins=7, losses=3, draws=0, total_fights=10),
            HeroAggregateResult(hero="Beta", wins=3, losses=7, draws=0, total_fights=10),
        ],
    )

    report = build_mode_report(result)
    rows = ranking_rows_as_dicts(report.rows)

    assert report.sheet_name == "Original"
    assert rows[0] == {"Rang": 1, "Held": "Alpha", "Wins": 7, "Losses": 3, "Winrate %": 0.7}
