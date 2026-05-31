# ============================================================
# mission_dust_config.py
# ------------------------------------------------------------
# Hier stellst du ein, WANN und WIE VIEL Infinitydust (Staub)
# man bei einer Mission bekommt.
#
# WICHTIG: Es geht um die WELLE, NICHT um einzelne Lakeien.
#   Welle 1 = erster Lakei-Kampf
#   Welle 2 = zweiter Lakei-Kampf
#   Welle 3 = dritter Lakei-Kampf
#   Welle 4 = BOSS-Kampf
#
# Pro Welle kannst du zwei Dinge einstellen:
#   "enabled" -> True  = es gibt Staub fuer diese Welle
#                False = es gibt KEINEN Staub fuer diese Welle
#   "amount"  -> wie viel Staub (ganze Zahl, z. B. 1, 2, 3 ...)
#
# AUSZAHLUNG:
#   * Staub aus Welle 1-3 wird AUFADDIERT und in der Pause NACH
#     Welle 3 sofort ausgezahlt (zusammen mit der Unit-Belohnung).
#     Wer Welle 3 schafft, bekommt diesen Staub also sicher -
#     auch wenn er danach am Boss verliert.
#   * Staub aus Welle 4 (Boss) wird erst beim Mission-Erfolg
#     ausgezahlt.
#
# STANDARD (v2.3.5):
#   Welle 1+2: kein Staub.
#   Welle 3  : 1 Staub  -> "alle 3 Wellen geschafft = 1 Staub + 1 Unit".
#   Welle 4  : kein Basis-Staub. Beim Boss gibt es nur dann Staub,
#              wenn die Belohnungs-Karte schon im Besitz war
#              (Duplikat -> 1 Staub, siehe check_and_add_karte).
# ============================================================

WAVE_DUST_REWARDS = {
    # Welle 1 (erster Lakei) - kein Staub
    1: {"enabled": False, "amount": 0},
    # Welle 2 (zweiter Lakei) - kein Staub
    2: {"enabled": False, "amount": 0},
    # Welle 3 (dritter Lakei) - 1 Staub fuer "alle 3 Wellen geschafft"
    3: {"enabled": True, "amount": 1},
    # Welle 4 (BOSS) - kein Basis-Staub (nur Duplikat-Karte gibt Staub)
    4: {"enabled": False, "amount": 0},
}

# ------------------------------------------------------------
# Bonus-Staub-Akkumulator fuer eine bereits besessene Reward-Karte.
#
# HINWEIS: Der Staub fuer eine doppelte Boss-/Reward-Karte wird
# bereits direkt in check_and_add_karte() vergeben (+1 Staub).
# Damit es KEINE Doppel-Zaehlung gibt, ist dieser Akkumulator-Bonus
# standardmaessig AUS. Nicht aktivieren, solange check_and_add_karte
# den Duplikat-Staub vergibt.
#   enabled -> True/False (an/aus)
#   amount  -> wie viel zusaetzlicher Bonus-Staub
# ------------------------------------------------------------
DAILY_DUPLICATE_BONUS_ENABLED = False
DAILY_DUPLICATE_BONUS_AMOUNT = 0
