# Ausbauplan — alles vom Bot auf die Website, und mehr

Stand: 6. August 2026. Grundlage: Vergleich aller Bot-Befehle und
Entwicklerpanel-Aktionen mit dem, was die Website heute kann.

Das Ziel in einem Satz: **Nichts soll nur über einen Discord-Befehl gehen.**
Und darüber hinaus Dinge, die im Chat gar nicht sinnvoll wären.

---

## Wo wir stehen

Die Website deckt heute ab: Übersicht, Spieler (Karten/Dust/Units geben und
nehmen), Kartenliste, Statistiken, Rollen und Mitglieder, Server-Analyse,
Bot-Steuerung, Einstellungen.

Was der Bot kann und die Website noch **nicht**:

| Bereich | Fehlt auf der Website |
|---|---|
| Währung | Units haben kein Protokoll und keine Massenvergabe (Dust schon) |
| Kanäle | Freigaben (`/kanal-freigeben`, `/konfigurieren`) |
| Karten | Varianten gezielt vergeben, Seltenheitsgruppen, Kartenprüfung |
| Spieler | Sammlung ansehen, Tageslimit zurücksetzen, Missionszähler |
| Einladungen | Übersicht, Höchstalter des Kontos (`/invite-limit`) |
| Nachrichten | Sichtbarkeit je Befehl (öffentlich oder nur für dich) |
| Wartung | Datenbank sichern, Integrität prüfen, Befehle neu anmelden |
| Berichte | Excel-Ausgabe aller Statistiken (`/stats_e`) |

---

## Reihenfolge

Nach Nutzen pro Aufwand sortiert. Jede Stufe ist für sich fertig und
benutzbar — kein „geht erst am Ende".

### Stufe 1 — Lücken schließen (klein, sofort spürbar)

1. **Units wie Dust behandeln.** Eigenes Protokoll, Massenvergabe, im
   Verlauf sichtbar. Heute gibt es Units nur durchs Spielen.
2. **Kanal-Freigaben.** Liste aller Kanäle mit Häkchen statt Befehlen.
   Zeigt gleich mit, wo der Bot nicht schreiben darf.
3. **Sammlung ansehen.** Was jemand besitzt, ohne den Umweg über Discord.
4. **Einladungen.** Wer wen geworben hat, plus das Höchstalter des Kontos.

### Stufe 2 — Bedienung (macht den Alltag leichter)

5. **Rückgängig überall.** Es gibt schon eine Einstellung dafür, aber sie
   wirkt noch nicht überall. Jede Aktion bekommt ein Zeitfenster zum
   Zurücknehmen — auch Karten und Währung, nicht nur Rollen.
6. **Mehrere Personen auf einmal.** Wie im Rollen-Bereich: mehrere anhaken,
   eine Aktion. Dazu Einfügen einer Liste (IDs oder Namen, untereinander).
7. **Spielerprofil.** Eine Seite pro Person mit allem: Karten, Währungen,
   Kämpfe, Missionen, Einladungen, Rollen, Verlauf, Einordnung aus der
   Analyse. Ersetzt das Zusammensuchen über vier Bereiche.
8. **Handy-Ansicht.** Die Seite läuft am Handy, ist aber für den großen
   Bildschirm gebaut. Eigene Runde für Tabellen, Listen und Dialoge.

### Stufe 3 — Wartung und Sicherheit

9. **Datenbank sichern.** Herunterladen mit einem Klick, dazu die
   Integritätsprüfung aus dem Entwicklerpanel.
10. **Papierkorb.** Gelöschte Spielerdaten bleiben 30 Tage
    wiederherstellbar. Heute ist Löschen endgültig.
11. **Sicherungspunkt vor riskanten Aktionen.** Vor einer Massenvergabe
    automatisch festhalten, was vorher war — damit „rückgängig" auch bei
    hundert Personen noch möglich ist.
12. **Excel-Bericht.** Was `/stats_e` per Nachricht schickt, als
    Herunterladen.

### Stufe 4 — Karten-Editor (das große Stück)

13. Karten, Angriffe, Seltenheiten und Varianten liegen heute fest im Code.
    Jede Änderung braucht einen Entwickler und einen Neustart.

    Der Editor macht daraus: anlegen, ändern, Vorschau wie im Discord,
    Prüfung vor dem Speichern, Versionsverlauf mit Rücknahme.

    **Ehrlich zum Aufwand:** Das ist die größte Einzelaufgabe im Plan. Der
    Bot liest die Karten beim Start aus einer Python-Datei. Damit Änderungen
    ohne Neustart wirken, muss diese Quelle in die Datenbank wandern — ohne
    dass die bestehenden Karten dabei kaputtgehen. Das ist machbar, aber es
    ist kein Nachmittag.

### Stufe 5 — Dinge, die es im Chat gar nicht gäbe

Vorschläge von mir, weil sie über eine Website natürlich sind und über
Befehle unmöglich:

14. **Vorlagen.** Häufige Abläufe einmal festlegen („Starterpaket": drei
    Karten, 50 Dust, Rolle Neuling) und mit einem Klick anwenden.
15. **Geplante Aktionen.** Etwas für später vormerken — eine Rolle zum
    Ereignis, Dust zum Monatsanfang.
16. **Zwei Spieler vergleichen.** Nebeneinander, für Streitfälle.
17. **Suchen über alles.** Ein Feld für Personen, Karten, Rollen, Kanäle,
    Einstellungen. Die Tastenkombination gibt es schon, den Inhalt noch nicht.
18. **Aufmerksam werden.** Wenn der Bot offline geht, sich Fehler häufen
    oder jemand auffällig wird — Hinweis auf der Seite statt Zufallsfund
    im Protokoll.
19. **Aktivitätskalender.** Wann ist auf dem Server was los, über Monate.
20. **Wer hat was gemacht.** Das Protokoll gibt es, aber ohne Filter. Nach
    Person, Zeitraum und Art durchsuchbar machen.

---

## Was ich dabei beachte

- **Jede Aktion bleibt nachvollziehbar.** Alles landet im Protokoll, mit
  Person, Zeitpunkt und Auslöser.
- **Nichts wird still gelöscht.** Wo etwas verschwindet, gibt es einen
  Papierkorb oder eine Rücknahme.
- **Der Bot bleibt der Ausführende.** Discord-Aktionen laufen weiter über
  die Auftragstabelle, damit die Rechteprüfung des Bots greift.
- **Kritisches bleibt aufs Heimnetz beschränkt.** Rollen, Rauswurf und Bann
  nur von dort — das ist schon so und bleibt.

---

## Reihenfolge-Abhängigkeiten

- Der **Karten-Editor** (13) setzt den Versionsverlauf voraus, der auch für
  **Rückgängig** (5) gebraucht wird — deshalb kommt 5 vorher.
- **Vorlagen** (14) und **geplante Aktionen** (15) bauen auf der
  Massenvergabe (6) auf.
- **Papierkorb** (10) und **Sicherungspunkt** (11) teilen sich denselben
  Unterbau und gehören zusammen gebaut.
