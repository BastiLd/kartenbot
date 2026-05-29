"""Gemeinsame Vergabe-Logik für Multi-Karten-Grants.

Dieses Modul kapselt die Schleife, die bei `/karte-geben` (Slash-Command) und
beim Dev-Panel-Eintrag „Grant Card" identisch ablaufen muss: für jede
Kombination aus Karte und Ziel-Nutzer wird genau ein Vergabe-Versuch
unternommen, und das Ergebnis wird in drei Eimer einsortiert:

* ``added``   – Karte wurde neu hinzugefügt (Vergabe-Funktion lieferte ``True``).
* ``skipped`` – Karte ist dem System bekannt, der Nutzer besaß sie aber bereits
  (Vergabe-Funktion lieferte ``False`` und ``is_card_known`` war ``True``).
* ``failed``  – Vergabe-Funktion warf eine Exception, oder lieferte ``False``
  bei einer unbekannten Karte.

Die zentrale Funktion :func:`grant_cards_to_users` ist bewusst eine *reine*
Service-Funktion: alle Abhängigkeiten (Karten-Vergabe, Karten-Katalog-Lookup,
optional ein Audit-Hook) werden als Callables hereingereicht. So lässt sich der
Code-Pfad im Test ohne Discord, ohne Datenbank und ohne globale Imports
abdecken, und sowohl der Slash-Command als auch das Dev-Panel rufen exakt
dieselbe Logik auf — funktionale Drift zwischen beiden Pfaden ist damit
ausgeschlossen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

GrantBucket = Literal["added", "skipped", "failed"]


@dataclass(frozen=True)
class GrantOutcome:
    """Ergebnis einer einzelnen Karten-Vergabe an einen Nutzer."""

    user_id: int
    card_name: str
    bucket: GrantBucket


@dataclass
class GrantSummary:
    """Aggregiertes Ergebnis eines Multi-Vergabe-Laufs."""

    outcomes: list[GrantOutcome] = field(default_factory=list)
    total_added: int = 0
    total_skipped: int = 0
    total_failed: int = 0

    def per_user_added(self, user_id: int) -> list[str]:
        """Karten, die für ``user_id`` neu hinzugefügt wurden."""
        return [
            outcome.card_name
            for outcome in self.outcomes
            if outcome.user_id == user_id and outcome.bucket == "added"
        ]

    def per_user_skipped(self, user_id: int) -> list[str]:
        """Karten, die ``user_id`` bereits besaß."""
        return [
            outcome.card_name
            for outcome in self.outcomes
            if outcome.user_id == user_id and outcome.bucket == "skipped"
        ]

    def per_user_failed(self, user_id: int) -> list[str]:
        """Karten, deren Vergabe an ``user_id`` fehlgeschlagen ist."""
        return [
            outcome.card_name
            for outcome in self.outcomes
            if outcome.user_id == user_id and outcome.bucket == "failed"
        ]


async def grant_cards_to_users(
    target_user_ids: list[int],
    card_names: list[str],
    *,
    add_card: Callable[[int, str], Awaitable[bool]],
    is_card_known: Callable[[str], bool],
    on_outcome: Callable[[int, str, str], Awaitable[None]] | None = None,
) -> GrantSummary:
    """Vergibt jede Karte aus ``card_names`` an jeden Nutzer aus ``target_user_ids``.

    Die Schleifen-Reihenfolge entspricht den bestehenden Aufrufstellen in
    ``botcommands/admin_commands.py`` (``/karte-geben``) und ``bot.py``
    (Dev-Panel „Grant Card"): die äußere Schleife läuft über die Karten,
    die innere über die Nutzer.

    Parameter
    ---------
    target_user_ids:
        Discord-User-IDs der Empfänger.
    card_names:
        Vollständige Karten-Namen (inklusive Variante / Style).
    add_card:
        Awaitable-Callable ``(user_id, card_name) -> bool``. Liefert ``True``,
        wenn die Karte tatsächlich hinzugefügt wurde, ``False`` wenn sie
        bereits vorhanden war oder gar nicht hinzugefügt werden konnte.
        Typischerweise :func:`services.user_data.add_exact_card_variant_once`.
    is_card_known:
        Synchrones Prädikat ``card_name -> bool``. Liefert ``True``, wenn die
        Karte im aktuellen Karten-Katalog bekannt ist. Wird gebraucht, um eine
        ``False``-Antwort von ``add_card`` korrekt zu interpretieren: bekannte
        Karte → Nutzer besaß sie schon (``skipped``), unbekannte Karte →
        echter Fehler (``failed``).
    on_outcome:
        Optionaler Audit-Hook. Wird nach jedem Versuch mit
        ``(user_id, card_name, bucket)`` aufgerufen, wobei ``bucket`` einer
        der Strings ``"added"``, ``"skipped"`` oder ``"failed"`` ist.

    Rückgabe
    --------
    :class:`GrantSummary` mit der vollständigen Liste der Einzel-Ergebnisse
    sowie aggregierten Zählern pro Eimer.
    """

    summary = GrantSummary()

    for card_name in card_names:
        card_known = bool(is_card_known(card_name))
        for user_id in target_user_ids:
            bucket: GrantBucket
            try:
                was_added = await add_card(user_id, card_name)
            except Exception:
                bucket = "failed"
            else:
                if was_added:
                    bucket = "added"
                elif card_known:
                    bucket = "skipped"
                else:
                    bucket = "failed"

            summary.outcomes.append(
                GrantOutcome(user_id=user_id, card_name=card_name, bucket=bucket)
            )
            if bucket == "added":
                summary.total_added += 1
            elif bucket == "skipped":
                summary.total_skipped += 1
            else:
                summary.total_failed += 1

            if on_outcome is not None:
                await on_outcome(user_id, card_name, bucket)

    return summary


__all__ = [
    "GrantBucket",
    "GrantOutcome",
    "GrantSummary",
    "grant_cards_to_users",
]
