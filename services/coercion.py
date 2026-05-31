"""Reine Typ-/Coercion-Helfer ohne Discord- oder Bot-Abhaengigkeit.

Diese Funktionen wurden aus ``bot.py`` ausgelagert, damit sowohl der Monolith
als auch die Service-Schicht (z.B. ``services/combat_runner.py``) sie ohne
Rueckgriff auf das ``bot``-Modul nutzen koennen. Sie haengen nur von der
Standardbibliothek ab und sind damit gefahrlos teil- und testbar.

Die fuehrenden Unterstriche bleiben aus Kompatibilitaetsgruenden erhalten:
``bot.py`` importiert die Namen unveraendert zurueck, sodass bestehende
Aufrufer (inklusive ``bot_module._maybe_int`` & Co.) weiterlaufen.
"""

from __future__ import annotations

import json
import random
from typing import Any


def _maybe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _maybe_float(value: object) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _json_clone(value: object) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=True))
    except (TypeError, ValueError):
        return value


def _dict_str_any(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_any(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _int_keyed_dict(value: object) -> dict[int, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[int, Any] = {}
    for key, item in value.items():
        parsed_key = _maybe_int(key)
        if parsed_key is None:
            continue
        result[parsed_key] = item
    return result


def _nested_int_keyed_dict(value: object) -> dict[int, dict[int, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[int, dict[int, Any]] = {}
    for key, item in value.items():
        outer_key = _maybe_int(key)
        if outer_key is None:
            continue
        if isinstance(item, dict):
            inner: dict[int, Any] = {}
            for inner_key, inner_value in item.items():
                parsed_inner_key = _maybe_int(inner_key)
                if parsed_inner_key is None:
                    continue
                inner[parsed_inner_key] = inner_value
            result[outer_key] = inner
        else:
            result[outer_key] = {}
    return result


def _nested_int_keyed_int_dict(value: object) -> dict[int, dict[int, int]]:
    source = _nested_int_keyed_dict(value)
    result: dict[int, dict[int, int]] = {}
    for outer_key, item in source.items():
        inner_result: dict[int, int] = {}
        for inner_key, inner_value in item.items():
            parsed = _maybe_int(inner_value)
            inner_result[inner_key] = 0 if parsed is None else parsed
        result[outer_key] = inner_result
    return result


def _int_keyed_bool_dict(value: object) -> dict[int, bool]:
    source = _int_keyed_dict(value)
    return {key: bool(item) for key, item in source.items()}


def _int_keyed_int_dict(value: object) -> dict[int, int]:
    source = _int_keyed_dict(value)
    result: dict[int, int] = {}
    for key, item in source.items():
        parsed = _maybe_int(item)
        if parsed is None:
            result[key] = 0
        else:
            result[key] = parsed
    return result


def _int_keyed_float_dict(value: object) -> dict[int, float]:
    source = _int_keyed_dict(value)
    result: dict[int, float] = {}
    for key, item in source.items():
        parsed = _maybe_float(item)
        if parsed is None:
            result[key] = 0.0
        else:
            result[key] = parsed
    return result


def _range_pair(value: object, *, default_min: int = 0, default_max: int = 0) -> tuple[int, int]:
    if isinstance(value, list) and len(value) == 2:
        first = _maybe_int(value[0])
        second = _maybe_int(value[1])
        if first is not None and second is not None:
            return first, second
    parsed = _maybe_int(value)
    if parsed is None:
        return default_min, default_max
    return parsed, parsed


def _coerce_damage_input(value: object, *, default: int = 0) -> int | list[int]:
    if isinstance(value, list) and len(value) == 2:
        min_value, max_value = _range_pair(value, default_min=default, default_max=default)
        return [min_value, max_value]
    parsed = _maybe_int(value)
    if parsed is None:
        return default
    return parsed


def _random_int_from_range(value: object, *, default: int = 0) -> int:
    min_value, max_value = _range_pair(value, default_min=default, default_max=default)
    if max_value < min_value:
        min_value, max_value = max_value, min_value
    return random.randint(min_value, max_value)
