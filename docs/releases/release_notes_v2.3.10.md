# 🗂️ Update v2.3.10 — `/verbessern`: Multiplikator-Auswahl wird wieder angezeigt

## 🐛 Fix: Verstärkung wurde sofort beim Klick angewendet
Beim `/verbessern` wurde **Leben** (oder eine Attacke) **sofort um 1 Schritt
verstärkt**, sobald man die Stat angeklickt hat – man konnte also **kein 2×/3×
usw.** mehr wählen.

Ursache: Nach der Stat-Auswahl (`BuffTypeSelect`) wurde direkt 1× (5 Dust)
angewendet, statt die bereits vorhandene **Multiplikator-Auswahl**
(`FuseMultiplierView`) zu öffnen.

**Jetzt wieder korrekt:**
1. `/verbessern` → Karte wählen
2. Stat wählen (**Leben** oder eine Attacke)
3. **Multiplikator wählen: 1× = 5 · 2× = 10 · 3× = 15 · … · 6× = 30 Dust**
4. Bestätigung mit Vorher/Nachher-Werten und verbrauchtem Dust

Der Multiplikator wird weiterhin dynamisch nach **Stat-Cap** (z. B. HP-/Schadens-
Limit) und **Dust-Saldo** gefiltert; zu teure Stufen bleiben sichtbar, aber nicht
wählbar.

## ✅ Tests
- Gesamte Testsuite grün (367 passed). Die Options-Tests für `BuffTypeSelect`
  bleiben unverändert gültig (Stat-Optionen unverändert, nur der Folge-Schritt
  führt jetzt wieder über die Multiplikator-Auswahl).
