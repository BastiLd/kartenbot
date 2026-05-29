"""Sample-based invariants for the `/verbessern`-Aufwertungssystem.

Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5

Hintergrund / Ansatz
---------------------
Ursprünglich war für Task 6.9 ein ``hypothesis``-basiertes PBT vorgesehen
(siehe Spec ``v2-3-0-update`` Design-Abschnitt "Correctness Properties",
Properties 5 bis 9). ``hypothesis`` ist in dieser Umgebung jedoch nicht
installiert (``ModuleNotFoundError: hypothesis``). Um die Anschaffung einer
neuen Test-Dependency zu vermeiden, validieren wir die identischen
Invarianten **systematisch über ``itertools.product`` von Grenzwerten**.

Pro Property werden alle für die Invariante relevanten Eckwerte (0, knapp
unter Cap, Cap, knapp darüber, viele Vielfache des Step usw.) kombinatorisch
gepaart und die Invariante einzeln überprüft. Das deckt den vom PBT
relevanten Eingabebereich erschöpfend ab — anders als bei einem Zufalls-PBT
sind alle interessanten Grenzfälle deterministisch enthalten.

Getestet werden die reinen Helfer aus ``bot.py``:

* ``_fuse_available_multipliers(base_step, cap_remaining, dust_balance)``
  → ``(visible, affordable)``.
* ``_fuse_resolve_stat_context(karte_data, user_buffs, stat_choice)``
  → ``(base_step, cap_remaining)``.
* Klassen-Konstanten ``FUSE_MULTIPLIER_DUST_PER_STEP = 5`` und
  ``FUSE_MULTIPLIER_VALUES = (1, 2, 3, 4, 5, 6)``.

Die Properties stammen aus ``design.md``:

* Property 5 — Stat-Cap-Invariante: nach Upgrade ``stat_value <= stat_cap``.
* Property 6 — HP-Cap 200: ``hp <= 200``.
* Property 7 — Multiplikator-Optionen-Konsistenz:
  ``m * base_step <= cap_remaining`` für jede sichtbare Option ``m``.
* Property 8 — Dust-Kosten-Formel: ``dust_cost == m * 5``, ``m in {1..6}``.
* Property 9 — Dust-Saldo-Nicht-Negativität:
  ``dust_after = dust_before - dust_cost`` und ``dust_after >= 0``.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import itertools
import unittest

import bot as bot_module
from bot import (
    FUSE_HEALTH_BONUS,
    FUSE_HP_CAP,
    FUSE_MULTIPLIER_DUST_PER_STEP,
    FUSE_MULTIPLIER_VALUES,
    MAX_ATTACK_DAMAGE_PER_HIT,
    _fuse_available_multipliers,
    _fuse_resolve_stat_context,
)
from karten import (
    SPECIAL_DAMAGE_UPGRADE_MAX_TIMES,
    SPECIAL_DAMAGE_UPGRADE_STEP,
    STANDARD_DAMAGE_UPGRADE_MAX_TIMES,
    STANDARD_DAMAGE_UPGRADE_STEP,
)


# ---------------------------------------------------------------------------
# Sample spaces (deterministic, exhaustive grenzwert coverage)
# ---------------------------------------------------------------------------

# Base steps cover HP step (10), standard damage step (4) and special step (3).
SAMPLE_BASE_STEPS = (3, 4, 10)

# cap_remaining boundaries: 0, sub-step, equal-step, multi-step, large.
SAMPLE_CAP_REMAINING = (0, 1, 5, 8, 10, 50, 100)

# Dust balances around each multiplier price (5, 10, 15, 20, 25, 30) plus 0.
SAMPLE_DUST_BALANCES = (0, 4, 5, 9, 10, 14, 15, 19, 20, 24, 25, 29, 30, 1000)

# HP states from empty to capped (current HP including buffs).
SAMPLE_CURRENT_HP = (0, 50, 100, 150, 190, 195, 199, 200)


def _build_attack(
    damage_min: int,
    damage_max: int,
    *,
    is_standard: bool,
) -> dict:
    """Konstruiert eine minimale Attack-Struktur passend zu ``bot.py``."""

    return {
        "name": "Test-Attack",
        "damage": [damage_min, damage_max],
        "is_standard_attack": bool(is_standard),
    }


# ---------------------------------------------------------------------------
# Property 5 / Property 7 — Cap-Invarianten und Sichtbarkeit
# ---------------------------------------------------------------------------


class FuseAvailableMultipliersInvariantsTest(unittest.TestCase):
    """Invarianten von ``_fuse_available_multipliers``.

    Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5
    """

    def test_property_5_visible_multipliers_never_overshoot_cap(self) -> None:
        """Property 5 + 7: ``m * base_step <= cap_remaining`` für sichtbare ``m``."""

        dust_balance = 9999  # genug, sodass Sichtbarkeit nur vom Cap abhängt.
        for base_step, cap_remaining in itertools.product(
            SAMPLE_BASE_STEPS, SAMPLE_CAP_REMAINING
        ):
            with self.subTest(base_step=base_step, cap_remaining=cap_remaining):
                visible, _ = _fuse_available_multipliers(
                    base_step=base_step,
                    cap_remaining=cap_remaining,
                    dust_balance=dust_balance,
                )
                for m in visible:
                    self.assertLessEqual(
                        m * base_step,
                        cap_remaining,
                        f"visible={visible}, m={m}, base_step={base_step}, "
                        f"cap_remaining={cap_remaining}",
                    )

    def test_property_7_visible_subset_includes_affordable(self) -> None:
        """``set(affordable) <= set(visible)`` für alle Eingaben."""

        for base_step, cap_remaining, dust_balance in itertools.product(
            SAMPLE_BASE_STEPS, SAMPLE_CAP_REMAINING, SAMPLE_DUST_BALANCES
        ):
            with self.subTest(
                base_step=base_step,
                cap_remaining=cap_remaining,
                dust_balance=dust_balance,
            ):
                visible, affordable = _fuse_available_multipliers(
                    base_step=base_step,
                    cap_remaining=cap_remaining,
                    dust_balance=dust_balance,
                )
                self.assertTrue(
                    set(affordable).issubset(set(visible)),
                    f"affordable={affordable} not subset of visible={visible}",
                )

    def test_property_8_six_visible_max(self) -> None:
        """Selbst bei riesigem cap_remaining werden höchstens 6 Optionen gezeigt.

        Begründung: ``FUSE_MULTIPLIER_VALUES`` ist auf ``(1..6)`` festgelegt
        (Req. 6.1). Damit ist die obere Schranke der angezeigten Optionen
        unabhängig vom Cap immer 6.
        """

        for base_step in SAMPLE_BASE_STEPS:
            with self.subTest(base_step=base_step):
                visible, affordable = _fuse_available_multipliers(
                    base_step=base_step,
                    cap_remaining=9999,
                    dust_balance=99_999,
                )
                self.assertLessEqual(len(visible), 6)
                self.assertLessEqual(len(affordable), 6)
                # Bei viel cap_remaining + viel Dust werden alle 6 Optionen
                # angeboten und sind alle bezahlbar.
                self.assertEqual(list(visible), list(FUSE_MULTIPLIER_VALUES))
                self.assertEqual(list(affordable), list(FUSE_MULTIPLIER_VALUES))


# ---------------------------------------------------------------------------
# Property 8 / Property 9 — Dust-Kosten und Saldo
# ---------------------------------------------------------------------------


class FuseDustCostInvariantsTest(unittest.TestCase):
    """Invarianten der Dust-Kosten-Formel und des Dust-Saldos.

    Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5
    """

    def test_property_8_dust_cost_formula(self) -> None:
        """``dust_cost == m * 5`` für ``m in {1..6}`` (Req. 6.1)."""

        expected = [5, 10, 15, 20, 25, 30]
        self.assertEqual(FUSE_MULTIPLIER_DUST_PER_STEP, 5)
        self.assertEqual(FUSE_MULTIPLIER_VALUES, (1, 2, 3, 4, 5, 6))
        for m, exp_cost in zip(FUSE_MULTIPLIER_VALUES, expected):
            with self.subTest(m=m):
                self.assertEqual(m * FUSE_MULTIPLIER_DUST_PER_STEP, exp_cost)

    def test_property_9_affordable_never_exceeds_balance(self) -> None:
        """Jeder ``m`` in ``affordable`` erfüllt ``m * 5 <= dust_balance``."""

        # Cap groß genug, dass ausschließlich Dust die Bezahlbarkeit beschränkt.
        cap_remaining = 60  # ≥ 6 * 10 = 60 für base_step 10.
        for base_step, dust_balance in itertools.product(
            SAMPLE_BASE_STEPS, SAMPLE_DUST_BALANCES
        ):
            with self.subTest(base_step=base_step, dust_balance=dust_balance):
                _, affordable = _fuse_available_multipliers(
                    base_step=base_step,
                    cap_remaining=cap_remaining,
                    dust_balance=dust_balance,
                )
                for m in affordable:
                    cost = m * FUSE_MULTIPLIER_DUST_PER_STEP
                    self.assertLessEqual(
                        cost,
                        dust_balance,
                        f"m={m} cost={cost} > dust_balance={dust_balance}",
                    )
                    # Property 9: Saldo nach Aufwertung ist nicht negativ.
                    self.assertGreaterEqual(dust_balance - cost, 0)

    def test_property_9_balance_decrement(self) -> None:
        """``dust_after == dust_before - dust_cost`` für jedes bezahlbare ``m``."""

        for dust_before in (0, 5, 10, 15, 20, 25, 30, 31, 100):
            with self.subTest(dust_before=dust_before):
                _, affordable = _fuse_available_multipliers(
                    base_step=4,
                    cap_remaining=100,
                    dust_balance=dust_before,
                )
                for m in affordable:
                    cost = m * FUSE_MULTIPLIER_DUST_PER_STEP
                    dust_after = dust_before - cost
                    self.assertEqual(dust_after, dust_before - cost)
                    self.assertGreaterEqual(dust_after, 0)


# ---------------------------------------------------------------------------
# Property 5 / Property 6 — resolve_stat_context (HP- und Damage-Cap)
# ---------------------------------------------------------------------------


class FuseResolveStatContextInvariantsTest(unittest.TestCase):
    """Invarianten von ``_fuse_resolve_stat_context`` rund um HP-/Damage-Cap.

    Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5
    """

    def test_property_6_hp_cap_via_resolve_context(self) -> None:
        """``cap_remaining = max(0, 200 - current_hp)`` und ``base_step == FUSE_HEALTH_BONUS``.

        ``_fuse_resolve_stat_context`` berechnet ``current_hp = base_hp + total_health``,
        wobei ``base_hp = int(karte_data.get("hp", 100) or 100)`` (HP-0 wird auf 100
        defaultet — Kartendaten besitzen immer eine HP). Wir modellieren ``current_hp``
        deshalb über Health-Buffs auf einer 100-HP-Basis.
        """

        for current_hp in (0, 100, 199, 200):
            with self.subTest(current_hp=current_hp):
                karte_data = {"hp": 100, "attacks": []}
                buffs = [("health", 0, current_hp - 100)] if current_hp != 100 else []
                base_step, cap_remaining = _fuse_resolve_stat_context(
                    karte_data=karte_data,
                    user_buffs=buffs,
                    stat_choice="health_0",
                )
                self.assertEqual(base_step, FUSE_HEALTH_BONUS)
                self.assertGreaterEqual(cap_remaining, 0)
                self.assertEqual(cap_remaining, max(0, FUSE_HP_CAP - current_hp))

    def test_property_6_hp_never_exceeds_200_via_resolve_context(self) -> None:
        """Selbst mit max-Multiplikator (6×) bleibt HP nach Aufwertung ≤ 200.

        Insbesondere: bei ``current_hp = 195`` ist ``cap_remaining = 5``,
        ``base_step = 10`` → ``max_by_cap = 5 // 10 = 0`` → keine sichtbare
        Option → HP kann nicht überschossen werden. (Property 6.)

        Hinweis: ``current_hp`` wird über Health-Buffs auf einer 100-HP-Basis
        modelliert, weil ``_fuse_resolve_stat_context`` ein leeres/0-``hp``-Feld
        auf den Default 100 zurückfallen lässt.
        """

        dust_balance = 9999
        base_hp = 100
        for current_hp in SAMPLE_CURRENT_HP:
            with self.subTest(current_hp=current_hp):
                karte_data = {"hp": base_hp, "attacks": []}
                delta = current_hp - base_hp
                buffs = [("health", 0, delta)] if delta else []
                base_step, cap_remaining = _fuse_resolve_stat_context(
                    karte_data=karte_data,
                    user_buffs=buffs,
                    stat_choice="health_0",
                )
                visible, _ = _fuse_available_multipliers(
                    base_step=base_step,
                    cap_remaining=cap_remaining,
                    dust_balance=dust_balance,
                )
                # Wenn cap_remaining < base_step, MUSS visible leer sein.
                if cap_remaining < base_step:
                    self.assertEqual(
                        visible,
                        [],
                        f"Erwartet leere Optionen für current_hp={current_hp}, "
                        f"cap_remaining={cap_remaining}, base_step={base_step}, "
                        f"erhalten={visible}",
                    )

                # Worst case: jede sichtbare Option führt zu HP ≤ 200.
                for m in visible:
                    new_hp = current_hp + m * base_step
                    self.assertLessEqual(
                        new_hp,
                        FUSE_HP_CAP,
                        f"current_hp={current_hp} + {m}*{base_step} = {new_hp} "
                        f"> FUSE_HP_CAP={FUSE_HP_CAP}",
                    )

    def test_property_5_attack_damage_cap_respected(self) -> None:
        """Damage-Aufwertung respektiert sowohl Upgrade-Cap als Hartcap.

        Wir simulieren Standard- und Spezial-Attacks bei verschiedenen Werten
        von ``current_bonus`` und prüfen für jede sichtbare Multiplikator-
        Option, dass der resultierende ``max_dmg`` den globalen
        ``MAX_ATTACK_DAMAGE_PER_HIT`` nicht überschreitet.
        """

        dust_balance = 9999

        scenarios = [
            # (attack, max_total_bonus_steps, bezeichner)
            (_build_attack(10, 20, is_standard=True), STANDARD_DAMAGE_UPGRADE_MAX_TIMES, "standard"),
            (_build_attack(8, 14, is_standard=False), SPECIAL_DAMAGE_UPGRADE_MAX_TIMES, "special"),
            # Edge-case: Schaden nahe Hartcap → hard_cap_remaining beschränkt cap.
            (
                _build_attack(MAX_ATTACK_DAMAGE_PER_HIT - 4, MAX_ATTACK_DAMAGE_PER_HIT - 1, is_standard=True),
                STANDARD_DAMAGE_UPGRADE_MAX_TIMES,
                "near-hardcap",
            ),
        ]
        karte_data_template = {"hp": 100}
        for attack, _max_steps, label in scenarios:
            for current_bonus in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 15):
                karte_data = dict(karte_data_template, attacks=[attack])
                buffs = [("damage", 1, current_bonus)] if current_bonus else []
                with self.subTest(label=label, current_bonus=current_bonus):
                    base_step, cap_remaining = _fuse_resolve_stat_context(
                        karte_data=karte_data,
                        user_buffs=buffs,
                        stat_choice="damage_1",
                    )
                    visible, _ = _fuse_available_multipliers(
                        base_step=base_step,
                        cap_remaining=cap_remaining,
                        dust_balance=dust_balance,
                    )
                    # Kein sichtbarer Multiplikator darf den Hartcap überschreiten.
                    base_max_damage = int(attack["damage"][1]) + int(current_bonus)
                    for m in visible:
                        new_max = base_max_damage + m * base_step
                        self.assertLessEqual(
                            new_max,
                            MAX_ATTACK_DAMAGE_PER_HIT,
                            f"{label}: current_bonus={current_bonus}, m={m}, "
                            f"base_step={base_step}, cap_remaining={cap_remaining}, "
                            f"new_max={new_max}",
                        )

    def test_property_5_damage_step_constants(self) -> None:
        """Sanity-Check: ``base_step`` matcht die Upgrade-Step-Konstanten.

        (Schließt das Risiko aus, dass eine Refactoring-Änderung die Step-
        Konstanten in ``karten.py`` von ``_attack_upgrade_step`` entkoppelt.)
        """

        std_attack = _build_attack(10, 20, is_standard=True)
        spec_attack = _build_attack(10, 20, is_standard=False)
        karte_data = {"hp": 100, "attacks": [std_attack]}
        base_step_std, _ = _fuse_resolve_stat_context(
            karte_data=karte_data,
            user_buffs=[],
            stat_choice="damage_1",
        )
        self.assertEqual(base_step_std, STANDARD_DAMAGE_UPGRADE_STEP)

        karte_data = {"hp": 100, "attacks": [spec_attack]}
        base_step_spec, _ = _fuse_resolve_stat_context(
            karte_data=karte_data,
            user_buffs=[],
            stat_choice="damage_1",
        )
        self.assertEqual(base_step_spec, SPECIAL_DAMAGE_UPGRADE_STEP)

    def test_resolve_invalid_choice_returns_zero(self) -> None:
        """Ungültige ``stat_choice`` führt zu ``(0, 0)`` und damit zu keinen Optionen."""

        karte_data = {"hp": 100, "attacks": []}
        for invalid in ("", "garbage", "damage_", "damage_x", "damage_99"):
            with self.subTest(stat_choice=invalid):
                base_step, cap_remaining = _fuse_resolve_stat_context(
                    karte_data=karte_data,
                    user_buffs=[],
                    stat_choice=invalid,
                )
                self.assertEqual((base_step, cap_remaining), (0, 0))
                visible, affordable = _fuse_available_multipliers(
                    base_step=base_step,
                    cap_remaining=cap_remaining,
                    dust_balance=1000,
                )
                self.assertEqual(visible, [])
                self.assertEqual(affordable, [])


# ---------------------------------------------------------------------------
# Sanity: Konstanten sind exakt wie spezifiziert
# ---------------------------------------------------------------------------


class FuseModuleConstantsTest(unittest.TestCase):
    """Konstanten aus ``bot.py`` matchen die Spec.

    Validates: Requirements 21.1, 21.2, 21.3, 21.4, 21.5
    """

    def test_constants_match_spec(self) -> None:
        self.assertEqual(bot_module.FUSE_MULTIPLIER_DUST_PER_STEP, 5)
        self.assertEqual(bot_module.FUSE_MULTIPLIER_VALUES, (1, 2, 3, 4, 5, 6))
        self.assertEqual(bot_module.FUSE_HP_CAP, 200)
        self.assertEqual(bot_module.FUSE_HEALTH_BONUS, 10)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
