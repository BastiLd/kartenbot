"""Kartenliste des Bots.

Es wird bewusst die echte Liste aus karten.py benutzt statt einer Kopie —
so kann die Website niemals eine Karte vergeben, die es im Spiel gar nicht
gibt, und neue Karten tauchen ohne Zutun auf.
"""
from __future__ import annotations

import sys
from functools import lru_cache

from . import config

if str(config.PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(config.PROJECT_ROOT))


@lru_cache(maxsize=1)
def _load() -> tuple[list[dict], object | None]:
    try:
        from karten import karten                                  # type: ignore
    except Exception:                                              # noqa: BLE001
        return [], None
    try:
        from services.card_variants import iter_card_variants      # type: ignore
    except Exception:                                              # noqa: BLE001
        iter_card_variants = None                                  # type: ignore
    return list(karten), iter_card_variants


def available() -> bool:
    cards, _ = _load()
    return bool(cards)


def catalog() -> list[dict]:
    """Alle Karten mit ihren Varianten, aufbereitet für die Oberfläche."""
    cards, iter_variants = _load()
    out = []
    for card in cards:
        entry = {
            "name": card.get("name"),
            "seltenheit": card.get("seltenheit"),
            "hp": card.get("hp"),
            "beschreibung": card.get("beschreibung"),
            "bild": card.get("bild"),
            "angriffe": [
                {"name": a.get("name"), "schaden": a.get("damage") or a.get("schaden"),
                 "info": a.get("info")}
                for a in (card.get("attacks") or [])
            ],
            "varianten": [],
        }
        if iter_variants:
            try:
                for variant in iter_variants(card):
                    entry["varianten"].append({
                        "name": variant.get("name") if isinstance(variant, dict) else str(variant),
                        "nur_admin": bool(variant.get("admin_only")) if isinstance(variant, dict) else False,
                    })
            except Exception:                                      # noqa: BLE001
                pass
        out.append(entry)
    return out


@lru_cache(maxsize=1)
def _valid_names() -> set[str]:
    names: set[str] = set()
    for card in catalog():
        if card["name"]:
            names.add(str(card["name"]).strip().lower())
        for variant in card["varianten"]:
            if variant["name"]:
                names.add(str(variant["name"]).strip().lower())
    return names


def resolve(name: str) -> str | None:
    """Prüft einen Kartennamen gegen die echte Liste. Gibt die korrekt
    geschriebene Fassung zurück oder None, wenn es die Karte nicht gibt."""
    wanted = str(name or "").strip()
    if not wanted:
        return None
    if not available():
        # Ohne Kartenliste lieber nichts durchlassen, als Unsinn zu verbuchen.
        return None
    if wanted.lower() not in _valid_names():
        return None
    for card in catalog():
        if str(card["name"]).strip().lower() == wanted.lower():
            return str(card["name"])
        for variant in card["varianten"]:
            if str(variant["name"]).strip().lower() == wanted.lower():
                return str(variant["name"])
    return None


def rarities() -> dict[str, list[str]]:
    gruppen: dict[str, list[str]] = {}
    for card in catalog():
        key = str(card.get("seltenheit") or "unbekannt").strip().lower()
        gruppen.setdefault(key, []).append(str(card["name"]))
    return gruppen
