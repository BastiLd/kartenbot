# 📄 Update v2.4.0 — Alternative Designs für Karten

Neu: Karten können bis zu **drei Designs** haben — das normale Bild und zwei alternative.
Ein Design ist **nur ein anderes Aussehen**: Werte, Angriffe und Name der Karte bleiben
genau gleich. Es ist auch keine zusätzliche Karte in der Sammlung.

## 🎨 Neu: `/design`
- Zeigt deine Karten, für die es alternative Designs gibt, mit Vorschaubild.
- Freigeschaltete Designs lassen sich mit **„Übernehmen“** wählen — danach zeigen
  **Sammlung und Kampf** dieses Design. Gesperrte Designs sind mit 🔒 markiert.
- **„Alle zurücksetzen“** stellt alle Karten wieder auf das normale Bild.

## 🗄️ Sammlung
- In der Kartenansicht gibt es den Knopf **„🎨 Design wechseln“**, sobald du für eine Karte
  mindestens zwei Designs wählen kannst. Jeder Klick schaltet zum nächsten.

## ⚔️ Kampf und Mission
- Deine Karte zeigt im Kampf dein gewähltes Design — und dein Gegner sieht es auch.
- In der Kartenauswahl vor Kampf und Mission erinnert die Zeile
  **„🎨 Design ändern: /design“** daran, wenn du Designs zur Wahl hast.

## 🛠️ Für Admins
- **`/design-geben`** und **`/design-entziehen`** schalten ein Design für ein Mitglied frei
  bzw. nehmen es wieder weg. Die Befehle sind nur für Administratoren sichtbar.
- Die Bilder der Designs werden auf der Website beim Helden eingetragen
  (Felder „Design 2“ und „Design 3“).

## ✅ Qualität
- **765 Tests grün** (+ 428 Subtests). Ohne freigeschaltete Designs sieht alles aus wie
  bisher.
