# 🃏 Update v2.3.0 — Boss-Balance, Belohnungen & Kampf-Komfort

Dieses Update bündelt Boss-Balancing, neue Belohnungen und mehrere Komfort-Funktionen.

## ⚔️ Boss-Balance neu justiert
- **Maestro**: Tyrannen-Schlag 14–20, Trophäensaal-Raub +10 Bonus, Gamma-Eruption 26–35.
- **M.O.D.O.K.**: Gedankenstrahl 12–20, Gehirn-Explosion 25, „Berechnete Heilung" heilt **15 HP** – oder **30 HP**, wenn du zuvor eine Spezialfähigkeit eingesetzt hast.
- **Green Goblin**: Goblin-Handschuh 14–18, Gleiter-Ramme nur noch **6 Rückstoß**, Kürbisbomben-Teppich 3×8.
- **Kingpin**: Stockhieb 13–17, Bestechungs-Versuch heilt **30 HP** (wenn du zuletzt 0 Schaden gemacht hast) bzw. **35 HP**, Zermalmender Griff **26** (Ziel ≥ 60 HP) bzw. **38** Schaden.
- **Agatha**: Chaos-Energie-Ball 11, Darkhold-Fluch 10 + **die nächste heilende Fähigkeit heilt 0 HP**.
- **Lakei 3** von MODOK, Green Goblin und Agatha wurde um ~15 % abgeschwächt (HP + Damage).

## 💎 Infinitydust-Belohnungen
- Jeder besiegte **Lakei** und jeder **Boss** bringt zusätzlich **+1 Infinitydust** (Auszahlung erst bei erfolgreichem Mission-Abschluss).
- Eine bereits besessene Daily-Reward-Karte gibt **+1 Infinitydust** extra. Maximal **5 Infinitydust** pro voller Standard-Mission.

## 🕒 Cooldown-Anzeige
- Verfügbare Fähigkeiten zeigen in der Vorschau jetzt ihren Cooldown als Suffix, z. B. `Gamma-Eruption (3CD)`.

## ⚡ Boss-Spezial-Hervorhebung
- Boss-Spezialfähigkeiten erscheinen einheitlich hervorgehoben: `⚡ **Name** — Effekt`.

## 🖼️ Kompaktere Embeds
- Dust-/Infinitydust-Bilder erscheinen jetzt ausschließlich als **Thumbnail**, nicht mehr als großes Bild.

## 🛠️ Dev-Panel
- Maintenance / Beta / Alpha zeigen im Bestätigungsdialog jetzt den **aktuellen Status** und den geplanten Übergang an, z. B. „Maintenance ist aktuell **AKTIV** → wird **DEAKTIVIERT**".

## ⚙️ Konfiguration
- Neue zentrale Datei `namenconfig.py` mit Feature-Toggles (`boss_switch_enabled`, `name_normalization_enabled`).
- Benutzernamen mit Sonderzeichen (`MFU-_-is_da`) werden überall korrekt angezeigt.

---

### 🔧 Technisch / Grundlagen (in diesem Release vorbereitet)
- Neues Modul `services/mission_rewards.py` (Infinitydust-Akkumulator).
- Neues Modul `services/afk_tracker.py` + SQLite-Tabelle `afk_timers` (persistentes AFK-Markierungssystem).
- Neue Helfer in `services/battle.py`: Cooldown-Label-Renderer und Boss-Spezial-Renderer.
- Umfangreiche Tests: `test_boss_balance`, `test_cooldown_display`, `test_mission_rewards`, `test_afk_tracker_invariants`, `test_mode_confirm`.
