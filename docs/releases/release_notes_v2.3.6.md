# 🛠️ Hotfix v2.3.6 — Dev-Panel „Give dust" & „Grant card" repariert

## 🐛 Behoben
- **Dev-Panel → Dev Tools → „Give dust" und „Grant card" stürzten ab** mit
  `404 Not Found (Unknown Webhook)`. Ursache: Die beiden Multi-Auswahl-Flows
  riefen sofort `interaction.followup.send(...)` auf, obwohl die Interaction
  vorher nie bestätigt (deferred) wurde – ein Followup ohne vorherige Antwort
  ist ungültig.
- **Fix:** Beide Flows bestätigen die Interaction jetzt zuerst per
  `defer_interaction(interaction, ephemeral=True)` (gleiches Muster wie der
  funktionierende `/verbessern`-Dust-Dialog). Danach funktionieren die
  Mehrfach-Nutzer-Auswahl, die Rollen-Auswahl, die Mengen-/Karten-Auswahl,
  die Bestätigung und das Verteilen wie vorgesehen.

## ℹ️ Hinweis
- Tritt der Fehler erneut auf, prüfe, ob der Bot **doppelt** läuft (zwei
  Instanzen mit demselben Token) – das kann ebenfalls „Unknown Webhook"-Races
  auslösen. Es sollte immer nur **eine** Instanz online sein.

Gesamte Testsuite grün (366 passed).
