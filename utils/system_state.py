"""Gemeinsamer, kleiner Zustand fuer serverweite Betriebsfunktionen."""

from __future__ import annotations

import discord

from utils.storage import JSONStore

SYSTEM_STATE_PATH = "data/system_state.json"
_store = JSONStore(SYSTEM_STATE_PATH, {"maintenance": False, "message": ""})

# Einheitliche Status-Texte. Werden von main.py (on_ready) UND von
# !system maintenance on/off verwendet, damit der Presence-Status nie
# an zwei Stellen unterschiedlich gesetzt wird und nach einem Neustart
# automatisch korrekt wiederhergestellt wird.
NORMAL_ACTIVITY_TEXT = "Avoke"
MAINTENANCE_ACTIVITY_TEXT = "⚠️ Wartungsmodus ⚠️"


async def get_maintenance_state() -> dict:
    """Liest den Wartungszustand in einem stabilen, vollstaendigen Format."""
    state = await _store.read()
    return {
        "maintenance": bool(state.get("maintenance", False)),
        "message": str(state.get("message", "")),
    }


async def set_maintenance_state(enabled: bool, message: str = "") -> dict:
    def mutate(data):
        data["maintenance"] = enabled
        data["message"] = message.strip()[:500]
        return data

    return await _store.update(mutate)


async def apply_presence(bot, *, normal_text: str = NORMAL_ACTIVITY_TEXT) -> dict:
    """
    Setzt die Discord-Presence passend zum gespeicherten Wartungszustand.

    Zentral genutzt von main.py (on_ready -> stellt Status nach Neustart
    wieder her) und von system_tools.py (!system maintenance on/off ->
    aendert den Status sofort). Dadurch kann der Wartungsstatus nie mehr
    versehentlich durch einen anderen Presence-Aufruf ueberschrieben werden.
    """
    state = await get_maintenance_state()
    if state["maintenance"]:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=MAINTENANCE_ACTIVITY_TEXT,
            ),
            status=discord.Status.idle,
        )
    else:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=normal_text,
            ),
            status=discord.Status.online,
        )
    return state
