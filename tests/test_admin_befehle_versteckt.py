"""Admin-Befehle sind für normale Nutzer unsichtbar (default_permissions).

Wer einen neuen Befehl anlegt, muss ihn hier eintragen — als Spieler- oder
als Admin-Befehl. Ein Admin-Befehl ohne default_permissions fällt sonst
niemandem auf, bis ihn ein Spieler in der Liste sieht.
"""
from __future__ import annotations

import pytest

import bot

# Befehl -> das Discord-Recht, das man braucht, um ihn zu sehen.
ADMIN_BEFEHLE = {
    "entwicklerpanel": "administrator",
    "bot-status": "administrator",
    "intro-zurücksetzen": "administrator",
    "sammlung-ansehen": "administrator",
    "test-bericht": "administrator",
    "karte-geben": "administrator",
    "dust": "administrator",
    "lödust": "administrator",
    "invite-limit": "administrator",
    "op-verwaltung": "administrator",
    "statistik": "administrator",
    "stats_e": "administrator",
    "design-geben": "administrator",
    "design-entziehen": "administrator",
    "level-einrichten": "administrator",
    "level-rolle": "administrator",
    "level-kanal": "administrator",
    "level-vorschau": "administrator",
    "level-offen": "administrator",
    "kanal-freigeben": "manage_guild",
    "konfigurieren": "manage_guild",
}

# Für alle sichtbar.
SPIELER_BEFEHLE = {
    "täglich", "eingeladen", "verbessern", "sammlung", "design", "anfang",
    "mission", "geschichte", "kampf", "level", "einladungen",
}


def _befehle() -> dict:
    return {c.name: c for c in bot.bot.tree.get_commands()}


def test_jeder_befehl_ist_eingeordnet():
    namen = set(_befehle())
    unbekannt = namen - set(ADMIN_BEFEHLE) - SPIELER_BEFEHLE
    assert not unbekannt, f"Bitte in ADMIN_BEFEHLE oder SPIELER_BEFEHLE eintragen: {sorted(unbekannt)}"
    fehlend = (set(ADMIN_BEFEHLE) | SPIELER_BEFEHLE) - namen
    assert not fehlend, f"Eingetragen, aber nicht angemeldet: {sorted(fehlend)}"


@pytest.mark.parametrize("name,recht", sorted(ADMIN_BEFEHLE.items()))
def test_admin_befehl_ist_versteckt(name, recht):
    befehl = _befehle()[name]
    rechte = befehl.default_permissions
    assert rechte is not None, f"/{name} hat keine default_permissions"
    assert getattr(rechte, recht) is True, f"/{name} braucht {recht}"
    assert befehl.guild_only is True, f"/{name} ist nicht guild_only"


@pytest.mark.parametrize("name", sorted(SPIELER_BEFEHLE))
def test_spieler_befehl_bleibt_sichtbar(name):
    assert _befehle()[name].default_permissions is None
