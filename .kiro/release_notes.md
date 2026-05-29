# 🎯 Update v2.3.0

Sammel-Update: Boss-Balance, Infinitydust-Belohnungen, Cooldown-Anzeige, Boss-Spezial-Hervorhebung, Thumbnail-Konsistenz, Mode-Confirm mit Status, Boss-Karten-Wechsel (Toggle + Marker), Challenge-Cancel + AFK-Markierungssystem (Grundgerüst).

- **Boss-Balance** (Maestro/MODOK/Goblin/Kingpin/Agatha) exakt nach Spec; Lakei 3 von MODOK/Goblin/Agatha um ~15 % abgeschwächt.
- **Infinitydust**: +1 pro Lakei/Boss (Auszahlung bei Mission-Erfolg, Cap 5), +1 bei bereits besessener Daily-/Reward-Karte.
- **Cooldown-Anzeige** `(<n>CD)` in der Fähigkeiten-Vorschau; **Boss-Spezial-Renderer** `⚡ **Name** — Effekt`.
- **Thumbnails**: Dust/Infinitydust nur noch als Thumbnail.
- **Dev-Panel**: Maintenance/Beta/Alpha-Dialog zeigt aktuellen Status + Übergang.
- **Boss-Wechsel**: Toggle `boss_switch_enabled` + `(aktuell)`-Marker.
- **Challenge-Cancel** für beide Spieler; **AFK-Tracker** (SQLite `afk_timers`, pure `evaluate_pings`, 5-min-Ticker) inkl. Challenge-Lifecycle.
- 353 Tests grün.

Detaillierte Notes: `release_notes_v2.3.0.md`. Offene Punkte (BattleView-Cancel, Battle-AFK-`on_action`): siehe `.kiro/specs/v2-3-0-update/tasks.md` (Status-Abschnitt).

## Was anhaken beim Update?
Damit Nutzerdaten erhalten bleiben, **anhaken**: `kartenbot.db`, `.env`, `bot_token.txt`, `bot.log`, `Simulation Files/`. Code-Dateien **nicht** anhaken, damit das Update sie aktualisiert.
