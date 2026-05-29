# ============================================================
# namenconfig.py — globale Feature-Toggles für v2.3.0
# Änderungen wirken nach Bot-Neustart.
# ============================================================

# ------------------------------------------------------------
# boss_switch_enabled
# ------------------------------------------------------------
# Wirkung: Steuert die Frage „Held wechseln?" vor Boss-Kämpfen.
# True  -> Spieler bekommt vor jedem Boss eine Auswahl aus
#          ALLEN seinen Karten, kann frisch starten.
# False -> Keine Frage, Boss-Kampf startet direkt mit der
#          aktuellen Mission-Karte.
# Erlaubt: True | False
# Default: True
# Beispiel ON  : "Wähle deinen Helden für den Boss-Kampf:"
# Beispiel OFF : (kein Menü; Mission-Karte tritt direkt an)
boss_switch_enabled = True

# ------------------------------------------------------------
# name_normalization_enabled
# ------------------------------------------------------------
# Wirkung: Markdown-aktive Zeichen in Benutzernamen (_, *, ~,
#          `, >, |) werden in Embeds, Buttons, Selects und
#          Pings so dargestellt, dass sie als wörtliche Zeichen
#          sichtbar bleiben (nicht als Markdown interpretiert).
# Erlaubt: True | False
# Default: True
# Beispiel ON  : MFU-_-is_da   (Zeichen sichtbar wie eingegeben)
# Beispiel OFF : MFU-\_-is\_da (Backslashes sichtbar / Italic)
name_normalization_enabled = True

# ------------------------------------------------------------
# Platzhalter — NOCH NICHT AKTIV in v2.3.0
# ------------------------------------------------------------
# Wird in einem späteren Update aktiviert. Für die Karten-Namen
# gibt es derzeit keine bekannten Markdown-Probleme. Der Block
# bleibt auskommentiert, damit du später nur das `#` entfernen
# musst.
#
# card_name_normalization_enabled = False
