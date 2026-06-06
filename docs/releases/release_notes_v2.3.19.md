# 📄 Update v2.3.19 — Karten-Blättern jetzt auch beim Kampf-Start

Nachtrag zu v2.3.18: Die Blätter-Buttons fehlten noch an einer zweiten Stelle.

## 📄 „Weiter"-Button auch bei der eigenen Karten-Auswahl im /kampf
- In v2.3.18 wurde die Auswahl des **Herausgeforderten** repariert. Die Auswahl des
  **Herausforderers** (wer `/kampf` startet und „Wähle deine Karte für den 1v1-Kampf:" sieht)
  nutzt jedoch eine **andere** Auswahl-Ansicht (`CardSelectView`) — dort wurden weiterhin nur die
  ersten 25 Karten gezeigt, ohne Blätter-Buttons. Bei mehr als 25 Karten waren die restlichen
  **nicht erreichbar**.
- Diese Auswahl hat jetzt ebenfalls **Zurück/Weiter-Buttons** und den Seiten-Hinweis
  „Seite X/Y – Karte wählen…", genau wie die Karten-Auswahl in `/sammlung`.

## ✅ Qualität
- **411 Tests grün** (+ 428 Subtests) — inkl. neuer Regressionstests für die Blätterfunktion der
  Herausforderer-Karten-Auswahl.
