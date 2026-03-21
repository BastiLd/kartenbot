from __future__ import annotations

from dataclasses import dataclass

import bot
from services.card_validation import validate_cards


EXPECTED_ALPHA_COMMANDS = {
    "anfang",
    "bot-status",
    "eingeladen",
    "entwicklerpanel",
    "geschichte",
    "intro-zurücksetzen",
    "kampf",
    "kanal-freigeben",
    "karte-geben",
    "konfigurieren entfernen",
    "konfigurieren hinzufügen",
    "konfigurieren liste",
    "mission",
    "op-verwaltung",
    "sammlung",
    "sammlung-ansehen",
    "statistik balance",
    "täglich",
    "test-bericht",
    "verbessern",
}


@dataclass(slots=True)
class AlphaSmokeResult:
    name: str
    ok: bool
    details: str


def flatten_command_names(commands, prefix: str = "") -> set[str]:
    names: set[str] = set()
    for command in commands:
        nested = getattr(command, "commands", None)
        if nested:
            names.update(flatten_command_names(nested, prefix=f"{prefix}{command.name} "))
        else:
            names.add(f"{prefix}{command.name}")
    return names


def run_alpha_smoke_checks() -> list[AlphaSmokeResult]:
    results: list[AlphaSmokeResult] = []

    built_bot = bot.create_bot()
    results.append(
        AlphaSmokeResult(
            name="bot-import",
            ok=built_bot is bot.bot,
            details="Bot-Factory liefert die registrierte Bot-Instanz.",
        )
    )

    command_names = flatten_command_names(built_bot.tree.get_commands())
    missing = sorted(EXPECTED_ALPHA_COMMANDS - command_names)
    results.append(
        AlphaSmokeResult(
            name="slash-commands",
            ok=not missing,
            details="Alle erwarteten Alpha-Commands sind registriert." if not missing else f"Fehlend: {', '.join(missing)}",
        )
    )

    validation_cards = bot.karten.all_cards() if hasattr(bot.karten, 'all_cards') else bot.karten
    validation_issues = validate_cards(validation_cards)
    results.append(
        AlphaSmokeResult(
            name="karten-validierung",
            ok=not validation_issues,
            details="karten.py ist valide." if not validation_issues else f"{len(validation_issues)} Validierungsprobleme gefunden.",
        )
    )
    return results


