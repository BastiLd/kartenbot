from __future__ import annotations

from typing import Any, TypeAlias


DamageValue: TypeAlias = int | list[int]
AttackEffect: TypeAlias = dict[str, object]
MultiHitConfig: TypeAlias = dict[str, object]
MultiHitRollDetails: TypeAlias = dict[str, object]
AttackData: TypeAlias = dict[str, Any]
CardData: TypeAlias = dict[str, Any]
