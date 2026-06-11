# 📄 Update v2.3.20 — Feedback-Buttons reagieren wieder zuverlässig

Bugfix aus den Live-Logs vom 09.06.: Der Bug-Melden-Button nach einem Kampf konnte mit
`404 Unknown interaction` abstürzen.

## 🐞 Fix: „Es gab einen Bug"-Button (Kampf-Feedback)
- Der Button hat **zuerst** den kompletten Kampf-Log per DM an den Entwickler geschickt und
  **danach** erst im Thread geantwortet. Bei langen Logs (mehrere DM-Nachrichten) lief dabei
  das **3-Sekunden-Limit** von Discord-Interaktionen ab — die Antwort schlug mit
  `404 Unknown interaction` fehl und der Klicker bekam **keine Reaktion** auf seinen Klick.
- Jetzt antwortet der Button **sofort** (Formular-Link erscheint direkt), die Log-DM und die
  Analytics werden erst danach verschickt.

## 🛡️ Gleiches Muster in allen Kampf-Feedback-Buttons abgesichert
- **„Kampf-Log per DM"**: Bei langen Logs wird die Interaktion jetzt zuerst bestätigt
  (`defer`), bevor die DM-Nachrichten rausgehen — vorher konnte auch hier das 3-Sekunden-Limit
  reißen.
- **„Es gab keinen Bug"** und **„Thread schließen"**: Antwort kommt jetzt vor dem
  Analytics-Schreibvorgang.
- Alle Buttons der Feedback-View nutzen jetzt den abgesicherten Antwort-Helper
  (`send_interaction_response`): Eine bereits abgelaufene Interaktion erzeugt nur noch eine
  Warnung im Log statt eines Crash-Reports.

## ✅ Qualität
- **411 Tests grün** (+ 428 Subtests), Test für die Feedback-View an die neue
  Antwort-Reihenfolge angepasst.
