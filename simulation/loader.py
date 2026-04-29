from __future__ import annotations

import copy

from karten import karten as BASE_CARDS
from services.battle_types import CardData
from services.card_variants import base_card_name, build_runtime_card


def canonical_hero_name(card: CardData) -> str:
    return str(base_card_name(card)).strip()


def load_base_runtime_cards() -> list[CardData]:
    cards: list[CardData] = []
    seen: set[str] = set()
    for raw_card in BASE_CARDS:
        base_name = str(base_card_name(raw_card)).strip()
        if not base_name or base_name in seen:
            continue
        runtime_card = build_runtime_card(base_name, cards=BASE_CARDS)
        if runtime_card is None:
            continue
        seen.add(base_name)
        cards.append(runtime_card)
    return cards


def fresh_runtime_copy(card: CardData) -> CardData:
    return copy.deepcopy(card)
