# Release v2.3.4 – Hotfix: /verbessern Absturz

Behebt einen Live-Absturz beim Verbessern-Flow. Der Bug steckte schon seit dem
`/verbessern`-Umbau in v2.3.0 drin und hat nichts mit dem v2.3.3-Aufräumen zu tun.

## 🐞 Behoben
- **`/verbessern` (Verbessern-Button) stürzt nicht mehr ab.** Der Fuse-Flow rief
  intern drei Namen auf (`_filter_owned_cards_for_current_mode`,
  `FuseCardSelectView`, `ALPHA_FEATURE_DISABLED_TEXT`), die nicht an die
  Command-Schnittstelle durchgereicht waren – das führte zu einem
  `AttributeError`, sobald man den Button gedrückt hat. Die Namen sind jetzt
  korrekt registriert.

## 🛡️ Vorbeugung
- Neuer Test `tests/test_command_api_parity.py`: prüft automatisch, dass jeder
  `module.*` / `api.*`-Zugriff in den Command-Modulen auch wirklich an der
  Command-Schnittstelle existiert. Solche „funktioniert im Test, kracht erst
  beim Klick"-Lücken fallen damit künftig sofort auf.

## ✅ Qualität
- Komplette Test-Suite grün: 366 passed, 428 subtests.

## Server-Update
Anhaken (bleiben erhalten): `kartenbot.db`, `.env`, `bot_token.txt`, `bot.log`,
`Simulation Files/`. Code-Dateien nicht anhaken.
