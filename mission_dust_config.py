# ============================================================
# mission_dust_config.py
# ------------------------------------------------------------
# Hier stellst du ein, WIE VIEL Infinitydust (Staub) man bei
# einer Mission pro WELLE bekommt.
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
# Der Staub wird AUFADDIERT und erst beim ERFOLGREICHEN
# Abschluss der ganzen Mission ausgezahlt. Bricht man ab oder
# verliert, gibt es nichts.
#
# Beispiel: willst du, dass nur der Boss Staub gibt (3 Stueck),
# setze bei Welle 1-3 "enabled": False und bei Welle 4
# "enabled": True, "amount": 3.
# ============================================================

WAVE_DUST_REWARDS = {
    # Welle 1 (erster Lakei)
    1: {"enabled": True, "amount": 1},
    # Welle 2 (zweiter Lakei)
    2: {"enabled": True, "amount": 1},
    # Welle 3 (dritter Lakei)
    3: {"enabled": True, "amount": 1},
    # Welle 4 (BOSS)
    4: {"enabled": True, "amount": 1},
}

# ------------------------------------------------------------
# Bonus-Staub, wenn man in einer Mission eine Karte als
# Belohnung bekommen wuerde, die man SCHON BESITZT.
#   enabled -> True/False (an/aus)
#   amount  -> wie viel Bonus-Staub
# ------------------------------------------------------------
DAILY_DUPLICATE_BONUS_ENABLED = True
DAILY_DUPLICATE_BONUS_AMOUNT = 1
