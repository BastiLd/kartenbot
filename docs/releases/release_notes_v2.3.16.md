# 🛠️ Update v2.3.16 — AFK-Fehler beim Start & Karten ansehen

Zwei kleine, aber spürbare Verbesserungen: kein Fehler-Spam mehr beim Bot-Start und
`/sammlung ansehen` zeigt jetzt auch die Karten-Werte anderer Spieler.

## 🧹 Kein AFK-Fehler mehr beim Start
- Ein AFK-Erinnerungs-Timer, dessen Kanal/Thread inzwischen **gelöscht** wurde, warf beim
  Start (und alle 5 Min) einen `Unknown Channel`-Fehler. Solche verwaisten Timer werden
  jetzt automatisch **entfernt**, statt immer wieder zu scheitern.
- Vorübergehende Aussetzer (z. B. Netzwerk) werden ruhig protokolliert und der Timer
  bleibt erhalten.

## 🔍 `/sammlung ansehen` mit „Anzeige"-Button
- Wenn du dir die Sammlung eines anderen Spielers ansiehst, gibt es jetzt — wie bei
  deiner eigenen `/sammlung` — einen **„Anzeige"-Button**. Damit kannst du einzelne
  Karten auswählen und ihre **Werte** sehen (Leben, Seltenheit, Attacken inkl. Schaden,
  Cooldown und Varianten).

## ✅ Qualität
- **398 Tests grün** (+ 428 Subtests) — inkl. neuer Regressionstests für das Aufräumen
  toter AFK-Timer.
