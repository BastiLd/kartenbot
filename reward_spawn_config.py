from __future__ import annotations

from typing import Any

from karten import COMMON, RARE

# Grundidee:
# - "Gewicht" bedeutet: Wie oft wird etwas gezogen?
# - Höheres Gewicht => Karte kommt häufiger.
# - Gewicht 0 oder kleiner => Karte wird in diesem Kontext nicht mehr gezogen.
CONTEXT_RARITY_WEIGHTS: dict[str, dict[str, float]] = {
    "default": {COMMON: 1.0, RARE: 1.0},
    "daily": {COMMON: 7.0, RARE: 3.0},
    "mission_reward": {COMMON: 8.0, RARE: 2.0},
    "mission_build_reward": {COMMON: 8.0, RARE: 2.0},
    "invite_reward": {COMMON: 7.0, RARE: 3.0},
    "draw_card": {COMMON: 1.0, RARE: 1.0},
}

# ---------------------------------------------------------
# EINFACHE KONFIGURATION (für Nicht-Programmierer)
# ---------------------------------------------------------
# Du kannst die Werte unten einfach als Text ändern:
# Format pro Zeile:
#   kontext: common=7, rare=3
#
# Beispiele:
#   daily: common=9, rare=1
#   mission_reward: common=8, rare=2
#
# Bekannte Kontexte:
# - default
# - daily
# - mission_reward
# - mission_build_reward
# - invite_reward
# - draw_card
#
# Bekannte Seltenheiten:
# - common
# - rare
#
# Wichtig:
# - Höhere Zahl = häufiger.
# - 0 oder kleiner = wird nicht gezogen.
#
# Wenn eine Zeile fehlerhaft ist, wird sie einfach ignoriert.
SIMPLE_CONTEXT_WEIGHTS_TEXT = """
default: common=1, rare=1
daily: common=7, rare=3
mission_reward: common=8, rare=2
mission_build_reward: common=8, rare=2
invite_reward: common=7, rare=3
draw_card: common=1, rare=1
""".strip()

# Optional:
# Hier können Karten-Gruppen (z. B. ein Team) extra bevorzugt oder geschwächt werden.
# Das Gruppengewicht wird mit dem Seltenheitsgewicht multipliziert.
CONTEXT_GROUP_WEIGHTS: dict[str, dict[str, dict[str, float]]] = {
    "default": {},
    "daily": {},
    "mission_reward": {},
    "mission_build_reward": {},
    "invite_reward": {},
    "draw_card": {},
}

# Gruppenname -> Kartennamen (exakt wie in karten["name"]).
CARD_GROUPS: dict[str, frozenset[str]] = {
    # Beispiel: "avengers": frozenset({"Iron-Man", "Captain America"}),
}


def _alias_rarity_key(raw: str) -> str:
    key = str(raw or "").strip().lower()
    if key in {"common", "gewöhnlich", "gewoehnlich"}:
        return COMMON
    if key in {"rare", "selten"}:
        return RARE
    return str(raw or "").strip()


def _apply_simple_context_weights_config() -> None:
    lines = [line.strip() for line in str(SIMPLE_CONTEXT_WEIGHTS_TEXT or "").splitlines() if line.strip()]
    for line in lines:
        if ":" not in line:
            continue
        ctx_raw, values_raw = line.split(":", 1)
        ctx = str(ctx_raw or "").strip().lower()
        if not ctx:
            continue
        parsed: dict[str, float] = {}
        for chunk in values_raw.split(","):
            part = str(chunk or "").strip()
            if "=" not in part:
                continue
            rarity_raw, weight_raw = part.split("=", 1)
            rarity = _alias_rarity_key(rarity_raw)
            try:
                weight = float(str(weight_raw or "").strip())
            except Exception:
                continue
            parsed[rarity] = weight
        if parsed:
            CONTEXT_RARITY_WEIGHTS[ctx] = parsed


_apply_simple_context_weights_config()


def _normalized_context(context: str | None) -> str:
    # Macht aus verschiedenen Schreibweisen einen stabilen Schlüssel.
    key = str(context or "").strip().lower()
    aliases = {
        "kampf": "default",
        "fight": "default",
        "pvp": "default",
    }
    key = aliases.get(key, key)
    if key not in CONTEXT_RARITY_WEIGHTS:
        return "default"
    return key


def rarity_weights_for_context(context: str | None) -> dict[str, float]:
    # Gibt nur positive Gewichte zurück.
    ctx = _normalized_context(context)
    raw = CONTEXT_RARITY_WEIGHTS.get(ctx) or CONTEXT_RARITY_WEIGHTS["default"]
    return {str(k): float(v) for k, v in raw.items() if float(v) > 0}


def group_weight_for_card(context: str | None, base_card_name: str) -> float:
    # Start mit neutral (=1.0). Jede gefundene Gruppe kann den Wert anpassen.
    ctx = _normalized_context(context)
    groups_cfg = CONTEXT_GROUP_WEIGHTS.get(ctx) or {}
    name = str(base_card_name or "").strip()
    mult = 1.0
    for group_name, weights in groups_cfg.items():
        members = CARD_GROUPS.get(group_name, frozenset())
        if name in members:
            mult *= float(weights.get(name, weights.get("*", 1.0)) or 1.0)
    return max(mult, 0.0)


def card_effective_weight(context: str | None, card: dict[str, Any]) -> float:
    # Endgewicht = Seltenheitsgewicht * (optional) Gruppengewicht.
    rarity = str(card.get("seltenheit") or "").strip() or COMMON
    rw = rarity_weights_for_context(context)
    base_w = float(rw.get(rarity, 1.0))
    base_name = str(card.get("name") or "").strip()
    return base_w * group_weight_for_card(context, base_name)
