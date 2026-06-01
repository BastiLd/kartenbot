# 🗂️ Update v2.3.12 — Gegner-Cooldowns (Mission) & Kampf-Log gefixt

## 🐛 Gegner-/Boss-Cooldowns frieren nicht mehr ein
In Missionen wurden die Cooldowns des Gegners **nur dann** heruntergezählt, wenn der
Bot gerade eine Spezial-Attacke einsetzte. Sobald alle Specials auf Cooldown standen
und der Bot nur noch seine **Standardattacke** nutzte, wurden die Cooldowns **nie**
weiter gesenkt – sie froren ein (z. B. Maestro dauerhaft auf `2 / 2 / 5`), und der Boss
konnte nur noch die Standardattacke spielen.

**Fix:** Die Bot-Cooldowns werden jetzt **genau einmal pro Bot-Zug** gesenkt – in allen
Pfaden:
- normaler Bot-Zug (egal ob Standard- oder Spezial-Attacke),
- Bot-Zug ohne verfügbare Attacke (Skip),
- Bot-Zug, der durch **Betäubung** ausgesetzt wird.

So tauen gesperrte Spezial-Fähigkeiten wieder auf und der Gegner kämpft wie vorgesehen.

## 🐛 „Für diesen Kampf ist kein Log verfügbar" behoben
Nach einer Mission lieferte „Kampf-Log per DM" oft keinen Log. Der Missions-Log wurde
nie zwischengespeichert (`_battle_log_text_cache` blieb leer), während die PvP-Variante
das schon tat.

**Fix:** Der Log-Text wird jetzt bei jeder Log-Aktualisierung zwischengespeichert –
auch wenn die Log-Nachricht zwischenzeitlich gelöscht wurde oder ein Bearbeiten
fehlschlägt. Da der Cache zusätzlich in der Session gespeichert wird, übersteht der
Log auch einen Bot-Neustart. „Kampf-Log per DM" liefert nun den vollständigen Log.

## ✅ Qualität
- 3 neue Regressionstests (`tests/test_mission_cooldown_log.py`): Bot-Cooldown sinkt pro
  Bot-Zug auch bei Standardattacke, Spezial wird nach genug Runden wieder verfügbar,
  Log-Cache funktioniert ohne Nachricht.
- Gesamte Testsuite grün (**373 passed**).
- Encoding-/Umlaut-Check über das ganze Repo: keine kaputten Umlaute.
- Benutzernamen mit Sonderzeichen (z. B. `/` oder `_`) werden korrekt und unverändert
  angezeigt – kein zusätzliches Zeichen, keine ungewollte Markdown-Formatierung.
