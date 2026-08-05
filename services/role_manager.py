"""Rollen vergeben und entziehen — Bot-Seite.

Discord lässt einen Bot nur Rollen **unterhalb** seiner eigenen höchsten Rolle
verwalten, und nur mit dem Recht „Rollen verwalten". Beides wird hier geprüft,
bevor irgendetwas passiert.

Das Ergebnis wird pro Mitglied in vier Töpfe einsortiert — genauso wie
services/card_grant.py es für Karten macht:
    gesetzt / schon_vorhanden / nicht_erlaubt / fehlgeschlagen
"""
from __future__ import annotations

import asyncio
import logging

import discord

# Discord bremst aus, wenn zu schnell hintereinander geändert wird.
PAUSE_BETWEEN_CALLS = 0.35


class RoleCheckError(Exception):
    """Grundsätzliches Problem, das die ganze Aktion verhindert."""


def bot_member(guild: discord.Guild) -> discord.Member | None:
    return guild.me


def may_manage_roles(guild: discord.Guild) -> bool:
    me = bot_member(guild)
    return bool(me and me.guild_permissions.manage_roles)


def role_blocked_reason(guild: discord.Guild, role: discord.Role) -> str | None:
    """Warum darf der Bot diese Rolle nicht vergeben? None = er darf."""
    me = bot_member(guild)
    if me is None:
        return "Der Bot ist auf diesem Server nicht (mehr) Mitglied."
    if role.is_default():
        return "Die @everyone-Rolle hat jeder automatisch."
    if role.managed:
        return "Diese Rolle gehört zu einer App oder Abo-Funktion und lässt sich nicht vergeben."
    if not me.guild_permissions.manage_roles:
        return "Dem Bot fehlt das Recht „Rollen verwalten“ auf diesem Server."
    if role >= me.top_role:
        return (f"Die Rolle „{role.name}“ steht auf oder über der höchsten Rolle des Bots "
                f"(„{me.top_role.name}“) — Discord lässt das nicht zu. "
                f"Ziehe die Bot-Rolle in den Servereinstellungen weiter nach oben.")
    return None


def overview(guild: discord.Guild) -> dict:
    """Alle Rollen mit der Angabe, ob der Bot sie vergeben darf."""
    me = bot_member(guild)
    rollen = []
    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        grund = role_blocked_reason(guild, role)
        rollen.append({
            "id": str(role.id),
            "name": role.name,
            "position": role.position,
            "color": role.color.value,
            "mitglieder": len(role.members),
            "verwaltet": role.managed,
            "vergebbar": grund is None,
            "grund": grund,
        })
    return {
        "rollen": rollen,
        "bot_hoechste_rolle": me.top_role.name if me else None,
        "bot_position": me.top_role.position if me else 0,
        "darf_rollen_verwalten": may_manage_roles(guild),
    }


async def apply_changes(guild: discord.Guild, changes: list[dict], action: str, reason: str,
                        *, progress=None, cancelled=None) -> dict:
    """Führt die Änderungen aus.

    ``changes``   Liste aus {"user_id": ..., "role_id": ...}
    ``progress``  optionale asynchrone Funktion(erledigt, gesamt)
    ``cancelled`` optionale asynchrone Funktion() -> bool, für den Abbruch-Knopf
    """
    if action not in ("add", "remove"):
        raise RoleCheckError("Die Aktion muss 'add' oder 'remove' sein.")
    if not may_manage_roles(guild):
        raise RoleCheckError("Dem Bot fehlt das Recht „Rollen verwalten“ auf diesem Server. "
                             "Bitte in den Servereinstellungen ergänzen.")

    gesetzt: list[dict] = []
    schon_so: list[dict] = []
    nicht_erlaubt: list[dict] = []
    fehlgeschlagen: list[dict] = []
    abgebrochen = False

    gesamt = len(changes)
    for index, change in enumerate(changes, start=1):
        if cancelled is not None and await cancelled():
            abgebrochen = True
            break

        eintrag = {"user_id": str(change["user_id"]), "role_id": str(change["role_id"])}
        role = guild.get_role(int(change["role_id"]))
        member = guild.get_member(int(change["user_id"]))

        if role is None:
            nicht_erlaubt.append({**eintrag, "grund": "Diese Rolle gibt es nicht mehr."})
        elif member is None:
            nicht_erlaubt.append({**eintrag, "grund": "Diese Person ist nicht mehr auf dem Server."})
        else:
            eintrag["name"] = member.display_name
            eintrag["role_name"] = role.name
            grund = role_blocked_reason(guild, role)
            hat_schon = role in member.roles
            if grund:
                nicht_erlaubt.append({**eintrag, "grund": grund})
            elif (action == "add" and hat_schon) or (action == "remove" and not hat_schon):
                schon_so.append(eintrag)
            else:
                try:
                    if action == "add":
                        await member.add_roles(role, reason=reason[:400])
                    else:
                        await member.remove_roles(role, reason=reason[:400])
                    gesetzt.append(eintrag)
                except discord.Forbidden:
                    nicht_erlaubt.append({
                        **eintrag,
                        "grund": "Discord hat abgelehnt — meist steht die Rolle doch über der "
                                 "Bot-Rolle oder dem Bot fehlt ein Recht.",
                    })
                except discord.HTTPException as exc:
                    logging.warning("Rollenänderung fehlgeschlagen (%s/%s): %s",
                                    member.id, role.id, exc)
                    fehlgeschlagen.append({**eintrag, "grund": f"Discord meldet: {exc}"})
                await asyncio.sleep(PAUSE_BETWEEN_CALLS)

        if progress is not None:
            await progress(index, gesamt)

    return {
        "gesetzt": gesetzt,
        "schon_vorhanden": schon_so,
        "nicht_erlaubt": nicht_erlaubt,
        "fehlgeschlagen": fehlgeschlagen,
        "abgebrochen": abgebrochen,
        "zusammenfassung": {
            "geaendert": len(gesetzt),
            "ohne_wirkung": len(schon_so),
            "abgelehnt": len(nicht_erlaubt),
            "fehler": len(fehlgeschlagen),
        },
    }


async def set_timeout(guild: discord.Guild, user_id: int, until, reason: str) -> dict:
    """Auszeit setzen oder aufheben (``until=None`` hebt sie auf)."""
    member = guild.get_member(int(user_id))
    if member is None:
        raise RoleCheckError("Diese Person ist nicht auf dem Server.")
    me = bot_member(guild)
    if not me or not me.guild_permissions.moderate_members:
        raise RoleCheckError("Dem Bot fehlt das Recht „Mitglieder in Auszeit schicken“.")
    if member.top_role >= me.top_role:
        raise RoleCheckError(f"„{member.display_name}“ steht in der Rangordnung auf oder über "
                             "dem Bot — Discord lässt das nicht zu.")
    await member.timeout(until, reason=reason[:400])
    return {"user_id": str(user_id), "name": member.display_name,
            "bis": until.isoformat() if until else None}


async def set_nickname(guild: discord.Guild, user_id: int, nickname: str | None,
                       reason: str) -> dict:
    member = guild.get_member(int(user_id))
    if member is None:
        raise RoleCheckError("Diese Person ist nicht auf dem Server.")
    me = bot_member(guild)
    if not me or not me.guild_permissions.manage_nicknames:
        raise RoleCheckError("Dem Bot fehlt das Recht „Spitznamen verwalten“.")
    if member.id == guild.owner_id:
        raise RoleCheckError("Den Spitznamen der Serverbesitzerin oder des Serverbesitzers "
                             "kann kein Bot ändern.")
    await member.edit(nick=(nickname or None), reason=reason[:400])
    return {"user_id": str(user_id), "spitzname": nickname or None}
