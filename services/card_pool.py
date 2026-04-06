from __future__ import annotations

import random
from typing import Iterable

from services.battle_types import CardData
from services.card_variants import base_card_name, build_runtime_card, reward_runtime_cards


ALPHA_PLAYABLE_CARD_NAMES: tuple[str, ...] = (
    "Black Widow",
    "Iron-Man",
    "Captain America",
    "Hulk",
    "Hawkeye",
    "Doctor Strange",
    "Black Panther",
    "Star Lord",
    "Groot",
    "Rocket",
    "Moon Knight",
    "Blade",
    "Wolverine",
    "Spider-Man",
)

CARD_NAME_ALIASES: dict[str, str] = {
    "black widow": "Black Widow",
    "iron-man": "Iron-Man",
    "iron man": "Iron-Man",
    "captain america": "Captain America",
    "captain amerika": "Captain America",
    "hulk": "Hulk",
    "hawkeye": "Hawkeye",
    "doctor strange": "Doctor Strange",
    "black panther": "Black Panther",
    "star": "Star Lord",
    "star lord": "Star Lord",
    "star-lord": "Star Lord",
    "groot": "Groot",
    "rocket": "Rocket",
    "moon knight": "Moon Knight",
    "blade": "Blade",
    "wolverine": "Wolverine",
    "spider-man": "Spider-Man",
    "spider man": "Spider-Man",
    "venom": "Venom",
    "captain marvel": "Captain Marvel",
    "ms marvel": "Ms Marvel",
    "miss marvel": "Ms Marvel",
    "ant-man": "Ant-Man",
    "ant man": "Ant-Man",
    "miles morales": "Miles Morales",
    "namor": "Namor",
    "nick fury": "Nick Fury",
    "shang-chi": "Shang-Chi",
    "shang chi": "Shang-Chi",
    "chon chin": "Shang-Chi",
    "she-hulk": "She-Hulk",
    "she hulk": "She-Hulk",
    "sue": "Sue Storm",
    "sue storm": "Sue Storm",
    "reed": "Mr. Fantastic",
    "mr fantastic": "Mr. Fantastic",
    "mr. fantastic": "Mr. Fantastic",
    "the thing": "The Thing",
    "human torch": "Human Torch",
    "humen torch": "Human Torch",
    "thor": "Thor",
    "cyclops": "Cyclops",
}


def canonical_card_name(name: object) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    runtime_card = build_runtime_card(raw)
    if runtime_card is not None:
        return str(runtime_card.get("name") or raw)
    base_alias = CARD_NAME_ALIASES.get(raw.lower())
    if base_alias:
        return base_alias
    return raw


def card_is_alpha_playable(name: object) -> bool:
    return base_card_name(canonical_card_name(name)) in ALPHA_PLAYABLE_CARD_NAMES


def alpha_playable_cards(cards: Iterable[CardData]) -> list[CardData]:
    return [card for card in cards if card_is_alpha_playable(card.get("name"))]


def gameplay_cards(cards: Iterable[CardData], *, alpha_enabled: bool) -> list[CardData]:
    _ = alpha_enabled
    return list(cards)


def filter_owned_cards_for_gameplay(
    owned_cards: Iterable[tuple[str, int]],
    *,
    alpha_enabled: bool,
) -> list[tuple[str, int]]:
    _ = alpha_enabled
    return [(canonical_card_name(name), int(amount)) for name, amount in owned_cards]


def random_gameplay_card(cards: Iterable[CardData], *, alpha_enabled: bool) -> CardData:
    pool = reward_runtime_cards(gameplay_cards(cards, alpha_enabled=alpha_enabled))
    if not pool:
        raise ValueError("No playable cards available for the current mode")
    return random.choice(pool)
