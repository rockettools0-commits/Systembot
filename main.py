"""
AVOKE Clan Bot — Elite System
Haupteinstiegspunkt: lädt Konfiguration, Intents und alle Cogs.
"""

import os
import asyncio
import datetime
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.errorlog import DequeErrorHandler
from utils.logger import setup_logging, get_logger
from utils.system_state import apply_presence

load_dotenv()

from utils.owners import OWNER_IDS, is_owner  # noqa: E402 — nach load_dotenv

TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("TOKEN fehlt in der .env Datei!")
if not OWNER_IDS:
    raise RuntimeError("Weder OWNER_ID noch OWNER_IDS in der .env Datei gesetzt!")

# Primärer Owner für discord.py bot.owner_id (nutzt ersten/kleinsten Wert)
_PRIMARY_OWNER_ID: int = min(OWNER_IDS)

SYNC_GUILD: discord.Object | None = None

# ── Logging einrichten ────────────────────────────────────────────────────────
# DequeErrorHandler speichert die letzten Fehler für das CMD-Live-Dashboard.
_deque_handler = DequeErrorHandler()
setup_logging(deque_handler=_deque_handler)

log         = get_logger("startup")   # logs/startup.log  + logs/bot.log
log_system  = get_logger("system")    # logs/system.log   + logs/bot.log
log_command = get_logger("command")   # logs/command.log  + logs/bot.log

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
intents.guilds = True
intents.voice_states = True


def _owner_ids_kwarg() -> dict:
    """
    Gibt entweder {'owner_id': x} oder {'owner_ids': {x, y, ...}} zurück —
    discord.py erlaubt nie beides gleichzeitig.
    """
    if len(OWNER_IDS) == 1:
        return {"owner_id": next(iter(OWNER_IDS))}
    return {"owner_ids": set(OWNER_IDS)}


class AVOKEBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            # owner_id und owner_ids dürfen laut discord.py nie gleichzeitig gesetzt sein.
            # Bei einem einzelnen Owner → owner_id, bei mehreren → owner_ids.
            **(_owner_ids_kwarg()),
            help_command=None,
        )
        # Startzeitpunkt für Uptime-Anzeige (!stats, CMD-Live-Dashboard)
        self.launch_time = datetime.datetime.now(datetime.timezone.utc)

    async def setup_hook(self):
        cogs = [
            "cogs.tickets",
            "cogs.trading",
            "cogs.coords",
            "cogs.roles",
            "cogs.moderation",
            "cogs.logging_cog",
            "cogs.giveaways",
            "cogs.rank",
            "cogs.owner",
            "cogs.economy",
            "cogs.verification",
            "cogs.welcome",
            "cogs.servertools",
            "cogs.help_menu",
            "cogs.botstatus",
            "cogs.clanlog",
            "cogs.setup",
            "cogs.utility",
            "cogs.promotion",
            "cogs.fun",
            "cogs.ratings",       # Ticketbewertungs-System
            "cogs.ticket_gui",    # Ticket-Setup als moderne Button/Select-GUI
            "cogs.owner_admin",   # Owner-Only Verwaltungscommands (!restart, !stats, ...)
            "cogs.dashboard",     # Live CMD-Dashboard
            "cogs.changelog",     # !changelog: Ueberblick ueber Neuerungen
            "cogs.system_tools",  # !system: Wartung, Health-Check und Backups
            "cogs.owner_panel",   # /owner-*: vollständiges Owner Slash-Panel
            "cogs.mc_verify",     # /mc verify/status/unlink/whois/list
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                log.info(f"Cog geladen: {cog}")
            except Exception as e:
                log.exception(f"Fehler beim Laden von {cog}: {e}")

        try:
            if SYNC_GUILD:
                # Guild-Sync: sofort verfügbar, kein 100er-Limit
                self.tree.copy_global_to(guild=SYNC_GUILD)
                synced = await self.tree.sync(guild=SYNC_GUILD)
                log.info(f"{len(synced)} Slash-Commands guild-spezifisch synchronisiert (Guild {SYNC_GUILD.id}).")
            else:
                # Global-Sync: bis zu 1h Verzögerung, max 100 Commands
                synced = await self.tree.sync()
                log.info(f"{len(synced)} Slash-Commands global synchronisiert.")
        except Exception as e:
            log.exception(f"Fehler beim Synchronisieren der Slash-Commands: {e}")

    async def on_ready(self):
        # ── Startup-Banner ────────────────────────────────────────────────────
        guild_count = len(self.guilds)
        user_count  = sum(g.member_count or 0 for g in self.guilds)
        sync_mode   = f"guild:{SYNC_GUILD.id}" if SYNC_GUILD else "global"
        log.info("━" * 60)
        log.info("  AVOKE System — Bot bereit")
        log.info(f"  User    : {self.user}  (ID: {self.user.id})")
        log.info(f"  Owner   : {', '.join(str(i) for i in sorted(OWNER_IDS))}")
        log.info(f"  Server  : {guild_count}  |  User: {user_count}")
        log.info(f"  Sync    : {sync_mode}")
        log.info(f"  Cogs    : {len(self.cogs)} geladen")
        log.info("━" * 60)

        # Stellt den zuletzt gesetzten Status wieder her (Wartungsmodus bleibt
        # nach einem Neustart erhalten und wird hier NICHT überschrieben).
        state = await apply_presence(self)
        if state["maintenance"]:
            log.info("Wartungsmodus war aktiv — Status wiederhergestellt.")


bot = AVOKEBot()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    # Owner-Only Commands (z.B. !restart, !stats, ...) → freundliche Fehlermeldung
    if isinstance(error, (commands.NotOwner, commands.CheckFailure)):
        try:
            await ctx.send("❌ Keine Berechtigung.")
        except discord.HTTPException:
            pass
        return

    if isinstance(error, commands.MissingRequiredArgument):
        try:
            await ctx.send(f"❌ Es fehlt ein Argument: `{error.param.name}`.")
        except discord.HTTPException:
            pass
        return

    if isinstance(error, commands.BadArgument):
        try:
            await ctx.send(f"❌ Ungültiges Argument: {error}")
        except discord.HTTPException:
            pass
        return

    log_command.exception(f"Prefix-Command-Fehler in !{ctx.command}: {error}")


async def main():
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
