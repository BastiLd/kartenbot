from __future__ import annotations

from karten import karten as base_cards
from simulation.cli import parse_args
from simulation.config import Playstyle, SimulationMode
from simulation.engine import simulate_matchup, simulate_playstyle_batch
from simulation.loader import canonical_hero_name, load_base_runtime_cards
from simulation.modes import apply_mode_to_card
import random


def test_loader_builds_runtime_base_cards_without_variant_duplicates() -> None:
    loaded = load_base_runtime_cards()
    unique_base_names = {str(card.get("name") or "") for card in base_cards}

    assert loaded
    assert len(loaded) == len(unique_base_names)
    assert len({canonical_hero_name(card) for card in loaded}) == len(loaded)
    assert all("Alpha_Iron-Man" != card.get("name") for card in loaded)


def test_single_matchup_runs_headless() -> None:
    cards = load_base_runtime_cards()
    result = simulate_matchup(cards[0], apply_mode_to_card(cards[1], SimulationMode.ORIGINAL), 2, rng=random.Random(3), playstyle="optimal", average_mistake_rate=0.35)

    assert result.fights == 2
    assert result.wins_a + result.wins_b + result.draws == 2


def test_playstyle_batch_smoke_run() -> None:
    cards = load_base_runtime_cards()[:2]
    batch = simulate_playstyle_batch(cards, [SimulationMode.ORIGINAL], 1, playstyle=Playstyle.OPTIMAL, seed=4, average_mistake_rate=0.35)

    assert "optimal" in batch.results
    assert SimulationMode.ORIGINAL in batch.results["optimal"].mode_results


def test_cli_defaults_to_optimal_playstyle() -> None:
    args = parse_args([])

    assert args.playstyle == "optimal"
