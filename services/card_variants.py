from __future__ import annotations

import copy
from typing import Any, Iterable

from karten import karten as BASE_CARDS
from services.battle_types import CardData


VariantData = dict[str, Any]


def _cards_source(cards: Iterable[CardData] | None = None) -> list[CardData]:
    return list(BASE_CARDS if cards is None else cards)


def iter_card_variants(card: CardData) -> list[VariantData]:
    base_name = str(card.get("name") or "").strip()
    image_url = str(card.get("bild") or "").strip()
    raw_variants = card.get("variants")
    if not isinstance(raw_variants, list) or not raw_variants:
        return [
            {
                "variant_id": base_name,
                "display_name": base_name,
                "image_url": image_url,
                "admin_only": False,
                "reward_enabled": True,
                "sort_order": 0,
                "is_default": True,
            }
        ]

    normalized: list[VariantData] = []
    for index, item in enumerate(raw_variants):
        if not isinstance(item, dict):
            continue
        variant_id = str(item.get("variant_id") or "").strip()
        if not variant_id:
            continue
        normalized.append(
            {
                "variant_id": variant_id,
                "display_name": str(item.get("display_name") or variant_id).strip() or variant_id,
                "image_url": str(item.get("image_url") or image_url).strip() or image_url,
                "admin_only": bool(item.get("admin_only", False)),
                "reward_enabled": bool(item.get("reward_enabled", not bool(item.get("admin_only", False)))),
                "sort_order": int(item.get("sort_order", index) or index),
                "is_default": bool(item.get("is_default", False)),
            }
        )
    if not normalized:
        return [
            {
                "variant_id": base_name,
                "display_name": base_name,
                "image_url": image_url,
                "admin_only": False,
                "reward_enabled": True,
                "sort_order": 0,
                "is_default": True,
            }
        ]
    normalized.sort(key=lambda item: (int(item.get("sort_order", 0) or 0), str(item.get("display_name") or "")))
    if not any(bool(item.get("is_default", False)) for item in normalized):
        normalized[0]["is_default"] = True
    return normalized


def _find_card_and_variant(
    name: object,
    *,
    cards: Iterable[CardData] | None = None,
) -> tuple[CardData, VariantData | None] | None:
    wanted = str(name or "").strip()
    if not wanted:
        return None
    wanted_lower = wanted.lower()
    for card in _cards_source(cards):
        base_name = str(card.get("name") or "").strip()
        if base_name.lower() == wanted_lower:
            return card, None
        for variant in iter_card_variants(card):
            variant_id = str(variant.get("variant_id") or "").strip()
            if variant_id.lower() == wanted_lower:
                return card, variant
    return None


def base_card_name(name: object, *, cards: Iterable[CardData] | None = None) -> str:
    if isinstance(name, dict):
        base_name = str(name.get("base_name") or name.get("name") or "").strip()
        return base_name
    resolved = _find_card_and_variant(name, cards=cards)
    if resolved is None:
        return str(name or "").strip()
    card, _variant = resolved
    return str(card.get("name") or "").strip()


def default_variant_name_for_base(base_name: object, *, cards: Iterable[CardData] | None = None) -> str:
    resolved = _find_card_and_variant(base_name, cards=cards)
    if resolved is None:
        return str(base_name or "").strip()
    card, variant = resolved
    if variant is not None:
        return str(variant.get("variant_id") or "").strip()
    variants = iter_card_variants(card)
    for item in variants:
        if bool(item.get("is_default", False)):
            return str(item.get("variant_id") or base_name).strip()
    return str(variants[0].get("variant_id") or base_name).strip()


def variant_names_for_base(base_name: object, *, cards: Iterable[CardData] | None = None) -> list[str]:
    resolved = _find_card_and_variant(base_name, cards=cards)
    if resolved is None:
        raw = str(base_name or "").strip()
        return [raw] if raw else []
    card, _variant = resolved
    return [str(item.get("variant_id") or "").strip() for item in iter_card_variants(card) if str(item.get("variant_id") or "").strip()]


def normalize_owned_card_name(name: object, *, cards: Iterable[CardData] | None = None) -> str:
    resolved = _find_card_and_variant(name, cards=cards)
    if resolved is None:
        return str(name or "").strip()
    card, variant = resolved
    if variant is not None:
        return str(variant.get("variant_id") or "").strip()
    return default_variant_name_for_base(card.get("name"), cards=cards)


def build_runtime_card(
    name: object,
    *,
    cards: Iterable[CardData] | None = None,
) -> CardData | None:
    resolved = _find_card_and_variant(name, cards=cards)
    if resolved is None:
        return None
    card, variant = resolved
    runtime_card = copy.deepcopy(card)
    base_name = str(card.get("name") or "").strip()
    runtime_card["base_name"] = base_name
    runtime_card["variants"] = copy.deepcopy(iter_card_variants(card))
    if variant is None:
        runtime_card["variant_id"] = default_variant_name_for_base(base_name, cards=cards)
        runtime_card["variant_display_name"] = base_name
        runtime_card["admin_only"] = False
        runtime_card["reward_enabled"] = True
        runtime_card["sort_order"] = 0
        return runtime_card
    runtime_card["name"] = str(variant.get("display_name") or variant.get("variant_id") or base_name).strip() or base_name
    runtime_card["variant_id"] = str(variant.get("variant_id") or runtime_card["name"]).strip()
    runtime_card["variant_display_name"] = str(variant.get("display_name") or runtime_card["name"]).strip()
    runtime_card["bild"] = str(variant.get("image_url") or runtime_card.get("bild") or "").strip()
    runtime_card["admin_only"] = bool(variant.get("admin_only", False))
    runtime_card["reward_enabled"] = bool(variant.get("reward_enabled", not bool(variant.get("admin_only", False))))
    runtime_card["sort_order"] = int(variant.get("sort_order", 0) or 0)
    runtime_card["is_default_variant"] = bool(variant.get("is_default", False))
    return runtime_card


def reward_runtime_cards(cards: Iterable[CardData] | None = None) -> list[CardData]:
    reward_cards: list[CardData] = []
    for card in _cards_source(cards):
        variants = [item for item in iter_card_variants(card) if bool(item.get("reward_enabled", True))]
        if not variants:
            continue
        for variant in variants:
            runtime = build_runtime_card(str(variant.get("variant_id") or ""), cards=[card])
            if runtime is not None:
                reward_cards.append(runtime)
    return reward_cards


def variant_count_for_base(base_name: object, *, cards: Iterable[CardData] | None = None) -> int:
    return len(variant_names_for_base(base_name, cards=cards))


def exact_variant_names_with_amounts(
    owned_cards: Iterable[tuple[str, int]],
    base_name: object,
    *,
    cards: Iterable[CardData] | None = None,
) -> list[tuple[str, int]]:
    source_cards = _cards_source(cards)
    target_base = base_card_name(base_name, cards=cards)
    if not target_base:
        return []
    collected: dict[str, int] = {}
    for raw_name, raw_amount in owned_cards:
        normalized_name = normalize_owned_card_name(raw_name, cards=source_cards)
        if base_card_name(normalized_name, cards=source_cards) != target_base:
            continue
        collected[normalized_name] = collected.get(normalized_name, 0) + int(raw_amount)
    resolved = _find_card_and_variant(target_base, cards=source_cards)
    variant_order = {
        str(variant.get("variant_id") or ""): int(variant.get("sort_order", 0) or 0)
        for variant in (iter_card_variants(resolved[0]) if resolved is not None else [])
    }
    return sorted(
        [(name, amount) for name, amount in collected.items() if amount > 0],
        key=lambda item: (variant_order.get(item[0], 10**6), item[0].lower()),
    )


def group_owned_cards_by_base(
    owned_cards: Iterable[tuple[str, int]],
    *,
    cards: Iterable[CardData] | None = None,
) -> list[dict[str, Any]]:
    source_cards = _cards_source(cards)
    order_map = {str(card.get("name") or ""): index for index, card in enumerate(source_cards)}
    grouped: dict[str, dict[str, Any]] = {}
    for raw_name, raw_amount in owned_cards:
        amount = int(raw_amount or 0)
        if amount <= 0:
            continue
        normalized_name = normalize_owned_card_name(raw_name, cards=source_cards)
        base_name = base_card_name(normalized_name, cards=source_cards)
        if not base_name:
            continue
        group = grouped.setdefault(
            base_name,
            {
                "base_name": base_name,
                "total_amount": 0,
                "variants": {},
            },
        )
        group["total_amount"] = int(group.get("total_amount", 0) or 0) + amount
        variants = group.setdefault("variants", {})
        variants[normalized_name] = int(variants.get(normalized_name, 0) or 0) + amount
    result: list[dict[str, Any]] = []
    for base_name, payload in grouped.items():
        variants_map = payload.get("variants", {})
        variant_rows = exact_variant_names_with_amounts(
            [(name, int(amount)) for name, amount in variants_map.items()],
            base_name,
            cards=source_cards,
        )
        result.append(
            {
                "base_name": base_name,
                "total_amount": int(payload.get("total_amount", 0) or 0),
                "variants": variant_rows,
            }
        )
    result.sort(key=lambda item: (order_map.get(str(item.get("base_name") or ""), 10**9), str(item.get("base_name") or "").lower()))
    return result


def has_exact_variant(owned_cards: Iterable[tuple[str, int]], variant_name: object, *, cards: Iterable[CardData] | None = None) -> bool:
    wanted = normalize_owned_card_name(variant_name, cards=cards)
    for raw_name, raw_amount in owned_cards:
        if normalize_owned_card_name(raw_name, cards=cards) == wanted and int(raw_amount or 0) > 0:
            return True
    return False


def card_has_multiple_variants(base_name: object, *, cards: Iterable[CardData] | None = None) -> bool:
    return variant_count_for_base(base_name, cards=cards) > 1


def is_reward_enabled_variant(name: object, *, cards: Iterable[CardData] | None = None) -> bool:
    resolved = _find_card_and_variant(name, cards=cards)
    if resolved is None:
        return False
    _card, variant = resolved
    if variant is None:
        return True
    return bool(variant.get("reward_enabled", True))


def is_admin_only_variant(name: object, *, cards: Iterable[CardData] | None = None) -> bool:
    resolved = _find_card_and_variant(name, cards=cards)
    if resolved is None:
        return False
    _card, variant = resolved
    if variant is None:
        return False
    return bool(variant.get("admin_only", False))
