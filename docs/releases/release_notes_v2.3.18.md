# 🛡️ Update v2.3.18 — Sue-Schild repariert, Karten-Blättern im Kampf & /op-verwaltung

Drei von der Community gemeldete Probleme sind behoben: Sue Storms Schild wirkte nicht,
bei vielen Karten ließ sich im Kampf keine zweite Seite öffnen, und im Admin-Menü
fehlten klare Solo/Multi-Optionen.

## 🛡️ Sue Storms „Unsichtbarer Schutz" funktioniert wieder
- Das Schild wurde bisher **nur verbraucht, wenn ein menschlicher Spieler angriff**. Beim
  **Bot-Angriff** (1v1 gegen den Bot) und in **Missionen** wurde das Schild komplett ignoriert —
  der Treffer ging voll durch („Schild funktioniert nicht"). Jetzt absorbiert das Schild den
  Schaden in **allen** Kampf-Varianten (Spieler- und Bot-Zug, PvP und Mission), inklusive des
  12er-Rückschadens beim Zerbrechen.
- In Missionen wurde das Schild zudem **gar nicht erst gesetzt** — auch das ist behoben.
- **Wert an den Text angeglichen:** Der Text sagt „bis zu **30** Schaden", intern lag aber ein
  Zufallsbereich von 25-35 (daher die gemeldeten 33/34). Das Schild absorbiert jetzt **feste 30**.

## 📄 „Weiter"-Button bei der Karten-Auswahl im Kampf
- Wer **mehr als 25 verschiedene Karten** besitzt, sah bei der Kampf-Karten-Auswahl bisher nur die
  ersten 25 — die restlichen Karten waren **nicht erreichbar**. Die Auswahl hat jetzt
  **Zurück/Weiter-Buttons** und blättert seitenweise durch alle Karten.

## 🛠️ /op-verwaltung: klare Solo- und Multi-Optionen
- Das Menü bietet jetzt **„card give (Solo)"** und **„card give (Multi)"** als eigene Einträge —
  Karten direkt an einen oder an mehrere Nutzer geben, ohne Zwischenabfrage. Alle übrigen Aktionen
  (card remove, Gruppen geben/entfernen, Nutzer-/Rollen-Freigabe) bleiben unverändert und wurden
  geprüft.

## ✅ Qualität
- **408 Tests grün** (+ 428 Subtests) — inkl. neuer Regressionstests für den Schild-Verbrauch beim
  Bot-Angriff, den festen 30er-Schildwert, die Karten-Blätterfunktion und die neuen Menüpunkte.
