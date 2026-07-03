"""Zugriff auf die Spieldaten des Bots (Kartennamen inkl. Varianten).

Importiert karten.py und services/card_variants.py aus dem Projekt-Root —
das sind reine Daten-/Hilfsmodule ohne Discord-Abhängigkeit. Falls der Import
scheitert (z.B. anderes Layout im Container), fällt das Dashboard auf eine
Namensliste ohne Varianten-Normalisierung zurück.
"""
from __future__ import annotations

import sys

from .config import PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_ALL_NAMES: list[str] = []
_normalize = None

try:
    from karten import karten as _karten  # type: ignore
    from services.card_variants import (  # type: ignore
        iter_card_variants,
        normalize_owned_card_name,
    )

    _normalize = normalize_owned_card_name
    seen: set[str] = set()
    for card in _karten:
        for variant in iter_card_variants(card):
            name = str(variant.get("variant_id") or "").strip()
            if name and name not in seen:
                seen.add(name)
                _ALL_NAMES.append(name)
except Exception:  # pragma: no cover - Fallback ohne Bot-Module
    _ALL_NAMES = []
    _normalize = None


def all_card_names() -> list[str]:
    return list(_ALL_NAMES)


def normalize_card_name(name: str) -> str:
    if _normalize is not None:
        try:
            return str(_normalize(name))
        except Exception:
            return str(name)
    return str(name)


def is_known_card(name: str) -> bool:
    if not _ALL_NAMES:
        return True  # ohne Kartenliste keine Validierung möglich
    return normalize_card_name(name) in _ALL_NAMES or name in _ALL_NAMES
