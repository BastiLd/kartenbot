# Aufgabe für Claude Opus — Discord Bot v2.3.0

## Kontext

Du arbeitest an einem deutschen Discord-Bot für ein Marvel-Karten-Sammelspiel (Codename „Karten").
Der Bot ist in Python geschrieben und nutzt `discord.py`. Dein Arbeitsordner ist das Repo-Root:
`d:\Cursour\Discord Bot\`

Die zentrale Datei ist `bot.py` (~17.000 Zeilen). Weitere wichtige Verzeichnisse/Dateien:
- `botcommands/` — Slash-Commands (admin, player, gameplay)
- `botcore/` — Hilfsfunktionen (name_utils.py, feature_config.py, …)
- `services/` — Business-Logik (battle.py, battle_state.py, user_data.py, runtime_store.py, …)
- `mission_enemies.py` — Boss- und Lakei-Werte aller Missionen
- `karten.py` — Karten-Definitionen
- `db.py` — SQLite-Datenbankzugriff (`kartenbot.db`)
- `tests/` — pytest-Testsuite

**Aktuelle Version in bot.py:** `__version__ = "2.2.15"`
**Zielversion dieses Auftrags:** `v2.3.0`

---

## Was bereits fertig ist

Die Spezifikation für v2.3.0 liegt unter `.kiro/specs/v2-3-0-update/` (requirements.md, design.md, tasks.md).
**Lies diese Dateien als erstes vollständig**, bevor du anfängst.

Aus der tasks.md sind folgende Tasks bereits als ✅ erledigt markiert:
- **Task 1** — `namenconfig.py` + Loader (`botcore/feature_config.py`) + Tests
- **Task 2** — Benutzernamens-Normalisierung (`normalize_user_display`) + Tests
- **Task 3** — `/karte_geben` Multi/Single Verifikation + Tests
- **Task 4** — „Dust geben" und Lödust UI-Parität + Tests
- **Task 5** — Grant-Card-Pfad konsolidiert (`services/card_grant.py`)
- **Task 6** — `/verbessern` komplett überarbeitet + PBT-Tests

Verifiziere kurz, dass diese tatsächlich im Code vorhanden sind — wenn etwas fehlt, hol es nach.

---

## Was du jetzt tun sollst

Arbeite die folgenden noch offenen Tasks **in dieser Reihenfolge** ab. Halte dich exakt an die
Anforderungen aus `requirements.md` und die Implementierungsdetails aus `design.md`.

### Task 7 — Boss-Balance-Werte aktualisieren

**7.1 Maestro-Werte in `mission_enemies.py`**
- `Tyrannen-Schlag`: damage-Range `[14, 20]` (bisher 20-20)
- `Trophäensaal-Raub`: bonus_damage `+10` (bisher +15), bleibt 50/50 Zufall zwischen Schild (bis 20 blockt) und Bonus-Damage
- `Maestros Hohn`: unverändert (next_attack_damage_override → 0 Schaden)
- `Gamma-Eruption`: damage-Range `[26, 35]` (bisher 40-40)
- Lakeien-Wellen unverändert lassen (Req. 16.5, 16.6)

**7.2–7.5** (MODOK, Green Goblin, Kingpin, Agatha) sind laut tasks.md ✅ — verifiziere trotzdem die Werte in `mission_enemies.py` gegen die Tabelle in `design.md` Abschnitt 12 und korrigiere Abweichungen.

**7.6 Neue Effect-Handler in `services/battle.py`**

Implementiere folgende neue Effekt-Typen (sofern noch nicht vorhanden):
- `next_player_heal_negation` — Agatha Darkhold-Fluch: die nächste Heal-Fähigkeit des Spielers heilt 0 HP, Effekt läuft danach ab
- `set_player_special_to_max_cooldown` — Agatha Hexen-Sabbat: wenn Spieler in dieser Runde einen Special eingesetzt hat, wird dessen Cooldown auf konfigurierten Maximalwert gesetzt
- `cooldown_lockout_one_round` — MODOK System-Hack: in der nächsten Spielerrunde sind nur Standardangriffe wählbar (alle CD-Fähigkeiten gesperrt)
- `conditional_heal_based_on_last_round_damage` — Kingpin Bestechungs-Versuch: heilt 30 wenn Spieler letzte Runde 0 Schaden gemacht hat, sonst 35
- `conditional_damage_based_on_player_hp` — Kingpin Zermalmender Griff: 26 Schaden wenn Spieler ≥60 HP, sonst 38
- `recoil_on_next_special` — Goblin Gleiter-Ramme: 6 Recoil-Schaden auf Spieler beim nächsten Spieler-Spezialangriff (1 Charge, dann weg)

Prüfe ob bestehende Effekte (z. B. `counter_flat`, `special_lock`, `conditional_enemy_hp_below_pct`) semantisch bereits passen — wenn ja, nutze/erweitere sie statt neue zu erfinden.

**7.7 Integrationstests** in `tests/test_boss_balance.py`
- Pro Boss-Skill: Damage-Range-Asserts (Maestro Tyrannen-Schlag 14-20, Gamma-Eruption 26-35 usw.)
- Bedingte Effekte testen (Kingpin Heilung 30 vs 35, Kingpin Griff 26 vs 38, MODOK Healing 15 vs 30)

---

### Task 8 — Cooldown-Anzeige + Boss-Spezial-Hervorhebung

**8.1 + 8.2** `_format_attack_label(attack, is_on_cooldown) -> str` in `services/battle.py` oder Helper:
- Fähigkeit verfügbar + cooldown > 0 → `"{name} ({n}CD)"`
- Fähigkeit auf Cooldown → nur `name` (bleibt ausgegraut, kein Suffix)
- Cooldown = 0 oder nicht gesetzt → nur `name`
- Alle Vorschau- und Auswahl-Listen umstellen (`_add_attack_info_field`, `BattleView.SkillSelect` etc.)
- Logs/History bleiben ohne Suffix

**8.3** `render_boss_special_activation(boss_name, ability_name, effect_text) -> str`:
- Format: `⚡ **{ability_name}** — {effect_text}`
- Aus jedem Boss-Special-Effect-Handler aufrufen
- Falls Name oder Effekt fehlen → kein Render, nur `logging.warning(...)`

**8.4** Tests in `tests/test_cooldown_display.py`:
- cooldown=0, cooldown=3 verfügbar, cooldown=3 auf Cooldown

---

### Task 9 — Boss-Karten-Wechsel mit voller Auswahl

**9.1–9.4** in `bot.py`:

Extrahiere eine gemeinsame Helper-Funktion `_build_owned_card_options(user_id) -> list[SelectOption]`:
- Nutzt `get_user_karten` → `_filter_owned_cards_for_current_mode` → `group_owned_cards_by_base`
- Ursprünglich gewählte Karte mit Marker `(aktuell)` als erste Option

Stelle `MissionNewCardSelectView` auf diesen Helper um (zeigt ALLE Karten, nicht nur die ursprüngliche).

Implementiere `_start_fresh_boss_battle(user_id, selected_card_name, mission_state)`:
- HP der gewählten Karte auf konfigurierten Max-Wert setzen
- Cooldowns leeren
- Alle Buffs/Debuffs/DoT-Effekte entfernen
- Bei Auswahl der ursprünglichen Karte: bisheriger State läuft weiter (kein Reset)

Toggle-Check: `if not boss_switch_enabled(): direkt Boss-Kampf starten, kein Menü`
Wechsel-Menü nur bei `seltenheit == "Boss"` (nie bei Lakei-Encountern)

**9.5** Tests in `tests/test_boss_card_switch.py`:
- Toggle ON: alle Karten erscheinen, ursprüngliche markiert
- Toggle OFF: kein Menü, direkt Kampf
- Wechsel auf neue Karte: HP voll, Cooldowns leer
- Beibehalten: State-Continuity
- Lakei-Encounter → kein Menü

---

### Task 10 — Infinitydust-Belohnungssystem

**10.1** Erstelle `services/mission_rewards.py`:
```python
@dataclass
class MissionRewardAccumulator:
    user_id: int
    mission_id: str
    infinitydust: int = 0
    daily_card_bonus_pending: bool = False

    def on_lakai_defeated(self): self.infinitydust += 1
    def on_boss_defeated(self): self.infinitydust += 1
    def on_daily_card_already_owned(self): self.daily_card_bonus_pending = True
    def total(self) -> int:
        return self.infinitydust + (1 if self.daily_card_bonus_pending else 0)

async def commit_on_mission_success(acc): ...  # add_infinitydust aufrufen
async def discard_on_mission_failure(acc): pass  # nichts auszahlen
```

**10.2** In den Mission-Flow einbauen (`MissionBattleView`):
- Mission-Start: `mission_state["reward_accumulator"] = MissionRewardAccumulator(...)`
- Lakei-Sieg: `acc.on_lakai_defeated()`
- Boss-Sieg: `acc.on_boss_defeated()`
- Mission-Erfolg: `await commit_on_mission_success(acc)` vor End-Embed
- Mission-Verlust/Abbruch: `discard_on_mission_failure(acc)`

**10.3** Daily-Duplikat-Bonus (außerhalb Mission): in `bot.py` Daily-Pfad (~Zeile 3248) — wenn Karte schon im Besitz → `await add_infinitydust(user_id, 1)` zusätzlich

**10.4** Falls Mission eine Daily-verknüpfte Reward-Karte hat und Spieler sie schon besitzt: `acc.on_daily_card_already_owned()`

**10.5** Tests in `tests/test_mission_rewards.py`:
- Lakei → +1, Boss → +1, Daily-Duplikat → +1
- Volle Standard-Mission cap = 5 (3 Lakeien + Boss + Daily-Bonus)
- Abbruch/Verlust → 0 ausgezahlt
- PBT-Property: `total <= 5` für jede Standard-Mission

---

### Task 11 — Thumbnail-Audit (Image → Thumbnail)

Alle Dust/Infinitydust/Unit-Bilder sollen ausschließlich im `thumbnail`-Slot erscheinen, nie als großes `image`.

**11.1 + 11.2** Audit in `bot.py`:
- Zeilen ~3249, ~7887, ~7902, ~7904 und alle weiteren `set_image`-Aufrufe für `infinitydust`/`unit`/Dust-Bilder
- `_apply_item_media`-Default auf `image=False, thumbnail=True` für diese Item-Typen
- Karten-Bilder im Kampfkontext bleiben als großes Bild — nur Dust/Unit/Infinitydust umstellen

**11.3** Datei `MANUAL_TEST_v2_3_0.md` mit visueller Checkliste der zu prüfenden Flows (Daily, Mission-Reward, /karte_geben, Dev-Tools, Lödust)

---

### Task 12 — Mode-Confirmation-Dialog mit Status

In `bot.py` Dev-Panel Buttons für Maintenance/Beta/Alpha:

**12.1 + 12.2** Bei Klick: aktuellen Status holen und in den Dialog einbauen:
```
Maintenance ist aktuell **AKTIV** → wird **DEAKTIVIERT**
```
Template in `game_ui_texts.MODE_CONFIRM_TEMPLATE = "{mode_name} ist aktuell **{current}** → wird **{transition}**"`

**12.3** Abbruch-Pfad lässt Status unverändert (Verifikation bestehender Code)

**12.4** Tests in `tests/test_mode_confirm.py`:
- AKTIV → Text enthält „AKTIV → wird DEAKTIVIERT"
- NICHT AKTIV → Text enthält „NICHT AKTIV → wird AKTIVIERT"

---

### Task 13 — Cancel-Buttons (Challenge + Kampf)

**13.1** In `FightChallengeView`: neuer roter `CancelButton` für Challenger UND Acceptor
- `interaction_check` lässt nur diese beiden zu (kein Dritter)
- Klick → Challenge schließen, beide informieren

**13.2** In `BattleView`: neuer roter `KampfAbbrechenButton` für beide Spieler
- Klick → Battle-State `cancelled`, Abbruch-Embed im Thread, Thread archivieren

**13.3** AFK-Tracker bei Cancel sofort löschen: `await afk_tracker.delete_state(battle_id)` — **vor** jeglicher weiterer Cleanup-Verarbeitung

**13.4** Tests in `tests/test_cancel_buttons.py`:
- Challenger Cancel vor Annahme → Challenge geschlossen
- Acceptor Cancel im Kampf → Thread archiviert, AFK-State weg
- Dritter User → geblockt

---

### Task 14 — AFK-Markierungssystem

**14.1** SQLite-Tabelle `afk_timers` in `db.py::init_db` anlegen:
```sql
CREATE TABLE IF NOT EXISTS afk_timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    battle_id TEXT NOT NULL UNIQUE,
    thread_id INTEGER,
    challenger_id INTEGER NOT NULL,
    acceptor_id INTEGER NOT NULL,
    active_player_id INTEGER,
    round_number INTEGER NOT NULL DEFAULT 0,
    round_started_at INTEGER NOT NULL,
    last_action_at INTEGER NOT NULL,
    pings_sent_mask INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_afk_battle_id ON afk_timers(battle_id);
```

**14.2** Neues Modul `services/afk_tracker.py` mit:
- `AfkState` Dataclass
- `evaluate_pings(state, now) -> list[Ping]` — pure function (deterministisch!)
- `tick(bot, state, now)` — sendet fällige Pings, setzt Bits, persistiert
- `on_action(state, actor_id, now)` — Reset bei neuem Zug
- `delete_state(battle_id)` — für Cancel/Ende
- `restore_all_states()` — für Bot-Start

Ping-Schwellen:
- Challenge offen: bei 4h → Acceptor pingen (einmalig)
- Runde 1+2: bei 4h → aktiver Spieler pingen (einmalig)
- Ab Runde 3: 2h → aktiv, 3h → beide, 4h → aktiv, 6h → beide (Max 4 Pings/Runde)
- Pings per Discord-Mention `<@id>` + kurze Erinnerung im Thread

**14.3** asyncio-Task `afk_tracker_loop()` in `bot.py` `setup_hook`/`on_ready`:
- Alle 5 Minuten alle aktiven States aus DB laden → `tick(...)` aufrufen

**14.4** Verdrahtung im Challenge/Kampf-Lifecycle:
- Challenge erstellt → `afk_tracker.create_challenge_state(...)`
- Challenge angenommen → `delete_state(...)` + `create_battle_state(...)`
- Spielzug → `afk_tracker.on_action(state, actor_id, now)`
- Battle-Ende/Cancel → `afk_tracker.delete_state(...)`

**14.5** Tests: serialize → deserialize → `evaluate_pings` ergibt gleiche Menge

**14.6** PBT-Tests in `tests/test_afk_tracker_invariants.py` (hypothesis):
- Idempotenz: gleicher State + mehrere tick-Aufrufe → max 1 Ping pro Schwelle
- Ping-Cap: ab Runde 3 max 4 Pings/Runde
- Reset: nach `on_action` gilt `pings_sent_mask == 0`
- Restart-Equivalenz: vor/nach Neustart gleiche Ping-Menge für gleiche Inputs

---

### Task 15 — Lakei-3-Abschwächung MODOK / Goblin / Agatha

**15.1** Führe `python simulate.py` (falls vorhanden) mit aktuellen Lakei-3-Werten aus.
- Ziel: ~15 % Reduktion auf HP und/oder mindestens eine Damage-Quelle für den dritten Lakei jeder dieser drei Missionen
- Falls kein Simulator: Werte manuell auf −15 % runden und in `mission_enemies.py` setzen

**15.2** Werte in `mission_enemies.py` commiten mit Code-Kommentar (Datum + Reduktion in %)

---

### Task 16 — Release v2.3.0

**16.1** Versions-Bump:
- `bot.py`: `__version__ = "2.3.0"`
- `README.md` Versions-Abschnitt aktualisieren

**16.2** Vor-Release-Verifikation:
- `python -m pytest` — alle Tests müssen grün sein
- Linter sauber

**16.3** Commit auf Branch `main`:
- Titel EXAKT: `release: v2.3.0 - boss switch, /verbessern overhaul, AFK system, balance`
- Prüfe vorher: Titel muss Pattern `^release:\s*v2\.3\.0` erfüllen, sonst Abbruch

**16.4** Push und Tag:
- `git push -u origin main`
- Tag-Konflikt-Check: `git tag -l v2.3.0` — falls vorhanden → ABBRUCH, kein Force
- `git tag v2.3.0 && git push origin v2.3.0`

**16.5** Update-Bericht erstellen:
- Datei `release_notes_v2.3.0.md` mit allen umgesetzten Punkten
- Stil: Deutsch, Discord-tauglich, Markdown
- Schreibe auch eine kurze Zusammenfassung in `.kiro/release_notes.md`

---

## Wichtige Verhaltensregeln

1. **Lese zuerst** `.kiro/specs/v2-3-0-update/requirements.md`, `design.md` und `tasks.md` vollständig.
2. **Verifiziere** die als ✅ markierten Tasks kurz im Code, bevor du mit Task 7 anfängst.
3. **Keine Massen-Refactors** — nur punktuelle Änderungen, bestehende Tests dürfen nicht brechen.
4. **Karten-Anpassungen** (Gamma Mutant, Exo-Suit, Tarnung, Sammlungs-Anzeigen) sind **nicht** Teil von v2.3.0 — lass sie in Ruhe. (Die sind in einem separaten Bugfix-Spec, der schon erledigt ist.)
5. **Destruktive Git-Operationen** (force push, tag löschen) nur mit expliziter Bestätigung.
6. **Aktualisiere tasks.md** nach Abschluss jeder Hauptaufgabe (markiere ✅).
7. Wenn du dir bei einer Anforderung unsicher bist, lies noch einmal die entsprechende Requirement-Nummer in `requirements.md`.

---

## Zusammenfassung: Offene Haupt-Tasks

| # | Task | Beschreibung |
|---|------|-------------|
| 7.1 | Maestro-Werte | mission_enemies.py anpassen |
| 7.6 | Effect-Handler | services/battle.py neue Effekte |
| 7.7 | Boss-Balance-Tests | tests/test_boss_balance.py |
| 8 | Cooldown-Anzeige | _format_attack_label, Boss-Spezial-Highlight |
| 9 | Boss-Karten-Wechsel | MissionNewCardSelectView, frischer Start |
| 10 | Infinitydust | services/mission_rewards.py |
| 11 | Thumbnail-Audit | set_image → thumbnail für Dust/Unit |
| 12 | Mode-Confirm | Status im Bestätigungsdialog |
| 13 | Cancel-Buttons | FightChallengeView + BattleView |
| 14 | AFK-Tracker | services/afk_tracker.py + SQLite |
| 15 | Lakei-3-Balance | MODOK/Goblin/Agatha Lakei 3 abschwächen |
| 16 | Release | Version bump, pytest, commit, tag, push |

Viel Erfolg!
