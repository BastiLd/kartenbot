from __future__ import annotations

import random
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable

from services.battle_types import CardData
from simulation.config import Playstyle, SimulationMetadata, SimulationMode
from simulation.loader import canonical_hero_name, fresh_runtime_copy
from simulation.modes import apply_mode_to_cards


PLAYER_ONE_ID = 1
PLAYER_TWO_ID = 2


@dataclass(slots=True)
class DuelResult:
    winner: str | None
    loser: str | None
    draw: bool
    rounds: int


@dataclass(slots=True)
class MatchupResult:
    hero_a: str
    hero_b: str
    fights: int
    wins_a: int
    wins_b: int
    draws: int = 0


@dataclass(slots=True)
class HeroAggregateResult:
    hero: str
    wins: int
    losses: int
    draws: int
    total_fights: int

    @property
    def winrate(self) -> float:
        return 0.0 if self.total_fights <= 0 else self.wins / self.total_fights


@dataclass(slots=True)
class ModeSimulationResult:
    mode: SimulationMode
    metadata: SimulationMetadata
    matchup_results: list[MatchupResult]
    hero_results: list[HeroAggregateResult]


@dataclass(slots=True)
class PlaystyleSimulationResult:
    playstyle: str
    metadata: SimulationMetadata
    mode_results: dict[SimulationMode, ModeSimulationResult]


@dataclass(slots=True)
class PlaystyleBatchResult:
    results: dict[str, PlaystyleSimulationResult] = field(default_factory=dict)


def warm_up_combat_core(cards: list[CardData] | None = None) -> None:
    from services.combat_runner import CombatRunner
    from simulation.strategy import build_strategy

    build_strategy("optimal", rng=random.Random(1))
    build_strategy("average", rng=random.Random(2))
    if cards and len(cards) >= 2:
        CombatRunner(fresh_runtime_copy(cards[0]), fresh_runtime_copy(cards[1]), starter_id=PLAYER_ONE_ID, debug=False)


def simulate_duel(
    card_a: CardData,
    card_b: CardData,
    *,
    starter_id: int,
    duel_seed: int,
    strategy_a_name: str,
    strategy_b_name: str,
    average_mistake_rate: float,
    debug: bool = False,
) -> DuelResult:
    from services.combat_runner import CombatRunner
    from simulation.strategy import build_strategy

    state = random.getstate()
    random.seed(duel_seed)
    try:
        runner = CombatRunner(fresh_runtime_copy(card_a), fresh_runtime_copy(card_b), starter_id=starter_id, debug=debug)
        strategy_a = build_strategy(strategy_a_name, rng=random.Random(duel_seed ^ 0xA11CE), average_mistake_rate=average_mistake_rate)
        strategy_b = build_strategy(strategy_b_name, rng=random.Random(duel_seed ^ 0xB0B), average_mistake_rate=average_mistake_rate)
        rounds = 0
        while not runner.is_finished() and rounds < 500:
            player_id = runner.current_turn
            strategy = strategy_a if player_id == runner.player1_id else strategy_b
            attack_index = strategy.select_attack_index(runner, player_id)
            runner.perform_turn(attack_index)
            rounds += 1
        winner_id = runner.winner_id() if rounds < 500 or runner.is_finished() else None
        if winner_id is None:
            return DuelResult(winner=None, loser=None, draw=True, rounds=rounds)
        loser_id = runner.other_player(winner_id)
        return DuelResult(
            winner=canonical_hero_name(runner.card_for(winner_id)),
            loser=canonical_hero_name(runner.card_for(loser_id)),
            draw=False,
            rounds=rounds,
        )
    finally:
        random.setstate(state)


def simulate_matchup(
    card_a: CardData,
    card_b: CardData,
    fights: int,
    *,
    rng: random.Random,
    playstyle: str,
    average_mistake_rate: float,
) -> MatchupResult:
    hero_a = canonical_hero_name(card_a)
    hero_b = canonical_hero_name(card_b)
    wins_a = 0
    wins_b = 0
    draws = 0
    for duel_index in range(max(1, int(fights))):
        duel_seed = rng.randrange(0, 2**31)
        starter_id = PLAYER_ONE_ID if duel_index % 2 == 0 else PLAYER_TWO_ID
        duel_result = simulate_duel(
            card_a,
            card_b,
            starter_id=starter_id,
            duel_seed=duel_seed,
            strategy_a_name=playstyle,
            strategy_b_name=playstyle,
            average_mistake_rate=average_mistake_rate,
        )
        if duel_result.draw:
            draws += 1
        elif duel_result.winner == hero_a:
            wins_a += 1
        else:
            wins_b += 1
    return MatchupResult(hero_a=hero_a, hero_b=hero_b, fights=int(fights), wins_a=wins_a, wins_b=wins_b, draws=draws)


def aggregate_hero_results(cards: list[CardData], matchup_results: list[MatchupResult]) -> list[HeroAggregateResult]:
    hero_names = [canonical_hero_name(card) for card in cards]
    stats = {hero: {"wins": 0, "losses": 0, "draws": 0, "total": 0} for hero in hero_names}
    for matchup in matchup_results:
        stats[matchup.hero_a]["wins"] += matchup.wins_a
        stats[matchup.hero_a]["losses"] += matchup.wins_b
        stats[matchup.hero_a]["draws"] += matchup.draws
        stats[matchup.hero_a]["total"] += matchup.fights
        stats[matchup.hero_b]["wins"] += matchup.wins_b
        stats[matchup.hero_b]["losses"] += matchup.wins_a
        stats[matchup.hero_b]["draws"] += matchup.draws
        stats[matchup.hero_b]["total"] += matchup.fights
    return [
        HeroAggregateResult(hero=hero, wins=payload["wins"], losses=payload["losses"], draws=payload["draws"], total_fights=payload["total"])
        for hero, payload in stats.items()
    ]


def simulate_full_round_robin(
    cards: list[CardData],
    fights_per_matchup: int,
    *,
    playstyle: str,
    mode: SimulationMode,
    seed: int | None,
    average_mistake_rate: float,
    progress_callback: Callable[[str], None] | None = None,
) -> ModeSimulationResult:
    mode_cards = apply_mode_to_cards(cards, mode)
    rng = random.Random(seed if seed is not None else 0)
    matchup_results: list[MatchupResult] = []
    all_pairs = list(combinations(mode_cards, 2))
    total_pairs = len(all_pairs)
    for index, (card_a, card_b) in enumerate(all_pairs, start=1):
        if progress_callback is not None:
            progress_callback(
                f"[{playstyle}][{mode.value}] {index}/{total_pairs} {canonical_hero_name(card_a)} vs {canonical_hero_name(card_b)}"
            )
        matchup_results.append(
            simulate_matchup(
                card_a,
                card_b,
                fights_per_matchup,
                rng=rng,
                playstyle=playstyle,
                average_mistake_rate=average_mistake_rate,
            )
        )
    metadata = SimulationMetadata(
        playstyle=playstyle,
        fights_per_matchup=int(fights_per_matchup),
        seed=seed,
        average_mistake_rate=float(average_mistake_rate),
    )
    hero_results = aggregate_hero_results(mode_cards, matchup_results)
    return ModeSimulationResult(mode=mode, metadata=metadata, matchup_results=matchup_results, hero_results=hero_results)


def simulate_playstyle(
    cards: list[CardData],
    modes: list[SimulationMode],
    fights_per_matchup: int,
    *,
    playstyle: str,
    seed: int | None,
    average_mistake_rate: float,
    progress_callback: Callable[[str], None] | None = None,
) -> PlaystyleSimulationResult:
    metadata = SimulationMetadata(
        playstyle=playstyle,
        fights_per_matchup=int(fights_per_matchup),
        seed=seed,
        average_mistake_rate=float(average_mistake_rate),
    )
    mode_results = {
        mode: simulate_full_round_robin(
            cards,
            fights_per_matchup,
            playstyle=playstyle,
            mode=mode,
            seed=None if seed is None else seed + position,
            average_mistake_rate=average_mistake_rate,
            progress_callback=progress_callback,
        )
        for position, mode in enumerate(modes)
    }
    return PlaystyleSimulationResult(playstyle=playstyle, metadata=metadata, mode_results=mode_results)


def simulate_playstyle_batch(
    cards: list[CardData],
    modes: list[SimulationMode],
    fights_per_matchup: int,
    *,
    playstyle: Playstyle,
    seed: int | None,
    average_mistake_rate: float,
    progress_callback: Callable[[str], None] | None = None,
) -> PlaystyleBatchResult:
    if playstyle == Playstyle.BOTH:
        ordered = [Playstyle.OPTIMAL.value, Playstyle.AVERAGE.value]
    else:
        ordered = [playstyle.value]
    result = PlaystyleBatchResult()
    for offset, style_name in enumerate(ordered):
        style_seed = None if seed is None else seed + offset * 1000
        result.results[style_name] = simulate_playstyle(
            cards,
            modes,
            fights_per_matchup,
            playstyle=style_name,
            seed=style_seed,
            average_mistake_rate=average_mistake_rate,
            progress_callback=progress_callback,
        )
    return result
