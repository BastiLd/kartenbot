# 🗂️ Update v2.3.11 — Automatische Crash-Reports für alle Slash-Commands

## 🛟 Crash-Reports / Error-Logs
Bisher wurde dem Entwickler nur bei **Kampf-/Missions-Fehlern** automatisch ein
Log geschickt. Jetzt gilt das für **jeden Slash-Command**: Stürzt ein Befehl
unerwartet ab, bekommt der Owner sofort eine DM mit

- dem **Befehl** (`/name`),
- **Guild** und **Kanal/Thread**,
- dem **User** (Name + ID),
- der **Exception** und dem **vollständigen Traceback**.

Der ausführende Nutzer sieht eine kurze, unauffällige Meldung
(„❌ … Der Entwickler wurde automatisch benachrichtigt.").

**Wichtig:** Erwartete Ablehnungen (gesperrter Kanal, Wartungsmodus,
Katabump-Rate-Limit über `interaction_check`) sind **kein** Absturz und lösen
**keinen** Report aus – es kommt also kein Spam.

## 🔧 Technisch
- Neuer globaler Handler `KatabumpCommandTree.on_error` (ignoriert
  `app_commands.CheckFailure`, packt `CommandInvokeError.original` aus).
- `_send_basti_log_dm` akzeptiert jetzt einen optionalen `title` (Default
  unverändert „Kampf-/Missionslog"), damit Reports klar betitelt sind.

## ✅ Tests
- Neuer Test `tests/test_crash_report.py`: CheckFailure wird ignoriert; echte
  Fehler erzeugen genau einen Owner-Report mit Traceback + Nutzer-Hinweis;
  `CommandInvokeError.original` wird korrekt ausgepackt.
- Gesamte Suite grün (370 passed).

---

### Hinweis zur Update-0.2.2-Verifikation
Die übrigen 0.2.2-Punkte wurden gegen den Code geprüft und sind bereits
enthalten (Dust aus Duplikaten, Welle-3-Staub, Upgrade-Kosten 5, Balancing,
permanente Cooldown-Anzeige, Boss-Spezial-Markierung, HP in `/sammlung`,
Schadensanzeige bei Schutz, AFK-Markierung). Die Gegner-Cooldowns bleiben als
**immer sichtbares Feld** (bewusst, statt Button). Die 50 Start-Staub wurden
bereits manuell vergeben.
