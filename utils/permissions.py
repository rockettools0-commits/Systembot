"""
Rollen-Berechtigungs-System für AVOKE Bot.

Admins können über /setup (Schritt: Berechtigungen) festlegen,
welche Rollen welche Command-Gruppen nutzen dürfen.

Command-Gruppen:
  moderation  — ban, kick, tempban, unban, mute, unmute, timeout, untimeout,
                warn, warn-remove, warn-clear, warnings, lockdown,
                say, announce, lock, unlock, resetnick,
                automod-config, automod-toggle, automod-badword, automod-domain, automod-set,
                giveaway-start, giveaway-end, giveaway-reroll,
                admin-log-set, autorole-set,
                bot-status, stream-setup, stream-all, stream-add, stream-remove, stream-list
  utility     — clear, slowmode, nick, addxp, removexp, setxp, resetxp
  promotion   — promote, demote
  clan        — uprank, derank, clan-kick

check_role_permission(interaction, gruppe) → bool
  True  = erlaubt (Admin, oder Rolle in der Erlaubt-Liste, oder keine Liste konfiguriert)
  False = verweigert
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import discord

from utils.storage import JSONStore

PERM_CONFIG_PATH = "data/permissions_config.json"

_store: JSONStore | None = None


def get_store() -> JSONStore:
    global _store
    if _store is None:
        _store = JSONStore(PERM_CONFIG_PATH, {})
    return _store


async def check_role_permission(
    interaction: discord.Interaction,
    gruppe: str,
) -> bool:
    """
    Gibt True zurück wenn der User die Command-Gruppe nutzen darf.
    Admins dürfen immer alles.
    Wenn keine Rollen-Liste konfiguriert ist → alle dürfen.
    """
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False

    # Admins immer erlaubt
    if member.guild_permissions.administrator:
        return True

    store = get_store()
    data  = await store.read()
    guild_perms = data.get(str(interaction.guild_id), {})
    allowed_ids = guild_perms.get(gruppe)

    # Keine Liste konfiguriert → alle dürfen (offen)
    if not allowed_ids:
        return True

    member_role_ids = {r.id for r in member.roles}
    return bool(member_role_ids & set(allowed_ids))


async def save_group_roles(guild_id: int, gruppe: str, role_ids: list[int]) -> None:
    """Speichert die erlaubten Rollen für eine Command-Gruppe."""
    store = get_store()
    gid   = str(guild_id)

    def mutate(data: dict) -> dict:
        data.setdefault(gid, {})[gruppe] = role_ids
        return data

    await store.update(mutate)
