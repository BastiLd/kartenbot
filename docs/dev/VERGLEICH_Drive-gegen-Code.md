# Vergleich: Google Drive „Marvel Nexus Battle“ gegen den Code

Stand: 19. September 2026. **Es wurde nichts am Code geändert** — das hier ist nur die Bestandsaufnahme.

**Gelesen (Drive):** Google Doc „Charaktere“ (5. April 2026), „Gegner - Missionen.docx“ (16. Mai 2026),
„Story - Missionen.docx“ (9. September 2026), dazu die Ordner „Helden“, „Missionen“, „Items“.
**Verglichen mit:** `karten.py`, `mission_enemies.py`, `botcommands/`, Stichproben in `bot.py` (Boss-Taktiken),
`docs/releases/release_notes_v2.3.0.md`.
**Was verglichen wurde:** Namen, Schadensbereiche, Abklingzeiten und die beschriebene Wirkung anhand der
Effektdaten. Das Verhalten im echten Kampf wurde **nicht** nachgespielt.

---

## Kurzfassung

| Bereich | Ergebnis |
|---|---|
| **Helden** | 34 im Drive, 34 im Code, alle Namen gleich. Bei 8 Helden gibt es echte Abweichungen (andere Mechanik oder anderer Cooldown), bei 7 weiteren nur kleine Wertunterschiede (Star Lord und Human Torch haben beides). Der Rest stimmt. Upgrade-Regeln (Standard 2× +4, Spezial 5× +3) stimmen. |
| **Missionen** | Drive kennt **9 Operationen**, der Code **5**. **4 fehlen komplett:** Roter Schatten (Red Skull), Tentakel-Griff (Doc Ock), Jenseits (Hela), Spiegelbild (Taskmaster). Die 5 vorhandenen weichen bei einigen Werten ab — das meiste davon ist **bewusst** (Update v2.3.0). |
| **Story (Secret Wars)** | **Nicht im Code.** Es gibt nur einen Platzhalter (`/geschichte` mit „Test-Story“). |
| **Drive-Ordner** | „Helden“ enthält nur 6 Bilder. „Items“ und alle Boss-Ordner unter „Missionen“ sind für mich **leer**. |

---

## 1. Helden

### 1.1 Echte Abweichungen (andere Mechanik oder anderer Cooldown)

| Held | Fähigkeit | Drive | Code |
|---|---|---|---|
| **Black Widow** | Tarnung | 1 Runde unsichtbar (0 Schaden), in dieser Runde darf sie nur „Treten“. CD 5 | „Bereitet Tarnung vor: nach dem nächsten eigenen Angriff wird der darauffolgende Gegnerangriff geblockt.“ CD 5. Andere Mechanik. Passt zur Spec `.kiro/specs/bugfix-tarnung-und-anzeigen` (vermutlich bewusst) |
| **Captain America** | Schild-Hieb | 20–28, **plus 8–12 Zusatzschaden, falls vorher geblockt** | Nur 20–28, **kein Bonus** nach Block. Der Text im Code erwähnt ihn auch nicht |
| **Rocket** | Das dicke Ding | **42–52**, 15 Rückstoß | **38–48**, 15 Rückstoß |
| **Star Lord** | Jet-Boots | Weicht dem nächsten Angriff komplett aus, eigener nächster Angriff +10 | Erst +10 auf den nächsten eigenen Angriff, **danach** Ausweichen des folgenden Gegnerangriffs (dieselbe „verzögerte Abwehr“ wie bei Tarnung) |
| **Human Torch** | Supernova-Ladung | **CD 5** | **CD 4** |
| **Cyclops** | Taktisches Manöver / Visier-Anpassung | Manöver **CD 5**, Visier **CD 4** | Manöver **CD 4**, Visier **CD 5** (vertauscht) |
| **Ultron** | Reaktive Evolution | Dauerhaft −2 Schaden **nur vom Angriffstyp des letzten Angriffs** (Standard oder Spezial), bis **3× stapelbar** | −2 Schaden auf **alle** Treffer, `max_stacks: 1` |
| **Deadpool** | Finisher | Sofort besiegt, wenn der Treffer den Gegner **unter 20 HP** bringt. Im Doc heißt er „Hex-Fluch“ (offensichtlich von Scarlet Witch kopiert) | Heißt „Letzter Witz“, Schwelle **15 HP** |

### 1.2 Kleine Wertunterschiede (Drive: Bereich oder feste Zahl, Code: anderer Wert)

Meist nimmt der Code die Mitte des Bereichs oder macht aus einer festen Zahl einen Bereich.

| Held | Fähigkeit | Drive | Code |
|---|---|---|---|
| Star Lord | Schwerkraft-Mine | nächster Gegnerangriff −8 bis −12 | −10 fest |
| Wolverine | X-Schnitt | Blutung 5–7 pro Runde | 6 fest |
| Spider-Man | Netz-Versiegelung | −12 bis −16 | −15 fest |
| Nick Fury | S.H.I.E.L.D. Verstärkung | Standardangriff +8 bis +12 | +10 fest |
| Shang-Chi | Fünf-Finger-Explosion | Gegner −12 bis −16 | −14 fest |
| She-Hulk | Einspruch! | Rückschaden 12–16 | 14 fest |
| Sue Storm | Unsichtbarer Schutz | absorbiert 25–35 | 30 fest |
| Thor | Ruf des Donners | 14 fest | 12–16 |
| Human Torch | Feuerball | 12 fest | 10–14 |

### 1.3 Nicht im Detail geprüft

- **Mr. Fantastic:** „Hyper-Intelligenz-Schlag“ (30, +15 wenn in der Vorrunde „Taktische Analyse“). Im Code gibt
  „Taktische Analyse“ selbst +15 auf den nächsten Angriff, und der Schlag hat eigene Cooldown-Regeln nach Schaden.
  Sinngemäß umgesetzt, aber anders verdrahtet.
- Alle übrigen Angriffe: Schaden und Abklingzeit sind gleich.

---

## 2. Missionen

### 2.1 Welche Operationen es gibt

| Operation (Drive) | Boss (Drive-HP) | Im Code? |
|---|---|---|
| Broken Timeline | Maestro (185) | ja |
| Roter Schatten | Red Skull (195) | **nein** |
| Technischer Kollaps | M.O.D.O.K. (190) | ja |
| Grüner Terror | Green Goblin (190) | ja |
| Tentakel-Griff | Doctor Octopus (200) | **nein** |
| Goldener Käfig | Kingpin (215) | ja |
| Jenseits | Hela (210) | **nein** |
| Spiegelbild | Taskmaster (195) | **nein** |
| Hexenfeuer | Agatha Harkness (185, siehe 2.3) | ja |

Fehlend sind damit **16 Gegner** (je 3 Wellen + 1 Boss × 4 Operationen). Im Drive-Ordner „Missionen“ gibt es
Unterordner für Taskmaster, Doc Ock, Hela, Kingpin, Red Skull und Green Goblin — **alle leer**. Für Maestro, M.O.D.O.K.
und Agatha gibt es dort gar keinen Ordner (die Bilder liegen bereits im Code als imgur-Links).

### 2.2 Abweichungen bei den 5 vorhandenen Operationen

**Bewusst geändert:** Das Update **v2.3.0** hat Bosse neu ausbalanciert und „Lakei 3“ (Welle 3) von M.O.D.O.K.,
Green Goblin und Agatha um rund 15 % abgeschwächt (`docs/releases/release_notes_v2.3.0.md`). Das erklärt die meisten
Unterschiede. Das Drive-Dokument wurde zuletzt am 16. Mai 2026 geändert, also vor dieser Neujustierung.

| Operation | Gegner | Drive | Code |
|---|---|---|---|
| Broken Timeline | Maestro | Tyrannen-Schlag 11–14 | 14–20 (v2.3.0) |
| Broken Timeline | Gamma-Mutant | Passiv „Radioaktive Aura“ | Passiv vorhanden, dazu eine **zusätzliche Attacke „Strahlen-Welle“ (12–14, CD 3)**, die im Drive fehlt |
| Technischer Kollaps | Welle 3 | „Schwerer Kampf-Mech“, HP **120**, Rammstoß 18, Gatling 28 | „Kybernetischer Exo-Suit“, HP **102**, Rammstoß 14–18, Gatling 20–24 (≈ −15 %) |
| Grüner Terror | Welle 3 | „Prototyp-Kampfgleiter“, HP **115**, MG 18, Rakete 30 | HP **98**, MG 14–18, Rakete 24–30 (≈ −15 %) |
| Grüner Terror | Green Goblin | Goblin-Handschuh 12 | 14–18 (v2.3.0) |
| Goldener Käfig | Kingpin | Zermalmender Griff 26 (38, wenn Spieler unter 60 HP) | In `mission_enemies.py` steht 38 fest, die Bedingung 26/38 liegt in `bot.py` (v2.3.0). Stockhieb 13 → 13–17 |
| Hexenfeuer | Welle 3 | „Wächter des Dunkelbuchs“, HP **115**, Höllenfeuer 30 | HP **98**, Höllenfeuer 24 (≈ −15 %) |

Außerdem: **Namen der Gegner** im Code sind an die Bilder angeglichen (Umprogrammierter Hulkbuster →
„Maestro-Hulkbuster“, A.I.M.-Laborwache → „A.I.M. Techniker“, Fisks Enforcer → „Fisk Rechte-Hand“ usw.). Das steht so
in der Übergabe (Stufe 5, Missionsbereich).

**Stimmt überein:** Alle Boss-HP (Maestro 185, M.O.D.O.K. 190, Green Goblin 190, Kingpin 215, Agatha 185), Wellen 1 und 2,
alle Abklingzeiten, Maestros „Gnadenschuss“ bei unter 35 HP (das Drive-Dokument nennt 35, die alte Datei 50).

### 2.3 Fehler im Drive-Dokument selbst

- **Agatha Harkness** hat zwei HP-Werte: „Der Boss: 185“ und „BOSS Agatha Harkness: 245“. Der Code nutzt 185.
- **Deadpool** (Charaktere) hat den Finisher „Hex-Fluch“ genannt (siehe 1.1).
- **Regieanweisungen zu Furys Auftritt sind kopiert:** „Fury steht vor einer digitalen Karte, die ein unterirdisches
  A.I.M.-Labor zeigt“ steht auch bei Grüner Terror und Tentakel-Griff, wo es nicht passt. „…flimmernder Bildschirm mit
  violetten Rissen“ (gehört zu Jenseits) steht auch bei Spiegelbild und Hexenfeuer.

### 2.4 `Missionen.txt` im Repo ist alt

Die Datei `Missionen.txt` im Hauptordner ist ein **älterer Stand** derselben Operationen mit höheren Boss-HP
(Maestro 200, Red Skull 230, M.O.D.O.K. 240, Green Goblin 240, Doc Ock 240, Kingpin 280, Hela 260, Taskmaster 240,
Agatha 245) und Maestros Schwelle 50 statt 35. Außerdem ist ihre Kodierung kaputt (Umlaute). Sie sollte nicht mehr als
Vorlage genutzt werden — entweder durch den Drive-Stand ersetzen oder entfernen.

---

## 3. Story „Secret Wars“

Das Dokument beschreibt einen Story-Modus aus **Intro, 4 Akten und Outro**, der sich auf Seiten aus Comic-Heften bezieht
(z. B. „Heft 2, Seite 15 & 16“). Vier Boss-Kämpfe mit eigenen Regeln:

| Akt | Boss | HP | Besonderheit |
|---|---|---|---|
| 1 | Ultron | 190 | Alle 3 Runden „System-Infiltration“: Spieler muss wählen (stärkste Spezialfähigkeit 1 Runde sperren **oder** 8 Schaden) |
| 2 | Titania & Absorbing Man | 210 (gemeinsam) | Ein Boss mit geteilten HP, wechselt alle 2 Runden den Kämpfer im Vordergrund |
| 3 | Galactus | 240 | Sperrt zu Beginn Spezial-Slot 4; freischalten durch 50 Schaden in 3 Runden |
| 4 | Dr. Doom | 250 | Wechselt jede Runde zwischen „Magische Barriere“ und „Technologische Panzerung“ |

**Im Code:** kein Treffer für Secret Wars, Ultron-Boss, Titania, Absorbing Man, Galactus oder Doom als Gegner. Vorhanden ist
nur `StorySelectView` / `StoryPlayerView` in `bot.py` mit einer „Test-Story“ und einem Platzhalter-Video sowie der
Befehl `/geschichte`. Auf dem Willkommenstext steht bereits: „Du hast drei Leben, um die gesamte Geschichte zu
überleben“ — dazu gibt es noch keine Umsetzung.

Für die Umsetzung fehlt außerdem das Bildmaterial (Comic-Seiten) — der Ordner „Story“ existiert im Drive nicht.

---

## 4. Drive-Ordner

| Ordner | Inhalt (so wie ich ihn sehe) |
|---|---|
| **Helden** | 6 Bilder: `Scarlet Witch.png`, `Scarlet Witch 2.png`, `Loki 2.png`, `Groot 2.png`, `Dr Strange 2.png`, `Rocket 2.png` |
| **Missionen** | 6 Unterordner (Taskmaster, Doc Ock, Hela, Kingpin, Red Skull, Green Goblin), alle leer |
| **Items** | leer |

**Zweite Designs, die für die Level-/Einladungsliste gebraucht werden, aber im Drive fehlen:**
Black Widow, The Thing, Captain Marvel (Level-Belohnungen) sowie Captain America, Spider-Man, Namor (Einladungen).
Vorhanden sind nur Rocket, Dr Strange, Groot, Loki, Scarlet Witch. Wahrscheinlich ist noch nicht alles hochgeladen.

**Wichtig für die Bilder-Links:** Discord kann Bilder nur von **direkten, dauerhaft erreichbaren** Adressen anzeigen.
Freigabe-Links von Google Drive funktionieren dafür nicht zuverlässig. Discord-eigene Anhang-Links laufen seit 2024
ab. Bisher liegen alle Bilder auf imgur (`https://i.imgur.com/….png`) — dabei bleiben.

---

## 5. Was daraus folgt

1. **Nichts davon ist dringend.** Die Abweichungen bei Helden und Missionen sind überwiegend Balance-Entscheidungen.
2. **Zu klären, wenn ihr weitermacht:** Was ist die Wahrheit — das Drive-Dokument oder der Code? Besonders bei
   Black Widow (Tarnung), Captain America (Schild-Hieb-Bonus), Rocket (Schaden), Ultron (Stapel), Cyclops und Human Torch
   (Cooldowns) und Deadpool (Schwelle).
3. **Die 4 fehlenden Operationen und die Story** sind neue Bauvorhaben (neue Boss-Regeln in `bot.py`, Bilder,
   Belohnungen). Sie gehören nicht in Teil 1.
