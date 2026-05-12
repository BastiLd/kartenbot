from __future__ import annotations

from copy import deepcopy
from typing import Any


items: list[dict[str, Any]] = [
    {
        "id": "infinitydust",
        "name": "Infinitydust",
        "display_name": "Staub",
        "aliases": ["dust", "staub", "infinitydust"],
        "type": "currency",
        "beschreibung": "Ressource zum Verstaerken von Karten.",
        "seltenheit": "common",
        "bild": "https://i.imgur.com/Mx9ad7N.png",
        "thumbnail": "https://i.imgur.com/PAtPxVW.png",
        "reward_enabled": True,
        "admin_give_enabled": True,
        "stackable": True,
        "tradeable": False,
        "amount": {
            "default": 1,
            "min": 1,
            "max": 999999,
        },
        "storage": {
            "table": "user_infinitydust",
            "amount_column": "amount",
        },
        "presentation": {
            "color": 0x9D4EDD,
            "emoji": "DUST",
            "large_card": True,
            "show_description": True,
            "show_image": True,
        },
        "effects": [
            {
                "kind": "upgrade_resource",
                "target": "card",
            }
        ],
        "variants": [
            {
                "variant_id": "default",
                "display_name": "Staub",
                "reward_enabled": True,
                "sort_order": 0,
                "is_default": True,
            }
        ],
    },
    {
        "id": "unit",
        "name": "Unit",
        "display_name": "Unit",
        "aliases": ["unit", "units"],
        "type": "currency",
        "beschreibung": "Missionen- und Event-Waehrung.",
        "seltenheit": "rare",
        "bild": "https://i.imgur.com/vhlFFiM.png",
        "thumbnail": "https://i.imgur.com/vhlFFiM.png",
        "reward_enabled": True,
        "admin_give_enabled": True,
        "stackable": True,
        "tradeable": False,
        "amount": {
            "default": 1,
            "min": 1,
            "max": 999999,
        },
        "storage": {
            "table": "user_units",
            "amount_column": "amount",
        },
        "presentation": {
            "color": 0x2E86FF,
            "emoji": "UNIT",
            "large_card": True,
            "show_description": True,
            "show_image": False,
        },
        "effects": [
            {
                "kind": "mission_currency",
                "target": "mission",
            }
        ],
        "variants": [
            {
                "variant_id": "default",
                "display_name": "Unit",
                "reward_enabled": True,
                "sort_order": 0,
                "is_default": True,
            }
        ],
    },
]


def all_items() -> list[dict[str, Any]]:
    return deepcopy(items)


def get_item_by_id(item_id: str) -> dict[str, Any] | None:
    wanted = str(item_id or "").strip().lower()
    if not wanted:
        return None
    for item in items:
        identifiers = {str(item.get("id") or "").lower(), str(item.get("name") or "").lower()}
        identifiers.update(str(alias).lower() for alias in item.get("aliases", []))
        if wanted in identifiers:
            return deepcopy(item)
    return None
