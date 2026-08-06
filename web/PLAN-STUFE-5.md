# Stufe 5 — Angriffe bearbeiten, Testlauf, lernender Gegner

Stand: 6. August 2026. Entscheidungen aus der Rückfrage sind eingearbeitet.

Das ist das größte Vorhaben bisher. Es baut auf zwei Dingen auf, die es schon
gibt und die viel Arbeit sparen:

- **`simulation/`** — eine fertige Kampf-Engine (797 Zeilen) mit
  `simulate_duel`, `simulate_matchup`, `simulate_full_round_robin` und einer
  `Strategy`-Schnittstelle samt `build_strategy()`. Genau dort setzen
  Testlauf, Schwierigkeitsgrade und Lernen an.
- **Der Karten-Editor aus Stufe 4** — Abweichungen liegen in der Datenbank
  und wirken ohne Neustart, weil die Karten-Objekte an Ort und Stelle
  geändert werden.

---

## A. Angriffe bearbeiten

Bisher nur Seltenheit, Lebenspunkte, Beschreibung und Bild. Jetzt auch
Angriffe: Name, Schaden, Abklingzeit, Trefferzahl, Heilung, Selbstschaden,
Lebensraub und Nebenwirkungen.

Das Heikle daran: Nebenwirkungen sind verschachtelt und der Kampf reagiert
sofort darauf. Ein falscher Wert fällt nicht beim Speichern auf, sondern
mitten im Spiel.

Deshalb:
- Die vorhandene Prüfung `services/card_validation.py` läuft **vor** dem
  Speichern über die geänderte Karte. Sie kennt die erlaubten Wirkungstypen,
  Ziele und Wertebereiche bereits.
- Nebenwirkungen werden aus einer Liste gewählt, nicht frei getippt.
- Jede Änderung geht in den Verlauf und lässt sich einzeln zurücknehmen.

## B. Kartenansicht: Liste oder Kacheln

- Umschalter zwischen der heutigen Liste und einer Kachelansicht.
- Ein Klick auf eine Kachel öffnet die Karte im Vollbild: links oben ein
  Pfeil zurück, das Kartenbild groß, alle Werte, der Editor darunter.
- **Vorschau-Knopf**: zeigt die Karte so, wie sie im Discord aussieht.
- Die Wahl bleibt gespeichert.

## C. Testlauf

Der Kern: Wie stark ist diese Karte wirklich?

**Schnell (Voreinstellung).** Die Simulation lässt die Karte gegen alle
anderen antreten, mehrere hundert Kämpfe je Paarung. Heraus kommen
Siegquote, durchschnittliche Rundenzahl und gegen wen es besonders gut oder
schlecht läuft.

**Mit KI-Beurteilung.** Die Zahlen gehen ans Sprachmodell, das in Worten
sagt, ob die Karte zu stark, zu schwach oder rund ist — und woran es liegt.

**KI als Gegner (zuschaltbar).** Das Sprachmodell entscheidet Zug für Zug.
Realistischer, aber sehr viel langsamer: jeder Zug ist eine Anfrage, ein
Kampf dauert Minuten statt Millisekunden. Deshalb nur für einzelne
Kontrollkämpfe, nicht für die Massenauswertung.

Alles läuft als Auftrag über die vorhandene Auftragstabelle, mit
Fortschrittsanzeige und Abbruch — ein Durchlauf über alle Karten dauert.

## D. Zwei KI-Modelle getrennt einstellbar

- eines fürs **Prüfen** (Server-Analyse, wie bisher)
- eines für den **Testlauf**

Beide bekommen einen eigenen **Modell-Finder**. Der vorhandene testet auf
eine Verständnisaufgabe; der neue muss etwas anderes können — eine
Kampflage lesen und eine sinnvolle Entscheidung treffen. Also eine eigene
Testaufgabe mit eigener Bewertung.

## E. Der lernende Gegner

**Was mitgeschrieben wird.** Heute steht im Verlauf nur, welcher Angriff
benutzt wurde und wer gewonnen hat — nicht die Lage im Moment der
Entscheidung. Ohne die lässt sich nicht lernen, *warum* jemand so gespielt
hat. Der Bot hält deshalb ab sofort bei jedem Zug fest: Lebenspunkte beider
Seiten, aktive Effekte, Abklingzeiten, verfügbare Angriffe, die getroffene
Wahl und wie der Kampf ausging.

Das heißt auch: **gelernt wird aus Kämpfen nach dem Update.** Die alten sind
dafür zu dünn. Bis genug zusammengekommen ist, spielt der Gegner wie bisher.

**Wie gelernt wird.** Kein undurchschaubares Modell, sondern eine
Gewichtung der vorhandenen Zug-Bewertung (`evaluate_move`): In welchen Lagen
haben erfolgreiche Spieler welche Art von Zug bevorzugt? Daraus werden die
Gewichte angepasst. Nachvollziehbar, überprüfbar, und es fügt sich in die
vorhandene Engine ein, statt sie zu ersetzen.

**Was gelernt werden darf** — eigener Einstellungsbereich, mit Hauptschalter:
- gar nicht
- nur für den Testlauf
- nur für den Gegner im Spiel
- für beides

## F. Gegner-Versionen

Eine Version ist ein benannter Satz Einstellungen: Name, **Beschreibung**,
Gewichte, Fehlerquote, Lernstand.

- Anlegen, **bearbeiten**, umbenennen, löschen, kopieren.
- „Standard" ist fest eingebaut und lässt sich nicht löschen.
- Auf der Website einstellbar **pro Server oder für alle**.
- **Im Discord**: Wer gegen den Bot kämpft, wählt nach dem Klick auf „Bot"
  zwischen Standard und den gespeicherten Versionen — mit der Beschreibung
  als Hilfe. Dafür muss die Kampf-Oberfläche im Bot erweitert werden.

---

## Reihenfolge

Jeder Schritt ist für sich benutzbar.

1. **Angriffe bearbeiten** (A) — baut direkt auf Stufe 4 auf.
2. **Kacheln und Vollbild** (B) — reine Oberfläche, kein Risiko.
3. **Testlauf schnell** (C, erster Teil) — nutzt die Engine, wie sie ist.
4. **Zweites KI-Modell und Finder** (D) — kleine Erweiterung der Einstellungen.
5. **KI-Beurteilung** (C, zweiter Teil) — braucht 3 und 4.
6. **Zug-Mitschrift** (E, erster Teil) — ab hier sammeln sich Daten. Je
   früher das läuft, desto eher ist genug da. Sollte deshalb **vorgezogen**
   werden, sobald 1–3 stehen.
7. **Gegner-Versionen** (F) — Verwaltung auf der Website.
8. **Auswahl im Discord** (F) — Eingriff in die Kampf-Oberfläche des Bots.
9. **Lernen** (E, zweiter Teil) — braucht 6 und genug gesammelte Kämpfe.
10. **KI als Gegner** (C, dritter Teil) — zuletzt, weil am wenigsten
    dringend und am teuersten im Betrieb.

## Was ich dabei beachte

- **Der Kampf im Spiel darf nicht schlechter werden.** Alles Neue ist
  abschaltbar; ist es aus, verhält sich der Bot exakt wie heute.
- **Nichts wird still gelernt.** Der Einstellungsbereich zeigt, woraus
  gelernt wurde und wie viele Kämpfe eingeflossen sind.
- **Testläufe fassen die echte Datenbank nicht an.** Sie rechnen auf Kopien.
- **Jede Kartenänderung bleibt zurücknehmbar**, auch nach dem Testlauf.
