# 🗂️ Update v2.3.9 — AFK-Erinnerungen jetzt auch in PvP-Kämpfen

## ⏰ AFK-Markierung in laufenden Kämpfen (PvP)
Bisher gab es die AFK-Erinnerung nur für **offene Herausforderungen** und in
**Missions-Threads**. Jetzt wird auch ein **1v1-Kampf** überwacht: Wer zu lange
am Zug ist, wird im Kampf-Thread automatisch angepingt.

**Wann wird gepingt (gemessen ab Beginn des aktuellen Zugs):**
- **Runde 1 & 2:** nach **4 Stunden** → nur der **aktive Spieler**.
- **Ab Runde 3:**
  - **2 h** → aktiver Spieler
  - **3 h** → **beide** Spieler
  - **4 h** → aktiver Spieler
  - **6 h** → **beide** Spieler
  (maximal 4 Erinnerungen pro Runde)

Jeder ausgeführte Zug (auch ein durch **Betäubung** ausgesetzter Zug) setzt den
Timer zurück und schaltet auf die nächste Runde. Bei **Sieg/Niederlage** und bei
**Abbruch** wird der Timer wieder entfernt. Bot-Kämpfe (Solo gegen den Bot) lösen
keine PvP-Pings aus – dafür gibt es weiterhin die Missions-Erinnerung.

## 🔧 Technisch
- Kampfstart legt einen AFK-Timer an (`create_battle_state`, Herausforderer zuerst aktiv).
- Neue Helfer in `services/afk_tracker.py`: `load_state` und `touch_battle_turn`
  (lädt den persistierten Timer, schaltet via `on_action` auf die nächste Runde,
  speichert) – funktioniert dadurch auch nach einem Bot-Neustart.
- Der bestehende AFK-Loop (alle 5 Minuten, Restart-fest über die SQLite-Tabelle
  `afk_timers`) deckt die neuen Kampf-Timer automatisch mit ab.

## ✅ Tests
- Neuer DB-Roundtrip-Test `tests/test_afk_battle_wiring.py`: Anlegen → Zug →
  Zug → Eskalation ab Runde 3 → Löschen → No-Op auf fehlendem Timer.
- Gesamte Testsuite grün (367 passed).
