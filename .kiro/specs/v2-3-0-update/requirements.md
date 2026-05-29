# Requirements Document

## Introduction

Diese Spezifikation beschreibt das Sammel-Update **v2.3.0** für den Karten-Discord-Bot ("Karten" / Marvel-Card-Game). Das Update bündelt mehrere unabhängige Verbesserungen aus den Bereichen Missionen, Entwickler-Tools, Aufwertungs-System (`/verbessern`), Belohnungen, UI-Konsistenz, Kampf-Lifecycle (Cancel + AFK-System), Boss-Balance sowie eine zentrale Konfigurationsdatei für globale Toggles.

Ziel ist es, alle Punkte konsistent in einer einzigen Release-Version `v2.3.0` umzusetzen, danach committen, pushen und taggen (Branch: `main`).

## Glossary

- **Bot**: Der Discord-Bot in diesem Repository (Karten / Marvel-Card-Game).
- **Karte / Held**: Eine spielbare Sammelkarte mit HP, Angriffen (Damage-Werten) und ggf. Spezialfähigkeiten.
- **Lakei**: Gegner-Minion in einer Mission (Welle 1–3 vor dem Boss).
- **Boss**: Endgegner einer Mission, der nach allen Lakeien erscheint.
- **Welle**: Eine Stufe in einer Mission (Welle 1, 2, 3 = Lakei-Stufen; Boss = finaler Encounter).
- **Mission**: Vom Bot bereitgestellte Kampagne aus mehreren Wellen + Boss (z. B. Maestro, MODOK, Green Goblin, Kingpin, Agatha).
- **Dust**: Allgemeine Spielwährung zum Aufwerten von Karten (über `/verbessern`).
- **Lödust**: Spezielle Dust-Variante (Lö-Dust) mit eigener Mechanik im Bot.
- **Infinitydust**: Spezielle Premium-Dust-Variante, die als zusätzliche Belohnung für Mission-Encounter ausgezahlt wird.
- **Daily-Karte**: Tägliche Karte, die einem Spieler einmal pro Tag angeboten wird.
- **`/karte_geben`**: Slash-Command, der einer Person eine Karte gibt.
- **`/verbessern`**: Slash-Command, mit dem Spieler ihre Karten gegen Dust aufwerten.
- **Entwickler-Panel / Dev-Tools**: Admin-UI im Bot mit Funktionen wie *Grant Card*, *Dust geben*, *Maintenance/Beta/Alpha Mode*.
- **Grant Card**: Funktion im Entwickler-Panel, die einer Person eine Karte gibt — entspricht inhaltlich `/karte_geben`.
- **Multi-Mode / Single-Mode**: Schalter, ob eine Aktion einzeln oder in Mehrfach-Ausführung läuft (z. B. mehrere Karten / mehrere Dust-Beträge auf einmal).
- **Maintenance / Beta / Alpha Mode**: Globale Bot-Modi, schaltbar im Entwickler-Panel.
- **Thumbnail**: Kleines Vorschaubild oben rechts in einem Discord-Embed (im Gegensatz zum großen `image` unten).
- **Challenge**: Eine offene Kampf-Anfrage zwischen zwei Spielern (Challenger = Herausforderer, Acceptor = Herausgeforderter).
- **Thread**: Discord-Thread, in dem ein einzelner Kampf abläuft.
- **AFK-Markierung**: Ping (`@user`) im Kampf-Thread an Spieler, die zu lange nicht reagieren.
- **Runde**: Eine Kampfrunde, in der genau ein Spieler am Zug ist; Rundenwechsel = der jeweils andere Spieler ist dran.
- **Cooldown (CD)**: Anzahl Runden, in denen eine Fähigkeit nach Nutzung nicht erneut gewählt werden kann.
- **Cap / Stat-Cap**: Maximalwert, den ein einzelner Stat (HP, Damage einer Attacke) durch Aufwertung erreichen darf.
- **Konfigurationsdatei (`namenconfig.py`)**: Neue Datei im Repo-Root, die globale Toggles für dieses Update zentral hält (insbesondere Boss-Wechsel-Toggle und Namens-Normalisierung). Karten-Namens-Normalisierung ist nicht Teil von v2.3.0 und wird in einem späteren Spec separat behandelt; ein auskommentierter Platzhalter dafür wird in der Datei vorbereitet.

---

## Requirements

### Requirement 1: Boss-Karten-Wechsel mit voller Auswahl

**User Story:** Als Spieler möchte ich beim Erreichen eines Bosses zwischen ALLEN Karten in meinem Besitz wählen können (nicht nur der zu Mission-Beginn gewählten), damit ich strategisch auf den Boss reagieren kann.

#### Acceptance Criteria

1. WENN ein Spieler in einer Mission den Boss-Encounter erreicht UND der globale Toggle `boss_switch_enabled` auf `True` steht, DANN SOLL DER Bot eine Wechsel-Auswahl anzeigen, die ALLE Karten im Besitz des Spielers enthält.
2. DAS Bot-Wechsel-Menü SOLL die ursprünglich zu Mission-Beginn gewählte Karte als Option enthalten (nicht ausschließen).
3. WENN der Spieler im Wechsel-Menü eine andere Karte als die ursprüngliche auswählt, DANN SOLL DER Bot einen frisch gestarteten Kampf gegen den Boss beginnen, bei dem der Karten-Zustand (HP, Cooldowns, temporäre Buffs/Debuffs) der gewählten Karte vollständig zurückgesetzt ist.
4. WENN der Spieler im Wechsel-Menü die ursprüngliche Karte beibehält, DANN SOLL DER Bot den Kampf mit dem aktuellen Zustand dieser Karte fortsetzen.
5. DAS Bot-Wechsel-Menü SOLL ausschließlich bei Boss-Encountern erscheinen, NICHT bei Lakei-Encountern.
6. FALLS der globale Toggle `boss_switch_enabled` auf `False` steht, DANN SOLL DER Bot die Wechsel-Frage gar nicht anzeigen und direkt den Boss-Kampf mit der aktuellen Karte starten.
7. FALLS der Bot fälschlicherweise einen kombinierten Boss-und-Lakei-Encounter erkennt, DANN SOLL DER Bot den Lakei-Encounter priorisieren und das Wechsel-Menü ausblenden.

---

### Requirement 2: Globale Konfigurationsdatei `namenconfig.py` für Feature-Toggles

**User Story:** Als Entwickler möchte ich alle neuen globalen Schalter zentral in einer kommentierten Datei `namenconfig.py` pflegen, damit ich Verhalten ohne Code-Änderungen umstellen kann.

#### Acceptance Criteria

1. DAS Repository SOLL im Repo-Root eine neue Datei mit Namen `namenconfig.py` enthalten, die alle in dieser Spezifikation eingeführten globalen Feature-Toggles bündelt.
2. DIE Datei `namenconfig.py` SOLL mindestens folgende Einträge in dieser Reihenfolge enthalten:
   1. `boss_switch_enabled` (Bool, Default: `True`) — steuert Requirement 1.
   2. `name_normalization_enabled` (Bool, Default: `True`) — steuert Requirement 9 (Spezialzeichen in Benutzernamen).
3. DIE Datei `namenconfig.py` SOLL einen auskommentierten, deaktivierten Platzhalter-Block für eine zukünftige Karten-Namens-Normalisierung enthalten (Beispiel-Konstante `# card_name_normalization_enabled = False`), inkl. Kommentar „wird in einem späteren Update aktiviert"; dieser Platzhalter darf in v2.3.0 keinen funktionalen Effekt haben und SOLL beim Bot-Start ignoriert werden.
4. JEDER aktive Eintrag in `namenconfig.py` SOLL einen Kommentarblock unmittelbar darüber enthalten, der mindestens enthält: (a) eine Beschreibung der Wirkung des Schalters, (b) die zulässigen Werte und Default, (c) ein konkretes „so sieht es im Bot aus, wenn ON" / „so sieht es aus, wenn OFF"-Beispiel.
5. WENN ein Toggle in `namenconfig.py` geändert wird, DANN SOLL DER Bot beim nächsten Start den neuen Wert lesen.
6. FALLS `namenconfig.py` fehlt oder ein Eintrag fehlt, DANN SOLL DER Bot den Default-Wert verwenden und eine Warnung in den Bot-Log schreiben, ohne den Start zu blockieren.
7. FALLS ein Eintrag einen ungültigen Wert (z. B. kein Bool) enthält, DANN SOLL DER Bot den Eintrag verwerfen, den Default-Wert verwenden und eine Warnung in den Bot-Log schreiben.

---

### Requirement 3: Multi/Single-Modus für `/karte_geben` (Verifikation und Verbesserung)

**User Story:** Als Admin möchte ich, dass `/karte_geben` zuverlässig sowohl im Single- als auch im Multi-Modus funktioniert, damit ich Karten effizient verteilen kann.

#### Acceptance Criteria

1. DAS Bot-`/karte_geben`-Kommando SOLL einen Single-Modus anbieten, der genau eine Karte an genau eine Zielperson vergibt.
2. DAS Bot-`/karte_geben`-Kommando SOLL einen Multi-Modus anbieten, der mehrere Karten in einem Vorgang an dieselbe Zielperson vergibt.
3. WENN der Multi-Modus genutzt wird, DANN SOLL DER Bot in einer einzigen Bestätigungsnachricht zusammenfassen, welche Karten vergeben wurden und in welcher Anzahl.
4. FALLS während eines Multi-Vorgangs eine einzelne Karten-Vergabe fehlschlägt, DANN SOLL DER Bot die fehlgeschlagene Karte einzeln in der Bestätigung aufführen und die übrigen Vergaben dennoch abschließen.
5. DAS Bot-`/karte_geben`-Verhalten SOLL durch Tests abgedeckt sein, die sowohl Single- als auch Multi-Pfade prüfen.

---

### Requirement 4: Multi/Single-Modus für „Dust geben" im Entwickler-Panel

**User Story:** Als Admin möchte ich Dust an Spieler im Entwickler-Panel sowohl einzeln als auch im Multi-Modus vergeben können, analog zu `/karte_geben`.

#### Acceptance Criteria

1. DAS Entwickler-Panel SOLL für „Dust geben" einen Single-Modus und einen Multi-Modus anbieten.
2. DAS Entwickler-Panel SOLL die Auswahl Single/Multi nach demselben UI-Muster wie `/karte_geben` darstellen (gleiche Button- und Embed-Struktur).
3. WENN der Multi-Modus für „Dust geben" gewählt wird, DANN SOLL DER Bot die Möglichkeit bieten, in einem Vorgang mehrere Dust-Vergaben (an dieselbe oder unterschiedliche Personen, je nach bestehender UI-Logik) zu konfigurieren und gemeinsam zu bestätigen.
4. DAS Bot-„Dust geben"-Verhalten SOLL durch Tests für Single- und Multi-Pfad abgedeckt sein.

---

### Requirement 5: Multi/Single-Modus für Lödust

**User Story:** Als Admin/Spieler möchte ich Lödust ebenfalls in Single- und Multi-Modus verarbeiten können, mit fester Schrittweite oder freier Eingabe.

#### Acceptance Criteria

1. DAS Bot-Lödust-Modul SOLL einen Single-Modus und einen Multi-Modus anbieten.
2. WENN der Multi-Modus für Lödust gewählt wird, DANN SOLL DER Bot eine Auswahl der Beträge `{5, 10, 15, 20, 25, 30}` als Schnellauswahl-Buttons anbieten.
3. DAS Bot-Lödust-Multi-Modus-UI SOLL zusätzlich ein freies Eingabefeld am oberen Rand bereitstellen, in dem ein beliebiger ganzzahliger Betrag eingegeben werden kann.
4. FALLS der eingegebene Lödust-Betrag negativ oder Null ist, DANN SOLL DER Bot eine Fehlermeldung zurückgeben und die Aktion vollständig verhindern (keine Ausführung).
5. FALLS der Spieler im freien Eingabefeld Null eingibt UND gleichzeitig einen Schnellauswahl-Button aktiviert hat, DANN SOLL DER Bot den Wert des Schnellauswahl-Buttons verwenden und die Null-Eingabe ignorieren.
6. WENN ein bestehender Multi-Mechanismus für Lödust schon existiert, DANN SOLL diese Implementierung gegen die Anforderungen 5.1–5.5 verifiziert und bei Abweichungen angepasst werden.

---

### Requirement 6: Überarbeitung `/verbessern` (variable Multiplikatoren, paginiertes Menü, Dust-Anzeige)

**User Story:** Als Spieler möchte ich beim Aufwerten meiner Karten frei wählen können, wie viel Dust ich pro Aufwertungsschritt einsetze, damit ich Aufwertungen effizient bündeln kann.

#### Acceptance Criteria

1. DAS Bot-`/verbessern`-Kommando SOLL die folgenden Dust-Multiplikator-Stufen anbieten:
   - 5 Dust = 1× (entspricht der bisherigen normalen Aufwertung)
   - 10 Dust = 2×
   - 15 Dust = 3×
   - 20 Dust = 4×
   - 25 Dust = 5×
   - 30 Dust = 6×
2. DAS Bot-`/verbessern`-Modul SOLL die Basiswerte pro Aufwertungsstufe (HP- und Damage-Zugewinn pro 1×-Stufe) gegenüber der bisherigen Implementierung unverändert lassen; höhere Multiplikatoren skalieren diesen Basiswert linear (z. B. 3× = 3 × Basiswert in einem Vorgang).
3. WENN der Spieler `/verbessern` ausführt, DANN SOLL DER Bot zuerst die Karten-Auswahl anzeigen und ausschließlich das untere Karten-Menü mit allen Karten im Besitz nutzen; das obere Such-/Show-All-Menü SOLL entfernt werden.
4. DAS Bot-Karten-Auswahl-Menü in `/verbessern` SOLL „Nächste Seite"- und „Vorherige Seite"-Buttons anbieten, wenn die Anzahl der Karten die Discord-Listengrenze überschreitet. Diese Pagination gilt ausschließlich in der Karten-Auswahlphase, nicht in nachfolgenden Schritten (Stat-Auswahl, Multiplikator-Auswahl).
5. NACH der Karten-Auswahl SOLL DER Bot zuerst eine Auswahl anzeigen, WAS aufgewertet wird (z. B. HP, Damage Attacke 1, Damage Attacke 2 …) — und ERST DANN den Multiplikator-Schritt.
6. DAS Bot-Multiplikator-Menü SOLL ausschließlich diejenigen Multiplikatoren anbieten, die unter Berücksichtigung des Stat-Caps der gewählten Karte und des gewählten Stats noch zulässig sind.
7. FALLS für einen gewählten Stat nur noch z. B. 3 Aufwertungs-Schritte bis zum Cap möglich sind, DANN SOLL DER Bot maximal 3× (15 Dust) als höchste Option anzeigen, und die Optionen 4×/5×/6× ausblenden.
8. WENN ein Aufwertungs-Schritt HP betrifft, DANN SOLL DER Bot den HP-Cap von 200 niemals überschreiten lassen — diese Cap-Beschränkung gilt global an JEDER Stelle der Aufwertungs-Pipeline (Stat-Auswahl, Multiplikator-Filterung, finale Anwendung), nicht nur bei der Multiplikator-Auswahl.
9. FALLS der Spieler so wenig Dust besitzt, dass eine Multiplikator-Option nicht mehr finanzierbar ist, DANN SOLL DER Bot diese Option als nicht wählbar markieren (z. B. ausgegraut), aber sichtbar lassen — die Option SOLL nicht ausgeblendet werden.
10. VOR der Auswahl des Dust-Betrags SOLL DER Bot anzeigen, wie viel Dust der Spieler aktuell besitzt.
11. NACH einer erfolgreichen Aufwertung SOLL DER Bot die neuen Stat-Werte und den verbleibenden Dust-Stand bestätigen.

---

### Requirement 7: Infinitydust-Belohnungen für Missionen und Daily-Karten

**User Story:** Als Spieler möchte ich für jeden besiegten Gegner und für eine bereits besessene Daily-Karte zusätzlich Infinitydust erhalten, damit Missionen und Daily-Aktivität mehr Mehrwert haben.

#### Acceptance Criteria

1. WENN ein Spieler in einer Mission einen Lakei besiegt, DANN SOLL DER Bot die Infinitydust-Belohnung um genau 1 erhöhen (akkumuliert über die Mission).
2. WENN ein Spieler in einer Mission einen Boss besiegt, DANN SOLL DER Bot die Infinitydust-Belohnung um genau 1 erhöhen.
3. WENN ein Spieler eine Daily-Karte erhält und diese Karte bereits in seinem Besitz ist, DANN SOLL DER Bot dem Spieler 1 Infinitydust gutschreiben.
4. DIE in 7.1–7.3 beschriebene Infinitydust-Belohnung SOLL zusätzlich zu den bisherigen Belohnungen ausgezahlt werden, NICHT als Ersatz.
5. DIE Mission-Infinitydust-Belohnung SOLL erst beim erfolgreichen Abschluss der Mission gutgeschrieben werden (nicht inkrementell pro Encounter).
6. WENN eine Mission abgebrochen oder vom Spieler verloren wird, DANN SOLL DER Bot die in dieser Mission akkumulierte Infinitydust-Belohnung NICHT auszahlen.
7. FÜR eine vollständig abgeschlossene Standard-Mission (3 Lakeien + 1 Boss) ergibt sich folgende Infinitydust-Auszahlung:
   - Alle 3 Lakeien besiegt: +3 Infinitydust.
   - Boss besiegt: +1 Infinitydust.
   - Boss besiegt UND der Spieler besaß die als Belohnung verknüpfte Daily-Karte bereits: zusätzliche +1 Infinitydust (zusammen mit dem Boss-Punkt also +2 für den Boss-Schritt).
   - Maximaler Gesamt-Infinitydust pro voll abgeschlossener Standard-Mission: 5.
8. DIE Daily-Karten-Duplikat-Belohnung aus 7.3 (Daily außerhalb einer Mission) SOLL unabhängig vom Mission-Lifecycle sofort gutgeschrieben werden.

---

### Requirement 8: Bilder ausschließlich als Thumbnail

**User Story:** Als Spieler möchte ich, dass Karten- und Dust-Bilder konsistent klein als Thumbnail oben rechts erscheinen, damit Nachrichten kompakt bleiben.

#### Acceptance Criteria

1. DAS Bot-UI SOLL Karten-Bilder ausschließlich im `thumbnail`-Slot eines Embeds setzen, niemals im großen `image`-Slot.
2. DAS Bot-UI SOLL Dust-Bilder (inkl. Infinitydust und Lödust) ausschließlich im `thumbnail`-Slot eines Embeds setzen, niemals im großen `image`-Slot.
3. WENN ein Spieler eine Daily-Karte erhält, die er bereits besitzt, DANN SOLL DER Bot das Dust-Bild im Thumbnail-Slot anzeigen.
4. DAS Bot-Verhalten in 8.1–8.2 SOLL für ALLE Befehle und UI-Flows gelten (Mission, Daily, `/karte_geben`, Dev-Tools, Lödust, etc.) — ohne Ausnahmen für Debug- oder Entwickler-Werkzeuge.

---

### Requirement 9: Spezialzeichen in Benutzernamen visuell normalisieren

**User Story:** Als Spieler möchte ich, dass Benutzernamen wie `MFU-_-is_da` überall korrekt angezeigt werden und nicht durch Discord-Markdown verfälscht (`MFU-\_-is\_da`) erscheinen.

> **Hinweis:** Dieses Requirement deckt ausschließlich Benutzernamen ab. Eine analoge Normalisierung für Karten-Namen ist nicht Teil von v2.3.0 und wird bei Bedarf in einem späteren Spec separat behandelt; in `name_config.py` existiert dafür ein auskommentierter Platzhalter-Block (siehe Requirement 2.3).

#### Acceptance Criteria

1. WENN der Bot einen Benutzernamen in einem Embed, einer Nachricht oder einem Menü anzeigt UND `name_normalization_enabled` auf `True` steht, DANN SOLL DER Bot Markdown-aktive Zeichen (`_`, `*`, `~`, `` ` ``, `>`, `|`) so behandeln, dass sie als wörtliche Zeichen sichtbar sind und nicht als Markdown interpretiert werden.
2. DAS Bot-Verhalten aus 9.1 SOLL in jeder UI-Komponente konsistent sein: Embeds, Plain-Text-Nachrichten, Select-Menüs, Buttons-Labels, AFK-Pings.
3. FALLS `name_normalization_enabled` auf `False` steht, DANN SOLL DER Bot die Benutzernamen ohne Normalisierung wie bisher ausgeben — auch dann, wenn dies dazu führt, dass Discord-Markdown den Namen optisch verändert.
4. DIE Normalisierung SOLL den Anzeigetext nicht inhaltlich verändern; sie SOLL ausschließlich die Markdown-Interpretation neutralisieren (sichtbare Zeichen, gleiche Reihenfolge, gleiche sichtbare Länge).

---

### Requirement 10: Bestätigung mit Statusanzeige für Maintenance/Beta/Alpha-Modus

**User Story:** Als Admin möchte ich beim Klicken auf Maintenance/Beta/Alpha im Dev-Panel im Bestätigungsdialog sofort sehen, ob der Modus aktuell schon aktiv ist, damit ich nicht versehentlich umschalte.

#### Acceptance Criteria

1. WENN ein Admin im Entwickler-Panel den Button für Maintenance, Beta oder Alpha klickt, DANN SOLL DER Bot einen Bestätigungsdialog anzeigen, der den aktuellen Aktivierungsstatus dieses Modus enthält (aktiv / nicht aktiv).
2. DER Bestätigungsdialog SOLL klar formuliert beschreiben, welcher Übergang ausgeführt wird (z. B. „Maintenance ist AKTIV → wird DEAKTIVIERT", „Beta ist NICHT AKTIV → wird AKTIVIERT").
3. WENN der Admin den Bestätigungsdialog abbricht, DANN SOLL DER Bot den Status unverändert lassen.

---

### Requirement 11: Grant Card identisch zu `/karte_geben` (mit Multi immer an)

**User Story:** Als Admin möchte ich, dass „Grant Card" im Entwickler-Panel sich exakt so verhält wie `/karte_geben`, mit dem einzigen Unterschied, dass Multi-Mode immer aktiv ist.

#### Acceptance Criteria

1. DAS Bot-„Grant Card"-Modul SOLL dieselben Auswahl-Schritte, dieselben Validierungen und dieselbe Bestätigungs-UX wie `/karte_geben` durchlaufen.
2. WENN „Grant Card" gestartet wird, DANN SOLL DER Bot Multi-Mode automatisch aktiv setzen, ohne dass der Admin Single/Multi wählen kann.
3. „Grant Card" und `/karte_geben` SOLLEN dieselbe interne Vergabe-Logik aufrufen (gemeinsamer Service / gemeinsame Funktion), so dass funktionale Drift ausgeschlossen ist.
4. ÄNDERUNGEN am Vergabe-Verhalten in `/karte_geben` SOLLEN automatisch auch in „Grant Card" wirksam werden (Eigenschaft: gemeinsamer Code-Pfad).

---

### Requirement 12: Kampf-Abbruch (Cancel) für Challenger und Acceptor

**User Story:** Als Spieler (Challenger oder Acceptor) möchte ich eine offene Challenge oder einen laufenden Kampf jederzeit abbrechen können, damit niemand in einer hängenden Anfrage festsitzt.

#### Acceptance Criteria

1. WENN ein Spieler einen anderen herausfordert, DANN SOLL DER Bot in der Challenge-Nachricht für BEIDE Spieler einen sichtbaren „Abbrechen"-Button anbieten.
2. WÄHREND ein Kampf läuft (in einem Thread), SOLL DER Bot für beide Spieler einen sichtbaren „Kampf abbrechen"-Button anbieten.
3. WENN ein Spieler den „Abbrechen"-Button vor Annahme der Challenge drückt, DANN SOLL DER Bot die Challenge schließen und beide Spieler informieren.
4. WENN ein Spieler den „Kampf abbrechen"-Button während des Kampfes drückt, DANN SOLL DER Bot den Kampf beenden, eine Abbruch-Nachricht im Thread posten und den Thread regelkonform schließen/archivieren.
5. NACH einem Abbruch SOLL DER Bot keine weiteren AFK-Pings für diese Challenge oder diesen Kampf senden — der Stopp aller weiteren Pings SOLL unmittelbar mit dem Klick auf den Cancel-Button erfolgen, nicht erst nach abgeschlossener Cleanup-Verarbeitung.

---

### Requirement 13: AFK-Markierungssystem (Pings bei Inaktivität)

**User Story:** Als Spieler möchte ich, dass inaktive Gegner per Ping erinnert werden, damit Kämpfe nicht in Inaktivität versanden.

#### Acceptance Criteria

1. WÄHREND eine Challenge offen, aber noch nicht angenommen ist UND seit 4 Stunden keine Aktivität (Aktion des Acceptor oder des Challengers) stattfand, SOLL DER Bot den Acceptor genau einmal pingen.
2. WÄHREND ein Kampf in Runde 1 oder Runde 2 läuft UND der aktive Spieler seit 4 Stunden keinen Zug ausgeführt hat, SOLL DER Bot den aktiven Spieler im Kampf-Thread genau einmal pingen.
3. IN Runde 1 und Runde 2 SOLL DER Bot keine weiteren Pings über den 4-Stunden-Ping in 13.2 hinaus senden.
4. WÄHREND ein Kampf ab Runde 3 läuft, SOLL DER Bot folgenden Ping-Zyklus pro Runde ausführen, basierend auf der Inaktivität seit Rundenbeginn:
   - Bei 2 Stunden Inaktivität: pingt den aktiven Spieler.
   - Bei 3 Stunden Inaktivität: pingt BEIDE Spieler.
   - Bei 4 Stunden Inaktivität: pingt den aktiven Spieler.
   - Bei 6 Stunden Inaktivität: pingt BEIDE Spieler.
5. NACH dem 6-Stunden-Ping in einer Runde ab Runde 3 SOLL DER Bot bis zum Beginn der nächsten Runde keine weiteren Pings senden — dies gilt vollständig (weder an den aktiven Spieler noch an beide Spieler).
6. WENN ein neuer Zug erfolgt (= neue Runde beginnt), DANN SOLL DER Bot den Ping-Zyklus für diese Runde vollständig zurücksetzen.
7. AFK-Pings SOLLEN ausschließlich im Kampf-Thread (bzw. im Challenge-Channel für ungeöffnete Challenges) erfolgen, nicht per DM.
8. DIE AFK-Timer SOLLEN in einem persistenten Speicher gehalten werden, sodass sie auch nach einem Bot-Neustart korrekt weiterlaufen.
9. WENN der Bot neu startet, DANN SOLL DER Bot offene Challenges und laufende Kämpfe samt ihrer AFK-Timer-Zustände aus dem persistenten Speicher rekonstruieren.
10. AFK-Pings SOLLEN den jeweiligen Spieler per Discord-Mention (`<@id>`) ansprechen.

#### Korrektheits-Eigenschaften (Properties)

- **Idempotenz**: Mehrfaches Auswerten der Ping-Logik im selben Zustand SOLL höchstens einmal je definierter Schwelle einen Ping erzeugen.
- **Ping-Cap pro Runde (ab Runde 3)**: Pro Runde werden höchstens 4 Pings gesendet (2h aktiv, 3h beide, 4h aktiv, 6h beide).
- **Reset-Property**: Nach einem Zug innerhalb einer Runde sind alle in dieser Runde aufgelaufenen Ping-Marker konsumiert; in der neuen Runde startet die Inaktivitätsuhr bei 0.
- **Restart-Property**: Sei `t_now` die aktuelle Zeit, `t_last_action` die zuletzt gespeicherte Aktion. Vor und nach einem Bot-Neustart liefert die Ping-Logik für denselben `(t_last_action, t_now, runde, aktiver_spieler)` dieselbe Ping-Menge.

---

### Requirement 14: Cooldown-Anzeige für Angriffe und Fähigkeiten

**User Story:** Als Spieler möchte ich vor der Auswahl einer Fähigkeit erkennen, wie viele Runden Cooldown sie hat, damit ich strategisch planen kann.

#### Acceptance Criteria

1. WENN der Bot eine Liste von Angriffen oder Fähigkeiten einer Karte anzeigt, DANN SOLL DER Bot bei jeder Fähigkeit mit Cooldown größer 0 die Cooldown-Anzahl in der Form `(<n>CD)` an den Anzeigenamen anhängen (z. B. `Gamma-Eruption (3CD)`).
2. DAS Bot-Verhalten aus 14.1 SOLL in allen Vorschauen und Auswahl-Listen vor Nutzung einer Fähigkeit sichtbar sein. Außerhalb von Vorschau- und Auswahl-Kontexten ist das `(<n>CD)`-Suffix nicht erforderlich.
3. WÄHREND eine Fähigkeit gerade auf Cooldown liegt (also gerade nicht wählbar), SOLL DER Bot diese Fähigkeit im bisherigen ausgegrauten Zustand belassen und KEINE zusätzliche `(<r>/<n>CD)`-Restzeit-Anzeige hinzufügen — der ausgegraute Zustand allein ist die UI-Markierung.
4. WENN eine Fähigkeit verfügbar ist (nicht auf Cooldown) UND einen konfigurierten Cooldown größer 0 hat, DANN SOLL DER Bot in der Vorschau- bzw. Auswahl-Liste das Suffix `(<n>CD)` an den Anzeigenamen anhängen (siehe 14.1), wobei `<n>` der konfigurierte Standard-Cooldown ist.
5. FALLS eine Fähigkeit keinen Cooldown hat (Cooldown = 0), DANN SOLL DER Bot kein `(CD)`-Suffix anzeigen.

---

### Requirement 15: Boss-Spezialfähigkeit: hervorgehobene Aktivierungs-Meldung

**User Story:** Als Spieler möchte ich, dass die Aktivierung einer Boss-Spezialfähigkeit klar und unübersehbar im Kampfverlauf hervorgehoben wird, damit ich sehe, was passiert ist.

#### Acceptance Criteria

1. WENN ein Boss eine Spezialfähigkeit auslöst, DANN SOLL DER Bot diese Aktivierung in einer eigenen, optisch hervorgehobenen Zeile darstellen (Fettschrift und/oder eigener Embed-Field oder eigene Zeile mit Marker wie `⚡` o. ä.).
2. DIE hervorgehobene Aktivierungs-Meldung SOLL den Namen der ausgelösten Spezialfähigkeit und ihren Effekt klar benennen (z. B. „⚡ **Maestros Hohn** — der nächste Spielerangriff verursacht 0 Schaden.").
3. FALLS entweder Name oder Effekt der ausgelösten Spezialfähigkeit nicht ermittelbar sind, DANN SOLL DER Bot die Aktivierungs-Meldung NICHT senden und stattdessen einen internen Logeintrag zur Fehlersuche erzeugen.
4. DIE Anzeige aus 15.1 SOLL für JEDE Boss-Spezialfähigkeit erfolgen (gilt für Maestro, MODOK, Green Goblin, Kingpin, Agatha sowie ggf. zukünftige Bosse).
5. FALLS die hervorgehobene Anzeige bereits an einer Stelle existiert, DANN SOLL diese als Referenz für die einheitliche Implementierung an allen Stellen dienen.

---

### Requirement 16: Boss-Balance: Maestro

**User Story:** Als Spieler möchte ich, dass der Maestro-Boss die folgenden Werte und Effekte hat, damit der Encounter wie geplant verläuft.

#### Acceptance Criteria

1. WENN das Bot-Maestro-Boss-Profil in seiner Aktivierung die Standardattacke `Tyrannen-Schlag` ausführt, DANN SOLL DAS Bot-Maestro-Boss-Profil ganzzahligen Schaden im Bereich 14 bis 20 (beide Grenzen inklusive, gleichverteilt) verursachen.
2. WENN das Bot-Maestro-Boss-Profil die Spezialfähigkeit `Trophäensaal-Raub` aktiviert, DANN SOLL DAS Bot-Maestro-Boss-Profil per uniformer Zufallsauswahl mit jeweils 50 % Wahrscheinlichkeit genau einen der beiden folgenden Effekte für die Dauer dieser Aktivierung aktivieren:
   - Variante A: Ein Schild blockt bis zu 20 eingehenden Schaden des nächsten Spielerangriffs; nach Auslösung oder spätestens am Ende der nächsten Spielerrunde verfällt der Schild, ODER
   - Variante B: Der in derselben Boss-Aktivierung ausgeführte `Tyrannen-Schlag` verursacht genau +10 zusätzlichen Schaden zum gewürfelten Basisschaden (14–20), wirkt einmalig und nicht kumulierbar.
3. WENN das Bot-Maestro-Boss-Profil die Spezialfähigkeit `Maestros Hohn` aktiviert, DANN SOLL DAS Bot-Maestro-Boss-Profil bewirken, dass der nächste Spielerangriff genau 0 Schaden verursacht, UND der Effekt SOLL nach genau einer Spielerrunde ablaufen — unabhängig davon, ob der Spieler in dieser Runde tatsächlich angreift oder eine andere Aktion wählt.
4. WENN das Bot-Maestro-Boss-Profil in seiner Aktivierung die Spezialfähigkeit `Gamma-Eruption` ausführt, DANN SOLL DAS Bot-Maestro-Boss-Profil ganzzahligen Schaden im Bereich 26 bis 35 (beide Grenzen inklusive, gleichverteilt) verursachen.
5. DAS Bot-Maestro-Mission-Profil SOLL die Wellen 1–3 (Lakeien) gegenüber dem zuletzt freigegebenen Stand vor v2.3.0 wertgleich beibehalten (identische Gegnerlisten, Anzahl pro Welle, Werte und Reihenfolge); FALLS Abweichungen festgestellt werden, DANN SOLL DAS Bot-Maestro-Mission-Profil als nicht konform gelten.
6. DIE Maestro-Mission SOLL aus genau 3 aufeinanderfolgenden Lakeien-Wellen, gefolgt von genau 1 Boss-Encounter bestehen; FALLS zusätzliche Wellen oder weitere Boss-Encounter konfiguriert sind, DANN SOLL DAS Bot-Maestro-Mission-Profil als nicht konform gelten.

---

### Requirement 17: Boss-Balance: MODOK

**User Story:** Als Spieler möchte ich den dritten MODOK-Lakei minimal abgeschwächt sehen und die Boss-Werte exakt nach Vorgabe angepasst.

#### Acceptance Criteria

1. DER dritte Lakei der MODOK-Mission SOLL minimal abgeschwächt werden (geringere HP- oder Damage-Werte als bisher); konkrete Zielwerte werden in der Design-Phase aus Sim-Daten abgeleitet (Richtwert: 10–20 % Reduktion auf HP und/oder mindestens eine Damage-Quelle).
2. WENN ein MODOK-Wellen-Lakei `Rammstoß` ausführt, DANN SOLL der Bot ganzzahligen Schaden im Bereich 14–18 (beide Grenzen inklusive, gleichverteilt) verursachen.
3. WENN ein MODOK-Wellen-Lakei `Kanone` ausführt, DANN SOLL der Bot ganzzahligen Schaden im Bereich 20–24 (beide Grenzen inklusive, gleichverteilt) verursachen.
4. WENN das Bot-MODOK-Boss-Profil `Gedankenstrahl` aktiviert, DANN SOLL der Bot ganzzahligen Schaden im Bereich 12–20 (beide Grenzen inklusive, gleichverteilt) verursachen.
5. WENN das Bot-MODOK-Boss-Profil `System-Hack` aktiviert, DANN SOLL der Bot exakt 15 Schaden verursachen UND in der unmittelbar darauffolgenden Spielerrunde alle Cooldown-Fähigkeiten des Spielers für genau diese eine Runde sperren (nur Standardangriffe sind in dieser Runde wählbar). Nach dieser Spielerrunde SOLL die Sperre automatisch enden.
6. WENN das Bot-MODOK-Boss-Profil `Berechnete Heilung` aktiviert UND der Spieler in der unmittelbar vorherigen Spielerrunde keine Cooldown-Fähigkeit eingesetzt hat, DANN SOLL der Bot die MODOK-HP um genau 15 erhöhen (gedeckelt durch Boss-Max-HP).
7. WENN das Bot-MODOK-Boss-Profil `Berechnete Heilung` aktiviert UND der Spieler in der unmittelbar vorherigen Spielerrunde mindestens eine Cooldown-Fähigkeit eingesetzt hat, DANN SOLL der Bot die MODOK-HP um genau 30 erhöhen (gedeckelt durch Boss-Max-HP).
8. WENN das Bot-MODOK-Boss-Profil `Gehirn-Explosion` aktiviert, DANN SOLL der Bot exakt 25 Schaden verursachen.

---

### Requirement 18: Boss-Balance: Green Goblin

**User Story:** Als Spieler möchte ich den dritten Green-Goblin-Lakei minimal einfacher sehen und die Boss-Werte angepasst.

#### Acceptance Criteria

1. DER dritte Lakei der Green-Goblin-Mission SOLL minimal abgeschwächt werden; konkrete Zielwerte werden in der Design-Phase festgelegt.
2. DIE Green-Goblin-Wellen-Standardattacken SOLLEN folgende Damage-Bereiche verwenden:
   - `MG-Sperrfeuer`: 14–18 (ganzzahlig, beide Grenzen inklusive, gleichverteilt).
   - `Hitzegranate`: 24–30 (ganzzahlig, beide Grenzen inklusive, gleichverteilt).
3. WENN das Bot-Green-Goblin-Boss-Profil die Standardattacke `Goblin-Handschuh` ausführt, DANN SOLL es ganzzahligen Schaden im Bereich 14–18 (beide Grenzen inklusive, gleichverteilt) verursachen.
4. WENN das Bot-Green-Goblin-Boss-Profil die Spezialfähigkeit `Gleiter-Ramme` aktiviert, DANN SOLL es 20 Schaden verursachen UND einen Effekt setzen, der den nächsten Spieler-Spezialangriff mit 6 Rückstoß-Schaden auf den Spieler selbst belegt; der Rückstoß-Effekt SOLL nach genau einem Spieler-Spezialangriff verbraucht sein.
5. WENN das Bot-Green-Goblin-Boss-Profil die Spezialfähigkeit `Halluzinogenes Gas` aktiviert, DANN SOLL es genau 10 Schaden verursachen UND dem nächsten Spielerangriff eine Verfehlchance von 50 % zuweisen; der Verfehl-Effekt SOLL nach genau einem Spielerangriff verbraucht sein.
6. WENN das Bot-Green-Goblin-Boss-Profil die Spezialfähigkeit `Kürbisbomben-Teppich` aktiviert, DANN SOLL es in derselben Boss-Aktivierung 3 sequenzielle Treffer mit je genau 8 Schaden auf denselben Spieler-Helden ausführen (Gesamtschaden 24).

---

### Requirement 19: Boss-Balance: Kingpin

**User Story:** Als Spieler möchte ich, dass der Kingpin-Boss die folgenden Werte und Effekte hat.

#### Acceptance Criteria

1. WENN das Bot-Kingpin-Boss-Profil die Standardattacke `Stockhieb` ausführt, DANN SOLL es ganzzahligen Schaden im Bereich 13–17 (beide Grenzen inklusive, gleichverteilt) verursachen.
2. WENN das Bot-Kingpin-Boss-Profil die Spezialfähigkeit `Sumo-Ansturm` aktiviert, DANN SOLL es genau 22 Schaden verursachen UND alle aktiven defensiven Effekte des Spielers (Schilde, Block-Stapel, Schadensreduktion, Immunitäten, defensive Buffs) entfernen.
3. WENN das Bot-Kingpin-Boss-Profil die Spezialfähigkeit `Bestechungs-Versuch` aktiviert UND der Spieler hat in der vorherigen Runde 0 Schaden zugefügt, DANN SOLL Kingpin 30 HP heilen.
4. WENN das Bot-Kingpin-Boss-Profil die Spezialfähigkeit `Bestechungs-Versuch` aktiviert UND der Spieler hat in der vorherigen Runde mehr als 0 Schaden zugefügt, DANN SOLL Kingpin 35 HP heilen.
5. DIE Heilung aus 19.3 und 19.4 SOLL Kingpins HP nicht über sein konfiguriertes Maximum heben; FALLS die berechnete Heilung das Maximum überschreiten würde, DANN SOLL DER Bot auf das Maximum cappen.
6. WENN das Bot-Kingpin-Boss-Profil die Spezialfähigkeit `Zermalmender Griff` aktiviert UND der Spieler hat aktuell 60 HP oder mehr, DANN SOLL es genau 26 Schaden verursachen.
7. WENN das Bot-Kingpin-Boss-Profil die Spezialfähigkeit `Zermalmender Griff` aktiviert UND der Spieler hat aktuell weniger als 60 HP, DANN SOLL es genau 38 Schaden verursachen.
8. DIE Wellen 1–3 (Lakeien) der Kingpin-Mission SOLLEN gegenüber dem zuletzt freigegebenen Stand vor v2.3.0 wertgleich beibehalten werden (identische Gegnerlisten, Werte und Reihenfolge).

---

### Requirement 20: Boss-Balance: Agatha

**User Story:** Als Spieler möchte ich den dritten Agatha-Lakei minimal einfacher sehen und die Boss-Werte exakt nach Vorgabe angepasst.

#### Acceptance Criteria

1. DER dritte Lakei der Agatha-Mission SOLL minimal abgeschwächt werden; konkrete Zielwerte werden in der Design-Phase aus Sim-Daten abgeleitet (Richtwert: 10–20 % Reduktion auf HP und/oder mindestens eine Damage-Quelle).
2. WENN ein Agatha-Wellen-Lakei die Standardattacke `Höllenfeuerstoß` ausführt, DANN SOLL der Bot exakt 24 Schaden verursachen.
3. WENN das Bot-Agatha-Boss-Profil `Chaos-Energie-Ball` aktiviert, DANN SOLL der Bot exakt 11 Schaden verursachen.
4. WENN das Bot-Agatha-Boss-Profil `Darkhold-Fluch` aktiviert, DANN SOLL der Bot exakt 10 Schaden verursachen UND den Heileffekt der nächsten ausgelösten Spielerfähigkeit, die Heilung gewährt (z. B. Wolverines Heilfaktor), um 100 % reduzieren (effektiv negieren); der Effekt SOLL mit der Auflösung dieser nächsten heilenden Spielerfähigkeit ablaufen oder spätestens am Kampfende.
5. WENN das Bot-Agatha-Boss-Profil `Lila Illusion` aktiviert, DANN SOLL der nächste Spielerangriff garantiert verfehlen (0 Schaden, keinerlei Sekundäreffekte) UND Agatha SOLL als Konter exakt 15 Schaden auf denselben Spieler verursachen; der Effekt SOLL mit der Auflösung dieses nächsten Spielerangriffs ablaufen.
6. WENN das Bot-Agatha-Boss-Profil `Hexen-Sabbat` aktiviert, DANN SOLL der Bot exakt 35 Schaden verursachen.
7. WENN das Bot-Agatha-Boss-Profil `Hexen-Sabbat` aktiviert UND der Spieler in derselben Spielerrunde (zwischen Beginn und Ende seines Zugs) eine Spezialfähigkeit eingesetzt hat, DANN SOLL der Cooldown genau dieser eingesetzten Spezialfähigkeit zusätzlich auf ihren konfigurierten Maximalwert gesetzt werden (vollständig zurückgesetzt — die Fähigkeit ist anschließend so lange gesperrt, wie ihr Maximal-Cooldown vorgibt).

---

### Requirement 21: Korrektheits-Eigenschaften für Aufwertungs-System

**User Story:** Als Entwickler möchte ich mathematische Invarianten des `/verbessern`-Systems durch Tests sicherstellen, damit Caps und Multiplikatoren immer konsistent gelten.

#### Acceptance Criteria

1. FÜR ALLE Karten und alle Stats SOLL gelten: nach jeder Aufwertung ist der Stat-Wert kleiner-gleich dem Stat-Cap.
2. FÜR ALLE Karten SOLL gelten: nach jeder HP-Aufwertung ist HP ≤ 200.
3. FÜR ALLE Aufwertungs-Vorgänge SOLL gelten: angezeigte Multiplikator-Optionen × Basiswert ≤ verbleibender Cap-Abstand.
4. FÜR ALLE Aufwertungs-Vorgänge SOLL gelten: Dust-Kosten = Multiplikator × 5 (1× = 5, 2× = 10, …, 6× = 30).
5. FÜR ALLE Aufwertungs-Vorgänge SOLL gelten: Dust-Saldo nach Aufwertung = Dust-Saldo vor Aufwertung − Dust-Kosten, und niemals negativ.

---

### Requirement 22: Release v2.3.0 (Versionierung, Commit, Push, Tag)

**User Story:** Als Maintainer möchte ich, dass v2.3.0 sauber im `main`-Branch versioniert, committed, gepusht und getaggt wird, damit das Release identifizierbar ist.

#### Acceptance Criteria

1. WENN alle inhaltlichen Änderungen für das Release abgeschlossen sind, DANN SOLL DAS Repository die Versionsnummer `v2.3.0` mindestens in `README.md` (sichtbar im Titel- oder Versions-Abschnitt) tragen, sowie in `bot.py` in einer Versions-Konstante (z. B. `__version__` oder `BOT_VERSION`), sofern eine solche Konstante in `bot.py` bereits existiert.
2. FALLS in `bot.py` keine Versions-Konstante existiert, DANN SOLL die Versionierung ausschließlich in `README.md` als verbindlicher Ort erfolgen, ohne neue Konstanten in `bot.py` einzuführen.
3. WENN die Versionierungsänderungen vorliegen, DANN SOLL DAS Repository genau einen Commit auf dem Branch `main` erzeugen, der alle Versionierungs-, Release- und zugehörigen Änderungen dieses Releases enthält und auf einem sauberen Arbeitsbaum basiert (keine ungetrackten oder ungestageten Release-Dateien verbleiben nach dem Commit).
4. DER Commit-Titel SOLL mit dem Präfix `release: v2.3.0` beginnen und einen Kurzbeschreibungs-Suffix enthalten (Beispiel: `release: v2.3.0 - boss switch, /verbessern overhaul, AFK system, balance`); die maximale Titel-Länge SOLL 100 Zeichen nicht überschreiten.
5. FALLS der Commit-Titel das Muster `^release:\s*v2\.3\.0(\s|$|-)` nicht erfüllt, DANN SOLL DER Commit-Vorgang abgebrochen werden, ohne Commit auf `main` anzulegen, und eine Fehlermeldung ausgegeben werden, die auf das fehlende `v2.3.0`-Muster hinweist.
6. WENN der Release-Commit auf `main` existiert, DANN SOLL DER Branch `main` mit gesetztem Upstream-Tracking auf `origin/main` zum Remote `origin` gepusht werden (entspricht `git push -u origin main`).
7. WENN der Push von `main` erfolgreich abgeschlossen ist, DANN SOLL EIN annotiertes oder leichtgewichtiges Git-Tag mit exaktem Namen `v2.3.0` auf den Release-Commit (HEAD von `main` nach Push) gesetzt werden.
8. WENN das Tag `v2.3.0` lokal gesetzt ist, DANN SOLL DAS Tag `v2.3.0` zum Remote `origin` gepusht werden (entspricht `git push origin v2.3.0` oder `git push --tags` beschränkt auf dieses Tag).
9. FALLS das Tag `v2.3.0` bereits lokal oder auf dem Remote existiert, DANN SOLL DER Release-Vorgang abgebrochen werden, ohne das bestehende Tag zu überschreiben oder zu verschieben, und eine Fehlermeldung ausgegeben werden, die auf den Tag-Konflikt hinweist.
10. FALLS während des Push-Vorgangs (Branch oder Tag) ein Fehler auftritt (z. B. Netzwerkfehler, abgelehnter Push, fehlende Berechtigungen), DANN SOLL DER Release-Vorgang den Fehler unverändert melden, lokale Commit- und Tag-Zustände erhalten und keinen automatischen Rollback durchführen.
11. WÄHREND des gesamten Release-Vorgangs SOLL DAS System keine destruktiven Git-Operationen (insbesondere `git push --force`, `git push -f`, `git branch -D`, `git reset --hard`, `git tag -d` auf bestehende Remote-Tags) ohne vorherige, in derselben Sitzung gegebene, explizite textuelle Bestätigung des Maintainers ausführen.

---

## Offene Klärungsfragen (für die Design-Phase)

1. **MODOK Lakei 3 / Green Goblin Lakei 3 / Agatha Lakei 3** (Req. 17.1, 18.1, 20.1): Konkrete Zielwerte (HP/Damage) für die minimale Abschwächung — die Design-Phase leitet aus den vorhandenen Sim-Daten ab; Richtwert 10–20 % Reduktion auf HP und/oder mindestens eine Damage-Quelle.

> Alle übrigen ursprünglich offenen Punkte sind durch die Nutzerantworten geschlossen:
> - Konfigurationsdatei: `namenconfig.py` im Repo-Root (siehe Req. 2).
> - Green Goblin `Gleiter-Ramme`: feste 20 Schaden + 6 Recoil auf nächsten Spieler-Spezial (siehe Req. 18.4).
> - Kingpin `Bestechungs-Versuch`: 30 HP wenn Vorrunde 0 Schaden, sonst 35 HP; 15 HP nur als Fallback wenn keine Vorrunde existiert (siehe Req. 19.3–19.5).
> - AFK-Ping-Format: `<@id>` (Mention) reicht; zusätzlicher Kontext optional in der Design-Phase.
> - Cooldown-Anzeige: `(<n>CD)` nur bei verfügbaren Fähigkeiten; gesperrte Fähigkeiten bleiben ausgegraut ohne zusätzlichen Counter (siehe Req. 14).
