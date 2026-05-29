# Implementation Plan

## Overview

Dieser Plan setzt die 22 Anforderungen aus `requirements.md` und das `design.md` in 16 Hauptaufgaben mit ~60 Unteraufgaben um. Reihenfolge folgt der Dependency-Pyramide: zuerst die Konfig- und Helper-Schicht (`namenconfig.py`, Namens-Normalisierung), dann Tools (Multi/Single-Vereinheitlichung, `/verbessern`-Überarbeitung), dann Mission-Logik (Boss-Balance, Cooldown-Anzeige, Wechsel, Belohnungen), dann UI-Audits (Thumbnail, Mode-Confirm), dann Lifecycle (Cancel-Buttons, AFK-Tracker), Lakei-3-Abschwächung nach Sim, abschließend Release. Karten-Anpassungen (Gamma Mutant, Exo-Suit, Tarnung, Sammlung) sind nicht enthalten und folgen separat.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "wave": 1,
      "tasks": ["1.1", "1.2", "1.3"],
      "rationale": "Konfig- und Loader-Schicht ohne Abhängigkeiten."
    },
    {
      "wave": 2,
      "tasks": ["2.1", "2.2", "2.3", "7.1", "7.2", "7.3", "7.4", "7.5", "11.1", "11.2", "11.3", "12.1", "12.2", "12.3", "12.4"],
      "rationale": "Namens-Normalisierung, Boss-Balance-Daten, Thumbnail-Audit und Mode-Confirm sind alle unabhängig voneinander und nutzen nur Wave 1."
    },
    {
      "wave": 3,
      "tasks": ["3.1", "3.2", "3.3", "6.1", "6.2", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "6.9", "7.6", "7.7", "8.1", "8.2", "8.3", "8.4", "9.1", "9.2", "9.3", "9.4", "9.5", "10.1", "10.2", "10.3", "10.4", "10.5"],
      "rationale": "/verbessern, Boss-Effekt-Handler, Cooldown-Anzeige, Boss-Karten-Wechsel, Infinitydust hängen von Wave 1+2 ab."
    },
    {
      "wave": 4,
      "tasks": ["4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.3"],
      "rationale": "Dust-Multi/Single und Grant-Card konsolidieren bauen auf den /karte_geben-Verifikationstests auf."
    },
    {
      "wave": 5,
      "tasks": ["13.1", "13.2", "13.3", "13.4", "14.1", "14.2", "14.3", "14.4", "14.5", "14.6"],
      "rationale": "Cancel-Buttons und AFK-Tracker brauchen die Battle-View-Anpassungen aus früheren Wellen."
    },
    {
      "wave": 6,
      "tasks": ["15.1", "15.2"],
      "rationale": "Lakei-3-Abschwächung nach Sim-Lauf gegen finale Boss-Werte."
    },
    {
      "wave": 7,
      "tasks": ["16.1", "16.2", "16.3", "16.4", "16.5"],
      "rationale": "Release nach allen anderen Aufgaben."
    }
  ]
}
```

## Tasks

- [x] 1. Konfiguration & Namens-Layer
- [x] 1.1 Erstelle `namenconfig.py` im Repo-Root
  - Lege die Datei `namenconfig.py` direkt im Repo-Root an
  - Füge die zwei aktiven Toggles ein: `boss_switch_enabled = True` und `name_normalization_enabled = True`
  - Über jedem Toggle: kommentierter Block mit Wirkung, erlaubten Werten, Default und ON/OFF-Beispiel (siehe Design-Doc Abschnitt 1)
  - Auskommentierter Platzhalter-Block für `card_name_normalization_enabled` mit Hinweis „wird in einem späteren Update aktiviert"
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 1.2 Implementiere Loader für `namenconfig` in `botcore/feature_config.py`
  - Lege neues Modul `botcore/feature_config.py` an
  - Funktion `_load_namenconfig() -> dict` mit Default-Fallback bei fehlender Datei oder fehlendem Eintrag
  - Validierung: nicht-Bool-Werte werden verworfen, Default + Warning-Log
  - Modul-Konstante `NAMENCONFIG` und Helfer `boss_switch_enabled()` / `name_normalization_enabled()`
  - _Requirements: 2.5, 2.6, 2.7_

- [x] 1.3 Schreibe Unit-Tests für den `namenconfig`-Loader
  - Neue Datei `tests/test_namenconfig.py`
  - Testfälle: gültige Werte, fehlende Datei, fehlender Einzel-Eintrag, ungültiger Typ (z. B. Integer statt Bool)
  - Mock von `import namenconfig` per `sys.modules`-Patch
  - _Requirements: 2.5, 2.6, 2.7_

- [x] 2. Benutzernamens-Normalisierung
- [x] 2.1 Erweitere `botcore/name_utils.py` um `normalize_user_display`
  - Neue Funktion, die Markdown-aktive Zeichen (`_`, `*`, `~`, `` ` ``, `>`, `|`) so neutralisiert, dass weder Backslash noch Markdown-Render entsteht (z. B. via Zero-Width-Space vor dem Markdown-Zeichen)
  - Toggle-Check: liefert Original zurück, wenn `name_normalization_enabled()` False ist
  - Sichtbare Zeichen, gleiche Reihenfolge, gleiche sichtbare Länge
  - _Requirements: 9.1, 9.3, 9.4_

- [x] 2.2 Stelle alle bestehenden `safe_display_name`/`escape_display_text`-Aufrufstellen um
  - Audit: `grep_search` nach `safe_display_name|escape_display_text|safe_user_option_label` in `bot.py`, `botcommands/*.py`, `services/battle.py`
  - In jeder Aufrufstelle, die für Embeds, Buttons, Selects, Pings sichtbar ist: `normalize_user_display` aufrufen (mit Toggle-Check)
  - Kein Karten-Namen-Touch (deferred per User-Entscheidung)
  - _Requirements: 9.2, 9.5_

- [x] 2.3 Schreibe Unit-Tests für die Normalisierung
  - Neue Datei `tests/test_name_normalization.py`
  - Inputs: `MFU-_-is_da`, `**bold**`, `~strike~`, `pipe|name`, `> quote`
  - Asserts: kein Backslash im Output, gleiche sichtbare Länge wie Input, Toggle-OFF gibt Input unverändert zurück
  - _Requirements: 9.1, 9.3, 9.5_

- [x] 3. `/karte_geben` Multi/Single Verifikation
- [x] 3.1 Bestandstests für `/karte-geben` Single-Pfad ausbauen
  - In `tests/test_combat_rules.py` oder neuer Datei `tests/test_karte_geben.py`: Test für 1 Karte → 1 Zielperson, korrekte Bestätigungsnachricht, korrekter DB-Eintrag
  - Mock von `add_karte` / `add_karte_amount`
  - _Requirements: 3.1, 3.5_

- [x] 3.2 Bestandstests für `/karte-geben` Multi-Pfad ausbauen
  - Test für N Karten → 1 Zielperson, einzige Bestätigungsnachricht mit Liste
  - Test für Teilfehler: eine Karte schlägt fehl, übrige werden vergeben, Fehler in Bestätigung sichtbar
  - _Requirements: 3.2, 3.3, 3.4, 3.5_

- [x] 3.3 Stelle Bestätigungs-UX an und schließe Lücken
  - Falls Tests zeigen, dass die Bestätigung nicht alle Vergaben/Fehlversuche enthält: `bot.py` (Karte-Geben-Confirm-View) entsprechend ergänzen
  - _Requirements: 3.3, 3.4_

- [x] 4. „Dust geben" und Lödust UI-Parität
- [x] 4.1 UI-Audit Single/Multi für „Dust geben" gegen `/karte_geben`
  - Vergleiche Embed-Struktur, Button-Reihenfolge, Single/Multi-Toggle in `botcommands/admin_commands.py::dust` und in `bot.py` Dev-Panel `give_dust`
  - Notiere Abweichungen
  - _Requirements: 4.1, 4.2_

- [x] 4.2 Vereinheitliche „Dust geben" Multi-Modus
  - Multi-Modus zeigt Schnellauswahl-Buttons {5,10,15,20,25,30}
  - Top-Modal mit ganzzahligem Custom-Betrag (1 ≤ n ≤ 1.000.000)
  - Validierung 0/negativ → ablehnen, Fehlermeldung, kein State-Change
  - Eingabe `0` + aktiver Schnellauswahl-Button → Schnellauswahl gewinnt
  - _Requirements: 4.3, 5.2, 5.3, 5.4, 5.5_

- [x] 4.3 Lödust analog vereinheitlichen
  - Da `/lödust` und `/dust` denselben `run_dust_command_flow(remove=...)` nutzen, automatisch via Aufgabe 4.2 abgedeckt — reiner Verifikations-Schritt
  - Test-Run: Single + Multi für `/lödust`, Custom-Betrag, Validierung
  - _Requirements: 5.1, 5.6_

- [x] 4.4 Tests für Single/Multi und Custom-Betrag
  - Neue Datei `tests/test_dust_commands.py`
  - Single 1 User + Betrag 25 → Saldo +25 (oder −25 bei lödust)
  - Multi 3 User + Betrag 30 → 3 Salden +30
  - Custom-Modal Betrag 7 → 7 funktioniert; Betrag 0 → abgelehnt; Betrag −5 → abgelehnt
  - _Requirements: 4.4, 5.4, 5.5_

- [x] 5. Grant-Card-Pfad konsolidieren
- [x] 5.1 Extrahiere gemeinsame Vergabe-Logik in `services/card_grant.py`
  - Neue Datei `services/card_grant.py`
  - Funktion `grant_cards_to_users(actor_id, target_ids, card_names) -> GrantSummary` (Liste von Erfolgen / Fehlern)
  - Diese Funktion macht: pro (target, card) → `add_karte` + Validierung + Logging
  - _Requirements: 11.1, 11.3_

- [x] 5.2 Verdrahte `/karte-geben` und „Grant Card" auf den gemeinsamen Service
  - In `botcommands/admin_commands.py::karte_geben` (Single/Multi-Pfad): am Ende `grant_cards_to_users` aufrufen
  - In `bot.py` Dev-Panel `action == "grant_card"`: ebenfalls `grant_cards_to_users` aufrufen
  - „Grant Card" setzt Multi-Mode hart auf True, ohne UI-Schalter
  - _Requirements: 11.2, 11.3, 11.4_

- [x] 5.3 Tests für gemeinsamen Code-Pfad
  - In `tests/test_karte_geben.py`: Test-Doppel, dass `/karte_geben` Multi und Dev-Panel „Grant Card" exakt dieselbe `grant_cards_to_users`-Aufruf-Form erzeugen
  - _Requirements: 11.3, 11.4_

- [x] 6. `/verbessern` Überarbeitung
- [x] 6.1 Entferne den oberen Action-Select aus `FuseCardSelectView`
  - In `bot.py`: `FuseCardActionSelect` nicht mehr instanziieren
  - `_render` ohne Action-Row aufbauen
  - Default-`mode = "browse"`, alle Karten direkt sichtbar
  - _Requirements: 6.3_

- [x] 6.2 Pagination im Karten-Auswahl-Schritt sicherstellen
  - Bestehende `prev_button`/`next_button` weiterverwenden
  - Disabled-State auf Seite 1 / letzter Seite korrekt setzen
  - Max 25 Optionen pro Seite (Discord-Limit)
  - _Requirements: 6.4_

- [x] 6.3 Neue Stat-Auswahl-View `FuseStatSelectView`
  - Nach Karten-Auswahl: View zeigt Optionen HP, Damage Attacke 1, Damage Attacke 2 …
  - Optionen werden ausgeblendet, wenn der Stat bereits am Cap ist
  - Hinweis-Embed wenn keine Stat-Aufwertung mehr möglich
  - _Requirements: 6.5, 6.12_

- [x] 6.4 Multiplikator-Auswahl mit dynamischer Filterung
  - Funktion `available_multipliers(stat_value, stat_cap, base_step, dust_balance)` (siehe Design Abschnitt 4)
  - Optionen über `max_by_cap` werden ausgeblendet (Req. 6.7)
  - Optionen, die `dust_balance` übersteigen, bleiben sichtbar aber ausgegraut/deaktiviert (Req. 6.9)
  - HP-Cap 200 erzwingen (Req. 6.8)
  - _Requirements: 6.1, 6.2, 6.6, 6.7, 6.8, 6.9_

- [x] 6.5 Dust-Vorrat vor der Auswahl anzeigen
  - Embed-Header der Multiplikator-View: `💎 Dein Dust-Vorrat: {dust_balance}`
  - _Requirements: 6.10_

- [x] 6.6 Bestätigungs-Embed nach erfolgreicher Aufwertung
  - Vorher/Nachher pro Stat, gekostete Dust-Menge, neuer Saldo
  - _Requirements: 6.11_

- [x] 6.7 Atomares Persistieren mit Rollback
  - Dust-Abzug + Stat-Aufwertung in einer DB-Transaktion
  - Bei Fehler: kein Teil-Update, Fehlermeldung, kein Cap-Überschreiten möglich
  - _Requirements: 6.13_

- [x] 6.8 Edge Case: 0 Karten im Besitz
  - Statt leerem Menü: Hinweis-Embed „Du hast keine Karten zum Aufwerten"
  - _Requirements: 6.14_

- [x] 6.9 PBT-Tests für `/verbessern`-Invarianten
  - Neue Datei `tests/test_verbessern_invariants.py` mit `hypothesis`
  - Properties 5–9 aus Design-Abschnitt Correctness Properties
  - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5_

- [ ] 7. Boss-Balance-Werte aktualisieren
- [ ] 7.1 Maestro-Werte in `mission_enemies.py` aktualisieren
  - Tyrannen-Schlag: damage `[14, 20]`
  - Trophäensaal-Raub bonus_damage `+10`
  - Maestros Hohn unverändert (`next_attack_damage_override`)
  - Gamma-Eruption: damage `[26, 35]`
  - _Requirements: 16.1, 16.2, 16.3, 16.4_

- [x] 7.2 MODOK-Werte in `mission_enemies.py` aktualisieren
  - Wellen-Lakeien Rammstoß `[14, 18]`, Kanone `[20, 24]`
  - Gedankenstrahl `[12, 20]`
  - System-Hack: 15 Schaden + Cooldown-Lockout 1 Runde (existing `special_lock` umetikettieren oder neuen `cooldown_lockout_one_round`-Effect)
  - Berechnete Heilung: `15` base, conditional `30` wenn Spieler in Vorrunde CD-Ability genutzt
  - Gehirn-Explosion: `25` Schaden
  - _Requirements: 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8_

- [x] 7.3 Green-Goblin-Werte in `mission_enemies.py` aktualisieren
  - Wellen MG-Sperrfeuer `[14, 18]`, Hitzegranate `[24, 30]`
  - Goblin-Handschuh `[14, 18]`
  - Gleiter-Ramme: 20 Schaden + 6 Recoil auf nächsten Spieler-Spezial (counter_flat 6)
  - Halluzinogenes Gas: 10 Schaden + 50% Verfehlchance
  - Kürbisbomben-Teppich: 3×8 = 24
  - _Requirements: 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8_

- [x] 7.4 Kingpin-Werte in `mission_enemies.py` aktualisieren
  - Stockhieb `[13, 17]`
  - Sumo-Ansturm 22 + clear_negative_effects
  - Bestechungs-Versuch: 30 wenn Spieler in Vorrunde 0 Schaden, sonst 35; 15 nur Fallback ohne Vorrunde
  - Zermalmender Griff: 26 (≥60HP) / 38 (<60HP)
  - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7_

- [x] 7.5 Agatha-Werte in `mission_enemies.py` aktualisieren
  - Höllenfeuerstoß 24 (Wellen-Lakei)
  - Chaos-Energie-Ball 11
  - Darkhold-Fluch 10 + neuer Effect `next_player_heal_negation`
  - Lila Illusion: evade + counter_flat 15 (existing)
  - Hexen-Sabbat: 35 Schaden + neuer Effect `set_player_special_to_max_cooldown` (Trigger: Spieler nutzte Special in dieser Runde)
  - _Requirements: 20.2, 20.3, 20.4, 20.5, 20.6, 20.7_

- [ ] 7.6 Implementiere neue Effect-Handler in `services/battle.py`
  - `next_player_heal_negation` (Agatha)
  - `set_player_special_to_max_cooldown` (Agatha)
  - `conditional_heal_based_on_last_round_damage` (Kingpin)
  - `conditional_damage_based_on_player_hp` (Kingpin — falls bestehende `conditional_enemy_hp_below_pct` nicht ausreicht)
  - `cooldown_lockout_one_round` (MODOK — falls `special_lock` semantisch nicht passt)
  - `recoil_on_next_special` (Goblin Gleiter-Ramme — kann bestehende `counter_flat` mit `uses=1` und Trigger auf Spieler-Spezial erfüllen)
  - _Requirements: 16.2, 17.5, 17.6, 17.7, 18.5, 19.3, 19.4, 19.6, 19.7, 20.4, 20.7_

- [ ] 7.7 Integrationstests für Boss-Balance
  - Neue Datei `tests/test_boss_balance.py`
  - Pro Boss-Skill: Damage-Range-Asserts, bedingte Effekte (Heilung 30 vs. 35, Damage 26 vs. 38, Cooldown-Reset)
  - _Requirements: 16.1–20.7_

- [ ] 8. Cooldown-Anzeige + Boss-Spezial-Hervorhebung
- [ ] 8.1 Cooldown-Suffix-Renderer in `services/battle.py` oder Helper
  - Funktion `_format_attack_label(attack, is_on_cooldown) -> str`
  - Verfügbar + cooldown > 0: `"{name} ({n}CD)"`
  - Auf Cooldown: nur `name` (grau bleibt grau, kein Suffix)
  - Cooldown = 0 oder nicht gesetzt: nur `name`
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5_

- [ ] 8.2 Aufrufstellen umstellen
  - `_add_attack_info_field` in `bot.py` und alle Skill-Auswahl-Selects (`BattleView.SkillSelect` etc.) auf den neuen Renderer umstellen
  - Logs / History bleiben ohne Suffix (laut Req. 14.2)
  - _Requirements: 14.1, 14.2_

- [ ] 8.3 Renderer für Boss-Spezial-Aktivierungs-Meldung
  - In `services/battle.py`: Funktion `render_boss_special_activation(boss_name, ability_name, effect_text) -> str`
  - Format: `⚡ **{ability_name}** — {effect_text}`
  - Aufruf aus jedem Boss-Special-Effect-Handler (Maestro/MODOK/Goblin/Kingpin/Agatha)
  - Falls Name oder Effekt fehlt: kein Render, stattdessen `logging.warning(...)` (Req. 15.3, 15.4)
  - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

- [ ] 8.4 Tests für Cooldown-Anzeige
  - Datei `tests/test_cooldown_display.py`
  - Cases: cooldown 0, cooldown 3 verfügbar, cooldown 3 auf Cooldown
  - _Requirements: 14.1, 14.3, 14.5_

- [ ] 9. Boss-Karten-Wechsel mit voller Auswahl
- [ ] 9.1 Extrahiere `_build_owned_card_options(user_id)` in `bot.py`
  - Gemeinsame Helper-Funktion, die `get_user_karten` → `_filter_owned_cards_for_current_mode` → `group_owned_cards_by_base` ausführt und `SelectOption[]` baut
  - Ursprünglich gewählte Karte mit Marker `(aktuell)` als erste Option
  - _Requirements: 1.1, 1.2_

- [ ] 9.2 Stelle `MissionNewCardSelectView` auf den neuen Helper um
  - In `MissionNewCardSelectView.__init__`: SelectOptions aus `_build_owned_card_options` statt nur der Mission-Karte
  - _Requirements: 1.1, 1.2_

- [ ] 9.3 Implementiere Frisch-Start-Logik bei Karten-Wechsel
  - Funktion `_start_fresh_boss_battle(user_id, selected_card_name, mission_state)`
  - HP der gewählten Karte auf konfigurierten Max-Wert, Cooldowns geleert, alle Buffs/Debuffs/DoT-Effekte entfernt
  - Bei Auswahl der ursprünglichen Karte: bisheriger State läuft weiter (kein Reset)
  - _Requirements: 1.3, 1.4_

- [ ] 9.4 Toggle-Check + Lakei-Encounter-Schutz
  - Vor dem Anzeigen des Wechsel-Menüs: `if not boss_switch_enabled(): direkt Boss-Kampf starten`
  - Wechsel-Menü erscheint nur bei `seltenheit == "Boss"` (Req. 1.5, 1.7)
  - _Requirements: 1.5, 1.6, 1.7_

- [ ] 9.5 Tests für Boss-Karten-Wechsel
  - Datei `tests/test_boss_card_switch.py`
  - Toggle ON: alle Karten erscheinen, ursprüngliche markiert
  - Toggle OFF: kein Menü, direkt Kampf
  - Wechsel auf neue Karte: HP voll, Cooldowns leer
  - Beibehalten: State-Continuity
  - Lakei-Encounter zeigt kein Menü
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

- [ ] 10. Infinitydust-Belohnungssystem
- [ ] 10.1 Erstelle `services/mission_rewards.py`
  - Klasse `MissionRewardAccumulator` (siehe Design Abschnitt 5)
  - Methoden: `on_lakai_defeated`, `on_boss_defeated`, `on_daily_card_already_owned`, `total`
  - `commit_on_mission_success(acc)` ruft `add_infinitydust`
  - `discard_on_mission_failure(acc)` macht nichts
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 10.2 Verdrahte Akkumulator in den Mission-Flow
  - Mission-Start (`MissionBattleView` Initialisierung): `mission_state["reward_accumulator"] = MissionRewardAccumulator(...)`
  - Encounter-Sieg gegen Lakei: `acc.on_lakai_defeated()`
  - Boss-Sieg: `acc.on_boss_defeated()`
  - Mission-Erfolg: `await commit_on_mission_success(acc)` vor End-Embed
  - Mission-Verlust / Abbruch: `discard_on_mission_failure(acc)`
  - _Requirements: 7.1, 7.2, 7.4, 7.5, 7.6_

- [ ] 10.3 Daily-Karten-Duplikat-Bonus außerhalb Mission
  - In `bot.py` Daily-Pfad (~Zeile 3248): wenn Karte schon im Besitz → `await add_infinitydust(user_id, 1)` zusätzlich zur bestehenden Dust-Umwandlung
  - _Requirements: 7.3, 7.8_

- [ ] 10.4 Daily-Karten-Mission-Bonus innerhalb Mission
  - Falls die Mission eine Daily-verknüpfte Reward-Karte hat und der Spieler diese bereits besitzt: `acc.on_daily_card_already_owned()` aufrufen, max-Cap 5 pro Mission
  - _Requirements: 7.7_

- [ ] 10.5 Tests für Infinitydust-Belohnungen
  - Neue Datei `tests/test_mission_rewards.py`
  - Lakei → +1, Boss → +1, Daily-Duplikat → +1
  - Voll-Mission Cap 5 (3 Lakeien + Boss + Daily-Duplikat-Bonus)
  - Mission-Abbruch / Verlust → 0 ausgezahlt
  - PBT-Property: total <= 5 für jede Standard-Mission
  - _Requirements: 7.7, 7.8, 7.6_

- [ ] 11. Thumbnail-Audit (Image → Thumbnail)
- [ ] 11.1 Audit aller `set_image`-Stellen für Dust- und Unit-Bilder
  - Liste aus Design Abschnitt 6 abarbeiten (bot.py Zeilen 3249, 7887, 7902, 7904 und Folgestellen)
  - Karten-Bilder im Kampfkontext explizit ausnehmen (bleiben groß)
  - _Requirements: 8.1, 8.2_

- [ ] 11.2 Stelle `_apply_item_media`-Defaults um
  - Default `image=False, thumbnail=True` für `infinitydust` und `unit`
  - Aufrufstellen mit explizitem `image=True` einzeln prüfen und auf Thumbnail-only umstellen
  - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [ ] 11.3 Manueller Test-Plan für Thumbnail-Verhalten
  - Datei `MANUAL_TEST_v2_3_0.md` (oder Abschnitt darin) mit visueller Checkliste pro Flow: Daily, Mission-Reward, /karte_geben, Dev-Tools, Lödust
  - _Requirements: 8.4_

- [ ] 12. Mode-Confirmation-Dialog mit Status
- [ ] 12.1 Erweitere `MaintenanceConfirmView`, `AlphaConfirmView`, `BetaConfirmView`
  - Im Dialog-Text aktuellen Status holen und einfügen: „Maintenance ist aktuell **AKTIV** → wird **DEAKTIVIERT**"
  - Template in `game_ui_texts.MODE_CONFIRM_TEMPLATE`
  - _Requirements: 10.1, 10.2_

- [ ] 12.2 Status-Read in den Panel-Action-Handler aufnehmen
  - In `bot.py` Dev-Panel `action == "maintenance_on/off"` / `alpha_*` / `beta_*`: vor Anzeige Status holen (`is_maintenance_enabled` etc.) und in den Confirm-View-Konstruktor übergeben
  - _Requirements: 10.1, 10.2_

- [ ] 12.3 Abbruch-Pfad lässt Status unverändert (Bestand)
  - Verifikation: Cancel-Button sendet `MAINTENANCE_CANCELLED` ohne `set_maintenance_mode` aufzurufen
  - _Requirements: 10.3_

- [ ] 12.4 Tests für Mode-Confirmation-Dialog
  - Datei `tests/test_mode_confirm.py`
  - Pro Modus: Status AKTIV, Confirm-Text enthält „AKTIV → wird DEAKTIVIERT"; Status NICHT AKTIV, Text enthält „NICHT AKTIV → wird AKTIVIERT"
  - _Requirements: 10.1, 10.2_

- [ ] 13. Cancel-Buttons (Challenge + Kampf)
- [ ] 13.1 Cancel-Button in der Challenge-View
  - In `FightChallengeView`: neuer `CancelButton` (red), sichtbar/klickbar für Challenger UND Acceptor
  - `interaction_check` lässt nur diese beiden Rollen zu
  - Bei Klick: Challenge schließen, beide Spieler informieren
  - _Requirements: 12.1, 12.3_

- [ ] 13.2 Cancel-Button in der Kampf-View
  - In `BattleView` (und ggf. `MissionBattleView`): neuer `KampfAbbrechenButton` (red)
  - Sichtbar/klickbar für beide Spieler
  - Bei Klick: Battle-State `cancelled`, Abbruch-Embed im Thread, Thread mit `CANCELLED_THREAD_AUTO_CLOSE_POLICY` archivieren
  - _Requirements: 12.2, 12.4_

- [ ] 13.3 AFK-Tracker bei Cancel sofort löschen
  - Vor jeder Cleanup-Verarbeitung: `await afk_tracker.delete_state(battle_id)`
  - Stellt sicher, dass keine späten Pings gesendet werden
  - _Requirements: 12.5_

- [ ] 13.4 Tests für Cancel-Buttons
  - Datei `tests/test_cancel_buttons.py`
  - Challenger drückt Cancel vor Annahme → Challenge geschlossen, beide informiert
  - Acceptor drückt Cancel im Kampf → Thread archiviert, AFK-Tracker entfernt
  - Dritter User drückt Cancel → Block, Fehlermeldung
  - _Requirements: 12.1, 12.3, 12.4, 12.5_

- [ ] 14. AFK-Markierungssystem
- [ ] 14.1 Erstelle SQLite-Tabelle `afk_timers`
  - Schema aus Design Abschnitt Data Models
  - Migration in `db.py::init_db` aufnehmen (CREATE IF NOT EXISTS, Index auf `battle_id`)
  - _Requirements: 13.8, 13.9_

- [ ] 14.2 Implementiere `services/afk_tracker.py`
  - Datenklasse `AfkState`
  - Pure function `evaluate_pings(state, now)`
  - `tick(bot, state, now)` setzt Bit, sendet Ping, persistiert
  - `on_action(state, actor_id, now)` Reset bei neuem Zug
  - `delete_state(battle_id)` für Cancel/Battle-Ende
  - `restore_all_states()` für Bot-Start
  - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 13.9, 13.10_

- [ ] 14.3 AFK-Ticker als asyncio-Task starten
  - In `bot.py` `setup_hook`/`on_ready`: `asyncio.create_task(afk_tracker_loop())`
  - Loop ruft alle 5 Minuten alle aktiven Zustände aus DB und `tick(...)` auf
  - _Requirements: 13.1, 13.2, 13.4_

- [ ] 14.4 Verdrahtung in Challenge- und Kampf-Lifecycle
  - Challenge-Erstellung: `afk_tracker.create_challenge_state(...)`
  - Challenge-Annahme: `afk_tracker.delete_state(...)` + `create_battle_state(...)`
  - Spielzug: `afk_tracker.on_action(state, actor_id, now)`
  - Battle-Ende / Cancel: `afk_tracker.delete_state(...)`
  - _Requirements: 13.1, 13.2, 13.6, 13.7, 13.10_

- [ ] 14.5 Persistenz testen mit simuliertem Bot-Restart
  - In `tests/test_afk_tracker_invariants.py`: serialize → fresh AfkState aus DB-Inhalt → `evaluate_pings` ergibt gleiche Menge wie vor Serialisierung
  - _Requirements: 13.8, 13.9_

- [ ] 14.6 PBT-Tests für AFK-Tracker-Invarianten
  - Properties 1–4 aus Design Abschnitt Correctness Properties via `hypothesis`
  - Idempotenz, Ping-Cap pro Runde, Reset bei Zug, Restart-Equivalenz
  - _Requirements: 13.1, 13.4, 13.5, 13.6, 13.8, 13.9_

- [ ] 15. Lakei-3-Abschwächung MODOK / Goblin / Agatha
- [ ] 15.1 Sim-Lauf zur Bestimmung konkreter Werte
  - `python simulate.py` mit aktuellen Lakei-3-Werten und mit −15 % HP / −15 % Damage testen
  - Konkrete Zielwerte ableiten und in `mission_enemies.py` setzen
  - _Requirements: 17.1, 18.1, 20.1_

- [ ] 15.2 Werte commiten
  - `mission_enemies.py` für die drei Lakei-3-Encounter aktualisieren
  - Begründung in Code-Kommentar (Sim-Run-Datum + Reduktion)
  - _Requirements: 17.1, 18.1, 20.1_

- [ ] 16. Release v2.3.0
- [ ] 16.1 Versions-Bump
  - In `bot.py`: `__version__ = "2.3.0"`
  - In `README.md` Versions-Abschnitt aktualisieren
  - _Requirements: 22.1, 22.2_

- [ ] 16.2 Vor-Release-Verifikation
  - Alle Test-Suites laufen lassen: `python -m pytest`
  - Linter / Pyright sauber
  - _Requirements: 22.3_

- [ ] 16.3 Commit auf `main`
  - Commit-Titel: `release: v2.3.0 - boss switch, /verbessern overhaul, AFK system, balance`
  - _Requirements: 22.3, 22.4, 22.5_

- [ ] 16.4 Push und Tag
  - `git push -u origin main`
  - Tag-Konflikt-Check: `git tag -l v2.3.0` (falls vorhanden → Abbruch)
  - `git tag v2.3.0 && git push origin v2.3.0`
  - _Requirements: 22.6, 22.7, 22.8, 22.9_

- [ ] 16.5 Update-Bericht erstellen
  - User-stilkonformer Bericht mit allen umgesetzten Punkten (Markdown, deutsch, Discord-tauglich)
  - _Requirements: alle_

## Notes

- Karten-Anpassungen (Gamma Mutant Vorschau-Fähigkeiten, Exo-Suit Selbstmord, Tarnung-Auslösung, Sammlungs-Anzeigen / Wiederbeleben) sind in v2.3.0 nicht enthalten — folgen in einem separaten Spec.
- AFK-Ping-Format und exakte Lakei-3-Werte werden in den jeweiligen Aufgaben (14.4 + 15.1) finalisiert.
- Property-based Tests (Hypothesis) decken die Korrektheits-Eigenschaften aus `design.md` ab.
- Release-Aufgaben (16.x) führen destruktive Git-Operationen nur auf explizite Bestätigung aus.

## Status v2.3.0 (Stand 2026-05-29)

**Erledigt & getestet (alle Tests grün, `python -m pytest` = 347 passed):**
- **7.1** Maestro-Werte (Tyrannen-Schlag 14-20, Trophäensaal +10, Gamma-Eruption 26-35).
- **7.2–7.5** MODOK/Goblin/Kingpin/Agatha Boss-Werte in `mission_enemies.py` korrigiert.
- **7.6** Bedingte Boss-Effekte im Mission-Turn (`bot.py` `MissionBattleView`):
  Kingpin Bestechungs-Versuch 30/35, Zermalmender Griff 26/38 (`reduced_damage_if_player_hp_at_least`),
  MODOK Berechnete Heilung 15/30 (`heal_if_player_used_cd_last_round` + Tracking),
  Agatha Darkhold-Fluch `next_player_heal_negation` (Apply + Consume beim Spieler-Heal).
  *Hinweis:* MODOK System-Hack nutzt weiter `special_lock` (semantisch = alle CD-Abilities gesperrt);
  Agatha Hexen-Sabbat nutzt weiter `special_lock` statt `set_player_special_to_max_cooldown` (offen).
- **7.7** `tests/test_boss_balance.py`.
- **8.1/8.3** `services/battle.py`: `_format_attack_label`, `render_boss_special_activation`.
- **8.2** Cooldown-Suffix in der Fähigkeiten-Vorschau (`_build_attack_info_lines`). *Offen:* Skill-Selects/Buttons.
- **8.4** `tests/test_cooldown_display.py`.
- **10.1** `services/mission_rewards.py` (`MissionRewardAccumulator`, commit/discard).
- **10.5** `tests/test_mission_rewards.py` (inkl. Cap-Property).
- **11.1/11.2** Thumbnail-Audit: `_apply_item_media`-Default ist `image=False, thumbnail=True`;
  Daily-Duplikat (Z. 3259) und Mission-Success-Dust-Embed auf Thumbnail-only umgestellt.
- **11.3** `MANUAL_TEST_v2_3_0.md`.
- **12.1/12.2/12.4** `game_ui_texts.MODE_CONFIRM_TEMPLATE` + `render_mode_confirm`, Dev-Panel-Verdrahtung, `tests/test_mode_confirm.py`.
- **14.1/14.2** SQLite-Tabelle `afk_timers` (`services/db.py`) + Modul `services/afk_tracker.py`
  (pure `evaluate_pings`, `on_action`, Persistenz, `restore_all_states`, `tick`).
- **14.5/14.6** `tests/test_afk_tracker_invariants.py` (Idempotenz, Cap, Reset, Restart-Äquivalenz).
- **15.1/15.2** Lakei-3-Abschwächung MODOK/Goblin/Agatha (~15 % HP + Damage-Ranges), mit Code-Kommentar.
- **16.1** Version-Bump `bot.py __version__ = "2.3.0"` + `README.md`.

**Zweite Welle erledigt & getestet (353 passed):**
- **9.1/9.2/9.4** Boss-Karten-Wechsel: `(aktuell)`-Marker + Reihenfolge in `MissionNewCardSelectView`,
  Toggle-Gate `boss_switch_enabled()` in der Boss-Preview. `tests/test_boss_card_switch.py`.
  (9.3 Fresh-Start ist durch das karten-namen-keyed Cooldown-Carryover emergent: anderer Held => kein Carryover => voll.)
- **10.2/10.3/10.4** Infinitydust-Akkumulator im Mission-Flow: +1 pro Welle (Lakei/Boss),
  Commit bei Mission-Erfolg inkl. Daily-Duplikat-Bonus (Cap 5); Daily-Duplikat außerhalb Mission +1.
- **13.1/13.3** Cancel-Button in der Challenge-View (beide Spieler), AFK-Sofort-Delete. `tests/test_cancel_buttons.py`.
- **14.3** AFK-Ticker-Loop `afk_tracker_loop()` in `on_ready` (alle 5 min).
- **14.4 (Challenge-Teil)** `create_challenge_state` bei Challenge-Erstellung, `delete_state` bei Accept/Decline/Cancel.

**Bewusst offen gelassen (tiefe/undurchsichtige Live-Engine-Interna, Risiko ohne Laufzeit-Test):**
- **8.2** Rest: Cooldown-Suffix auch in den Skill-Auswahl-Buttons (BattleView nutzt eigenes dynamisches Label-Format).
- **13.2** Cancel-Button in `BattleView` (Buttons werden dynamisch über ein nicht extrahiertes System aufgebaut).
- **14.4 (Battle-Teil)** `create_battle_state` bei Annahme + `on_action` pro Spielzug
  (Eingriff in die Turn-Verarbeitung beider Battle-Views).
- **16.2–16.5** Release-Schritte (Commit/Push/Tag auf `main`).
