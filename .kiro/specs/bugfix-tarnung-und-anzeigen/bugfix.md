# Bugfix Requirements Document

## Introduction

Dieses Bugfix-Spec adressiert vier voneinander unabhängige Defekte im deutschen Discord-Karten-Bot
(`d:\Cursour\Discord Bot`, Version `2.2.14`). Die Bugs betreffen unterschiedliche Subsysteme
(UI-Vorschau, Bot-KI, Sammlung-Anzeige, Kampfregel-Engine), werden aber gemeinsam in einem Release
`v2.2.15` ausgeliefert, weil sie alle in einer einzigen Spielrunde auftreten und der Nutzer den
Fix als zusammenhängendes Update wahrnehmen wird.

Die wichtigste Verhaltens­änderung ist Bug 4 (Tarnung): Tarnung darf nur dann konsumiert werden,
wenn der eingehende gegnerische Angriff tatsächlich Schaden > 0 zufügen würde. Phase 1 von
Multi-Phasen-Angriffen (z. B. `airborne_two_phase` / Sternenflug Hochfliegen) und alle
0-Schaden-Aktionen (Buffs, Heals, Schild-Block, Tarnung selbst) dürfen Tarnung NICHT mehr
verbrauchen.

Die übrigen Bugs:
- **Bug 1 — Gamma-Mutant Vorschau**: Die Mission-Encounter-Vorschau zeigt nur 3 statt aller
  Fähigkeiten des Gegners.
- **Bug 2 — Schwerer Kampf-Mech (Exo-Suit) Selbstmord**: Der Mech zündet sein
  „Selbstzerstörungs-Protokoll" (`self_damage: 999`) bereits am Anfang des Kampfes und stirbt
  ohne Vorwarnung, obwohl die Fähigkeit nur unter 20 % HP ausgelöst werden darf.
- **Bug 3 — `/sammlung` Unit-Anzeige fehlt**: Im `/sammlung`-Embed wird die Dust-Menge angezeigt,
  aber nicht die Anzahl der Units oder der Vorschauwert nach einer Wiederbelebung.

Alle Bugfixes müssen zusammen mit der bestehenden `pytest`-Testsuite unter `tests/` als
Verifikations-Baseline grün laufen und einer abschließenden Release-Pipeline (Version-Bump auf
`2.2.15`, Git-Tag `v2.2.15`, Discord-Update-Text, Server-Upload-Schutz-Notiz) folgen.

## Bug Analysis

### Current Behavior (Defect)

Die folgenden Klauseln beschreiben das fehlerhafte Verhalten, **wie es heute in Version 2.2.14
auftritt**.

**Bug 1 — Gamma-Mutant Vorschau (`Mission-Encounter-Preview`)**

1.1 WHEN ein Spieler die Encounter-Vorschau für „Gamma-Mutant" in `Operation Broken Timeline`
öffnet THEN zeigt das Embed im Feld „Fähigkeiten" maximal 3 Einträge, obwohl der Gegner mehr
deklarierte Attacks/Passives hat, die in der Vorschau gelistet sein sollten.

1.2 WHEN ein Mission-Gegner mehr als 3 Attacks deklariert oder neben Attacks zusätzliche Passives
besitzt THEN werden die Einträge ab Index 4 in der Vorschau abgeschnitten und der Spieler erhält
keine Information über diese Fähigkeiten.

**Bug 2 — Schwerer Kampf-Mech (Exo-Suit) Selbstmord**

2.1 WHEN der Bot-Encounter „Schwerer Kampf-Mech" am Kampfanfang seine Attacke wählt UND seine HP
über 20 % seiner Max-HP liegen THEN wählt die Bot-KI dennoch
„Selbstzerstörungs-Protokoll" (`self_damage: 999`, `conditional_self_hp_below_pct: 0.2`) und
fügt sich tödlichen Selbstschaden zu, weil das Feld `conditional_self_hp_below_pct` in der
Bot-Auswahl­logik nicht respektiert wird.

2.2 WHEN „Schwerer Kampf-Mech" über 20 % HP hat UND `Selbstzerstörungs-Protokoll` aktuell nicht
auf Cooldown ist THEN ist diese Attacke trotz nicht erfüllter HP-Bedingung als „verfügbar"
gelistet und kann (auch via `bot_priority` oder Max-Damage-Score) gewählt werden.

**Bug 3 — `/sammlung` zeigt keine Unit-Anzahl und keine Wiederbelebungs-Vorschau**

3.1 WHEN ein Spieler `/sammlung` ausführt THEN zeigt das Embed nur die Infinitydust-Menge im
Feld „💎 Infinitydust", aber kein Feld für die Anzahl der vom Spieler besessenen Units.

3.2 WHEN ein Spieler `/sammlung` ausführt UND mindestens 1 Unit besitzt THEN gibt es im Embed
keine Information darüber, wie viele Units nach einer Boss-Wiederbelebung verbleiben würden.

**Bug 4 — Tarnung wird durch 0-Schaden-Aktionen konsumiert (Sternenflug Phase 1)**

4.1 WHEN ein Verteidiger den Status `stealth` (Tarnung) hat UND ein Angreifer eine 0-Schaden-
Phase eines `airborne_two_phase`-Angriffs (Sternenflug Phase 1, Fliegen Phase 1, Flug der
Knöchelflügel Phase 1) ausführt THEN konsumiert die Engine die Tarnung des Verteidigers, obwohl
Phase 1 keinen Schaden zufügt.

4.2 WHEN ein Verteidiger den Status `stealth` hat UND der Angreifer eine reine Buff-, Heil-,
Schild-Block- oder eigene Tarnung-Aktion mit `damage: [0, 0]` und keinem Schaden-erzeugenden
Effekt einsetzt THEN konsumiert die Engine die Tarnung, obwohl der eingehende Angriff dem
Verteidiger keinen Schaden zugefügt hätte.

4.3 WHEN die Tarnung-Konsumierungslogik im aktuellen Code-Pfad
(`services/combat_runner.py` Zeilen ~1016–1039 und gespiegelt in `bot.py` Zeilen ~5116–5148,
~6179–6211, ~12629–12656, ~13404–13428) ausgeführt wird THEN wird `consume_stealth(defender_id)`
auch im `elif`-Zweig aufgerufen (in dem `actual_damage` zwar nicht auf 0 forciert wird, aber der
eingehende Angriff schon vorher 0 Schaden hatte oder die Tarnung gar nicht zur Verteidigung nötig
war).

### Expected Behavior (Correct)

Diese Klauseln definieren das Soll-Verhalten nach dem Fix.

**Bug 1 — Gamma-Mutant Vorschau**

5.1 WHEN ein Spieler die Encounter-Vorschau für einen Mission-Gegner öffnet THEN soll das Embed
im Feld „Fähigkeiten" ALLE Attacks des Gegners listen (nicht nur die ersten 3 oder 4).

5.2 WHEN ein Gegner mehr Attacks deklariert hat als die Vorschau-Funktion bisher zugelassen hat
THEN soll die Vorschau dynamisch alle Einträge anzeigen und nur durch Discord's Embed-Field-
Längenlimit (1024 Zeichen mit `...`-Trunkierung) begrenzt werden, nicht durch ein hartcodiertes
Attack-Limit.

**Bug 2 — Schwerer Kampf-Mech (Exo-Suit) Selbstmord**

6.1 WHEN „Schwerer Kampf-Mech" eine Attacke wählt UND `conditional_self_hp_below_pct` für eine
Attacke definiert ist UND die aktuellen HP > `max_hp * conditional_self_hp_below_pct` sind THEN
soll die Bot-KI diese Attacke aus der Kandidaten-Liste ausschließen.

6.2 WHEN „Schwerer Kampf-Mech" unter 20 % seiner Max-HP fällt THEN darf
„Selbstzerstörungs-Protokoll" weiterhin gewählt werden (das ist die designierte Last-Stand-
Mechanik) und soll dann den deklarierten Schaden + Selbstschaden anwenden.

**Bug 3 — `/sammlung` Unit-Anzeige**

7.1 WHEN ein Spieler `/sammlung` ausführt UND er mindestens 1 Unit besitzt THEN soll das Embed
direkt unterhalb des Infinitydust-Feldes ein Feld „🪙 Unit" (oder analoges Item-Emoji aus
`items.py`) im selben Stil anzeigen mit:
```
Anzahl: Nx
Aktuell: (Anzahl Unit)
Danach: (Anzahl Unit − Wiederbelebungskosten, geclamped auf 0)
```
Die Wiederbelebungskosten müssen aus dem existierenden Konstanten-Pfad gelesen werden
(`items.get_item_by_id("unit")` → `effects[].kind == "boss_revive"` → `cost`, default 3).

7.2 WHEN ein Spieler 0 Units besitzt THEN soll das Unit-Feld weggelassen werden (analog zum
heutigen Dust-Verhalten bei 0 Dust), oder optional mit `Aktuell: 0 / Danach: 0` angezeigt
werden — je nach Konsistenz mit dem Dust-Feld.

7.3 WHEN das Unit-Feld angezeigt wird THEN soll KEIN Cooldown-Text und KEIN Timer angezeigt
werden, weil die Wiederbelebung jederzeit verfügbar ist, solange der Spieler ≥ Kosten Units hat.

**Bug 4 — Tarnung nur bei tatsächlichem Schaden konsumieren**

8.1 WHEN ein Angreifer eine Aktion ausführt UND der Verteidiger Tarnung hat UND der Angriff
würde nach allen Regeln (multi_hit, blind, evade, force_min, force_max, damage_buff,
attack_multiplier, damage-overrides, ignore_defense/shield/unblockable) `actual_damage > 0`
gegen den Verteidiger erzeugen THEN SOLL die Engine die Tarnung des Verteidigers konsumieren
und den Treffer auf 0 Schaden setzen, sodass `attack_hits_enemy = False` und
`miss_reason = "durch Tarnung"`.

8.2 WHEN ein Angreifer eine Aktion ausführt UND der Verteidiger Tarnung hat UND der Angriff
würde `actual_damage == 0` erzeugen (z. B. weil `damage = [0, 0]`, weil es Phase 1 eines
`airborne_two_phase`-Angriffs ist, weil es ein reiner Buff/Heal/Schild/Tarnung-Cast ist, oder
weil ein vorgelagerter Modifikator den Schaden auf 0 reduziert hat) THEN SOLL die Engine die
Tarnung des Verteidigers NICHT konsumieren und der Statuseffekt soll für die nächste
schadensverursachende Eingabe erhalten bleiben.

8.3 WHEN ein Angriff `ignore_defense`, `ignore_shield` oder `unblockable` gesetzt hat ODER
`guaranteed_hit` aktiv ist THEN gilt unverändert die heutige Regel: Tarnung blockt den Treffer
nicht und wird nicht konsumiert.

### Unchanged Behavior (Regression Prevention)

Folgende Verhaltensweisen müssen durch die Fixes unberührt bleiben.

**Generell**

9.1 WHEN bestehende Pytest-Tests unter `tests/` (insbesondere `test_combat_rules.py`,
`test_battle_state.py`, `test_card_validation.py`, `test_smoke.py`,
`test_alpha_smoke.py`) ausgeführt werden THEN SOLL CONTINUE TO die gesamte Suite grün laufen.

**Bug 1**

9.2 WHEN `_build_attack_info_lines` für eine Heldenkarte mit ≤ 4 Attacks aufgerufen wird (z. B.
`Iron-Man`, `Black Widow`, `Captain America`) THEN SOLL CONTINUE TO genau dieselben Zeilen wie
heute zurückgeben, damit Hero-Embed, Reward-Embed und Mission-Battle-Embed unverändert bleiben.

9.3 WHEN das Embed-Field-Limit von 1024 Zeichen überschritten wird THEN SOLL CONTINUE TO die
bestehende `[:1021] + "..."`-Trunkierung in `_add_attack_info_field` greifen.

**Bug 2**

9.4 WHEN ein anderer Mission- oder Story-Gegner Attacks ohne `conditional_self_hp_below_pct`
deklariert THEN SOLL CONTINUE TO die Bot-KI diese Attacks unverändert auswählen.

9.5 WHEN „Schwerer Kampf-Mech" unter 20 % HP fällt UND `Selbstzerstörungs-Protokoll` nicht auf
Cooldown ist THEN SOLL CONTINUE TO die Attacke verfügbar sein und der Selbstzerstörungs-Effekt
am Spieler 45 Schaden und am Mech `self_damage: 999` (Tod) auslösen.

**Bug 3**

9.6 WHEN ein Spieler 0 Karten und 0 Dust besitzt THEN SOLL CONTINUE TO die heutige
„Du hast noch keine Karten in deiner Sammlung."-Antwort gesendet werden.

9.7 WHEN ein Spieler `/sammlung` mit > 0 Dust ausführt THEN SOLL CONTINUE TO das
Infinitydust-Feld mit demselben Format („💎 Infinitydust", `Anzahl: Nx`, Thumbnail) wie heute
gerendert werden.

9.8 WHEN ein Spieler in einer Mission stirbt UND eine Boss-Wiederbelebung angeboten bekommt
THEN SOLL CONTINUE TO die `MissionBossReviveView` mit dem aus `items.py` gelesenen `cost`
funktionieren — dieselbe Konstante wird für die `/sammlung`-Vorschau benutzt.

**Bug 4**

9.9 WHEN ein Verteidiger Tarnung hat UND ein normaler Damage-Angriff (z. B. Repulsor-Strahlen,
Hieb, Standard-Attacke) trifft UND `actual_damage > 0` THEN SOLL CONTINUE TO die Tarnung
konsumiert werden und der Treffer auf 0 reduziert werden.

9.10 WHEN ein Verteidiger Tarnung hat UND ein Angriff mit `ignore_defense` / `ignore_shield` /
`unblockable` ausgeführt wird THEN SOLL CONTINUE TO der Angriff durch die Tarnung gehen und
Tarnung weiterhin (aktueller Status quo) bestehen bleiben — die heutige Logik ist hier korrekt
und wird nicht angefasst.

9.11 WHEN ein Verteidiger `airborne` (Flugphase) hat UND ein Angreifer eine 0-Schaden-Aktion
ausführt THEN SOLL CONTINUE TO die Flugphase die Aktion verfehlen und ggf. konsumiert werden,
weil `airborne` semantisch dem nächsten Angriff (egal ob Damage oder 0-Damage) ausweicht. Der
bestehende Test `test_real_flow_no_unfair_block_when_tarnung_followup_misses` sowie die
Flugphase-Verbrauchstests bleiben unverändert grün. **Nur Tarnung-Konsumierung wird angepasst,
Flugphase-Konsumierung NICHT.**

9.12 WHEN ein Multi-Hit-Angriff mehrere Treffer mit jeweils > 0 Schaden ausführt UND der
Verteidiger Tarnung hat THEN SOLL CONTINUE TO die Tarnung beim ersten schadensverursachenden
Treffer konsumiert werden (heutiges Verhalten der ersten Tarnung-Konsumierung pro Angriff).
