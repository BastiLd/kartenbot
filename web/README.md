# Kartenbot Web

Eine vollwertige Website zur Steuerung des Kartenbots — zusätzlich zum Docker-Dashboard
unter `website/`, das unverändert weiterläuft.

## Was sie kann

| Bereich | Inhalt |
|---|---|
| **Übersicht** | Zustand des Bots, Laufzeit, Fehler der letzten 24 Stunden, wichtigste Zahlen |
| **Spieler** | Karten, Infinitydust **und Units** geben oder nehmen, Seltenheits-Pakete, Steckbrief, Spielerdaten löschen |
| **Karten** | Der komplette Katalog mit Angriffen und Varianten, durchsuchbar |
| **Statistiken** | Befehle, Helden, Angriffe, Siegquoten, Kartenverteilung, Aktivität nach Uhrzeit |
| **Rollen & Mitglieder** | Jede Rolle unterhalb der Bot-Rolle vergeben, einzeln oder in Massen, mit Trockenlauf, Rechte-Übersicht, Rollen auf Zeit und Verlauf |
| **Server-Analyse** | Chat-Verlauf auswerten und Mitglieder einordnen (Meme-Typ, der Lustige, Helfer, Nachteule, stiller Mitleser …) |
| **Bot-Steuerung** | Wartungsmodus, Alpha, Beta, Kanalfreigaben, Protokoll aller Aktionen |
| **Einstellungen** | Zugang, Netzbereiche, Ollama samt Modell-Finder, Ausführungsart, Darstellung |

## Zugang — drei Schlösser hintereinander

1. **Passwort** (`WEB_PASSWORD`). Ohne gesetztes Passwort bleibt die Seite komplett gesperrt.
2. **Discord-Login.** Standard-Besitzer ist die im Bot hinterlegte ID. Ist keine gesetzt,
   beansprucht der **erste erfolgreiche Login** den Zugang dauerhaft. Zurücksetzen in den
   Einstellungen.
3. **Netzstufe.**
   - *Heimnetz* → darf alles, auch Rollen vergeben, Kick und Bann.
   - *VPN (Tailscale)* → darf alles außer diesen kritischen Aktionen.
   - *Von außen* → gar nichts.

   Bei mehreren Zwischenstationen zählt immer die **schwächste** Stufe der ganzen Kette.
   Ein gefälschter `X-Forwarded-For` kann die Stufe deshalb nie anheben, nur senken.

## Wie die Website mit dem Bot spricht

Reine Datenbank-Arbeit (Karten, Währung, Statistiken, Schalter) erledigt die Website selbst —
mit derselben Buchungslogik wie `services/user_data.py`, damit beide Seiten nie auseinanderlaufen.

Alles, was **Discord** anfassen muss, legt sie als Auftrag in der Tabelle `web_jobs` ab. Der Bot
holt ihn im Sekundentakt ab und führt ihn aus. Dadurch gelten automatisch seine Rechteprüfungen,
die Rangordnung der Rollen und die Wiederholung bei Discord-Sperren. In den Einstellungen lässt
sich stattdessen **Direkt** wählen — dann spricht die Website selbst mit Discord.

## Datenschutz bei der Verlaufs-Analyse

Nachrichteninhalte werden **ausschließlich im Arbeitsspeicher** verarbeitet und sofort verworfen.
Gespeichert werden nur Kennzahlen und Einordnungen (`Bilder-Anteil 41 %`, `Meme-Typ`) — nie ein
Nachrichtentext, nie ein Zitat, nie ein Dateiname.

Jede Einordnung trägt ihre Begründung mit sich, damit nachvollziehbar bleibt, warum jemand als
etwas eingestuft wurde.

## Starten

### Örtlich, mit Doppelklick

Datei `Kartenbot-Web starten.bat` im Projektordner. Beim ersten Mal richtet sie sich selbst ein.

### Als Docker-Container

```bash
docker compose -f web/docker-compose.yml up -d --build
```

### Über WebHafen

1. Container wie oben starten (Port 8090).
2. In WebHafen eine neue Website anlegen, Typ **Statisch**.
3. Bei **API-Ziel** die Adresse des Backends eintragen, z. B. `192.168.68.10:8090`.
4. Den Inhalt von `web/static/` in die Website hochladen.

Oberfläche und Backend laufen dann unter **einer** Adresse — dadurch entfallen die typischen
Browser-Probleme, und es muss nur ein Port freigegeben werden.

## Einstellungen

Pflicht ist nur `WEB_PASSWORD`. Alles Weitere steht in `.env.example`. Werte, die du im Betrieb
ändern willst (Ollama-Adresse, Modell, Wortliste, Netzbereiche, Ausführungsart), stellst du
direkt in der Oberfläche ein — sie landen in der Tabelle `web_settings`.

## Neue Tabellen

Rein additiv, bestehende Tabellen des Bots bleiben unberührt:

`web_settings`, `web_auth`, `web_jobs`, `web_audit`, `guild_feature_toggles`,
`scan_runs`, `member_profiles`, `mod_events`, `role_grants`, `web_discord_cache`

## Tests

```bash
python -m pytest tests/test_web_dashboard.py
```

Prüft Wortliste, Einordnungslogik, Nachrichtenauswertung und die Rangordnung der Rollen.
