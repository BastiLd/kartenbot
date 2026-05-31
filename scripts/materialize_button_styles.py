from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from pprint import pformat


ROOT = Path(__file__).resolve().parents[1]
KARTEN_PATH = ROOT / "karten.py"


def _compute_default_style(attack: dict) -> str:
    # blue: reload-type actions
    if bool(attack.get("requires_reload")) or attack.get("reload_name") is not None:
        return "blue"

    # green: heal-ish actions
    if attack.get("heal") is not None:
        return "green"
    effects = attack.get("effects") or []
    if isinstance(effects, list):
        for eff in effects:
            if not isinstance(eff, dict):
                continue
            eff_type = str(eff.get("type") or "").strip().lower()
            if eff_type in {"heal", "regen", "attack_heal", "mix_heal_or_max"}:
                return "green"

    # grey: pure utility (no direct damage)
    dmg = attack.get("damage")
    if isinstance(dmg, list) and len(dmg) == 2:
        try:
            if int(dmg[0]) == 0 and int(dmg[1]) == 0:
                return "grey"
        except Exception:
            pass

    # red: default for damaging actions
    return "red"


def _load_karten_module(path: Path):
    spec = importlib.util.spec_from_file_location("_karten_materialize", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = _load_karten_module(KARTEN_PATH)
    cards = list(getattr(mod, "karten"))

    # Ensure explicit button_style on all attacks (first 4 entries only)
    for card in cards:
        attacks = card.get("attacks") or []
        if not isinstance(attacks, list):
            continue
        for atk in attacks[:4]:
            if not isinstance(atk, dict):
                continue
            atk.setdefault("button_style", _compute_default_style(atk))

    # Rebuild file with materialized styles.
    # Note: this rewrites formatting but keeps data + end-of-file standard-attack normalization logic.
    header = f"""COMMON = {pformat(getattr(mod, "COMMON"))}
RARE = {pformat(getattr(mod, "RARE"))}
DEFAULT_HP = {pformat(getattr(mod, "DEFAULT_HP"))}
NEW_CARD_IMAGE = {pformat(getattr(mod, "NEW_CARD_IMAGE"))}

# Zentraler Konfig-Block für spätere Balance-Anpassungen.
DIRECT_DAMAGE_CAP = {pformat(getattr(mod, "DIRECT_DAMAGE_CAP"))}  # Maximales Schadenslimit für direkten normalen Angriffsschaden.
STANDARD_DAMAGE_UPGRADE_STEP = {pformat(getattr(mod, "STANDARD_DAMAGE_UPGRADE_STEP"))}  # So viel Schaden bekommt ein Standardangriff pro Upgrade dazu.
STANDARD_DAMAGE_UPGRADE_MAX_TIMES = {pformat(getattr(mod, "STANDARD_DAMAGE_UPGRADE_MAX_TIMES"))}  # So oft darf ein Standardangriff maximal verbessert werden.
SPECIAL_DAMAGE_UPGRADE_STEP = {pformat(getattr(mod, "SPECIAL_DAMAGE_UPGRADE_STEP"))}  # So viel Schaden bekommt eine Spezialfähigkeit pro Upgrade dazu.
SPECIAL_DAMAGE_UPGRADE_MAX_TIMES = {pformat(getattr(mod, "SPECIAL_DAMAGE_UPGRADE_MAX_TIMES"))}  # So oft darf eine Spezialfähigkeit maximal verbessert werden.
DOT_TYPE_DEFAULTS = {pformat(getattr(mod, "DOT_TYPE_DEFAULTS"), width=160)}


karten = {pformat(cards, width=160)}


for card in karten:
    attacks = list(card.get("attacks", []))
    if not attacks:
        continue
    if not any(bool(attack.get("is_standard_attack")) for attack in attacks[:4]):
        attacks[0]["is_standard_attack"] = True
"""

    KARTEN_PATH.write_text(header, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

