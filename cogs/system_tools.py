"""
Kompakte Owner-Systemverwaltung — Wartung, Health-Checks, Backups und Steuerung.

Commands (!system <subcommand>):
  status              — aktueller Betriebszustand
  maintenance on/off  — Wartungsmodus ein-/ausschalten
  health              — Ticket-Konfiguration und Berechtigungen prüfen
  backup              — Datensicherung als ZIP
  stats               — Live-Statistiken (RAM, CPU, Uptime, Tickets, Bewertungen …)
  ping                — aktuelle Latenz
  reload <cog>        — einzelnen Cog neu laden
  reloadall           — alle Cogs neu laden
  sync                — Slash-Commands synchronisieren
  restart             — Bot sauber neu starten
  shutdown            — Bot sauber beenden

Slash-Sync (global/guild/clear) → /sync in owner.py
"""

from __future__ import annotations

import asyncio
import datetime
import os
import platform
import shutil
from pathlib import Path

import discord
from discord.ext import commands

from utils.storage import JSONStore
from utils.system_state import get_maintenance_state, set_maintenance_state, apply_presence
from utils.theme import success_embed, error_embed, info_embed, FOOTER_TEXT, COLOR_INFO, get_footer_text

try:
    import psutil
except ImportError:
    psutil = None

from cogs.tickets import OPEN_TICKETS_PATH
from cogs.ratings import compute_rating_stats

from utils.logger import get_logger
log = get_logger("system")

_open_tickets_store = JSONStore(OPEN_TICKETS_PATH, {})


def _format_uptime(delta: datetime.timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    days, rem     = divmod(total_seconds, 86400)
    hours, rem    = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:    parts.append(f"{days}d")
    if hours:   parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


class SystemTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self.ticket_config = JSONStore("data/tickets_config.json", {"panels": {}})
        self._process     = psutil.Process() if psutil else None

    async def cog_check(self, ctx: commands.Context) -> bool:
        return await self.bot.is_owner(ctx.author)

    def _log(self, ctx: commands.Context, action: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"[SYSTEM] {action} | User: {ctx.author} ({ctx.author.id}) | {now}")

    # ── !system ───────────────────────────────────────────────────────────────

    @commands.group(name="system", invoke_without_command=True)
    async def system(self, ctx: commands.Context):
        embed = discord.Embed(title="🛠️  Systemverwaltung", color=discord.Color.blurple())
        embed.description = (
            "`!system status`              — Betriebszustand\n"
            "`!system maintenance on/off`  — Wartungsmodus\n"
            "`!system health`              — Konfiguration & Berechtigungen\n"
            "`!system backup`              — Datensicherung erstellen\n"
            "`!system stats`               — Live-Statistiken\n"
            "`!system ping`                — Aktuelle Latenz\n"
            "`!system reload <cog>`        — Cog neu laden\n"
            "`!system reloadall`           — Alle Cogs neu laden\n"
            "`!system sync`                — Slash-Commands synchronisieren\n"
            "`!system restart`             — Bot neu starten\n"
            "`!system shutdown`            — Bot beenden\n\n"
            "Erweiterte Slash-Sync-Optionen: `/sync`\n"
            "Offene Tickets anzeigen: `!tickets`\n"
            "DM an alle senden: `!dm-all <text>`"
        )
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)

    # ── !system status ────────────────────────────────────────────────────────

    @system.command(name="status")
    async def status(self, ctx: commands.Context):
        state  = await get_maintenance_state()
        active = state["maintenance"]
        embed  = discord.Embed(
            title="📊 Systemstatus",
            color=discord.Color.orange() if active else discord.Color.green(),
        )
        embed.add_field(name="Wartungsmodus", value="🟠 Aktiv" if active else "🟢 Inaktiv")
        if active and state["message"]:
            embed.add_field(name="Hinweis", value=state["message"], inline=False)
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)

    # ── !system maintenance on/off ────────────────────────────────────────────

    @system.group(name="maintenance", invoke_without_command=True)
    async def maintenance(self, ctx: commands.Context):
        await self.status(ctx)

    @maintenance.command(name="on")
    async def maintenance_on(
        self, ctx: commands.Context, *,
        message: str = "Das Ticket-System befindet sich momentan in Wartung.",
    ):
        self._log(ctx, f"Maintenance ON ({message!r})")
        await set_maintenance_state(True, message)
        await apply_presence(self.bot)
        embed = discord.Embed(
            title="🟠 Wartungsmodus aktiviert",
            description="Neue Tickets werden vorübergehend nicht erstellt.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Hinweis", value=message, inline=False)
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)

    @maintenance.command(name="off")
    async def maintenance_off(self, ctx: commands.Context):
        self._log(ctx, "Maintenance OFF")
        await set_maintenance_state(False)
        await apply_presence(self.bot)
        embed = discord.Embed(
            title="🟢 Wartungsmodus beendet",
            description="Neue Tickets können wieder erstellt werden.",
            color=discord.Color.green(),
        )
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)

    # ── !system health ────────────────────────────────────────────────────────

    @system.command(name="health")
    async def health(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("Dieser Check muss auf einem Server ausgeführt werden.")
            return
        config = await self.ticket_config.read()
        panels = config.get("panels", {})
        issues = []
        for panel_id, panel in panels.items():
            label = panel.get("anzeige_name", panel_id)
            if not isinstance(ctx.guild.get_channel(panel.get("channel_id")), discord.TextChannel):
                issues.append(f"{label}: Panel-Kanal fehlt")
            if not isinstance(ctx.guild.get_channel(panel.get("kategorie_id")), discord.CategoryChannel):
                issues.append(f"{label}: Ticket-Kategorie fehlt")
            if not isinstance(ctx.guild.get_channel(panel.get("log_kanal_id")), discord.TextChannel):
                issues.append(f"{label}: Ticket-Log fehlt")
        me      = ctx.guild.me
        required = ("manage_channels", "send_messages", "read_message_history")
        missing  = [n for n in required if me and not getattr(me.guild_permissions, n)]
        embed = discord.Embed(
            title="🩺 System-Health-Check",
            color=discord.Color.green() if not issues and not missing else discord.Color.orange(),
        )
        embed.add_field(name="Ticketpanels",   value=str(len(panels)), inline=True)
        embed.add_field(name="Berechtigungen", value="OK" if not missing else ", ".join(missing), inline=True)
        embed.add_field(
            name="Ergebnis",
            value="Keine Probleme gefunden." if not issues else "\n".join(f"- {i}" for i in issues)[:1024],
            inline=False,
        )
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)

    # ── !system backup ────────────────────────────────────────────────────────

    @system.command(name="backup")
    async def backup(self, ctx: commands.Context):
        self._log(ctx, "Backup")
        async with ctx.typing():
            stamp        = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_dir   = Path("backups")
            backup_dir.mkdir(exist_ok=True)
            archive_base = backup_dir / f"avoke-data-{stamp}"
            archive      = await asyncio.to_thread(shutil.make_archive, str(archive_base), "zip", "data")
            file_path    = Path(archive)

        existing = sorted(backup_dir.glob("avoke-data-*.zip"))
        if file_path.stat().st_size > 8 * 1024 * 1024:
            embed = info_embed(
                "📁 Backup erstellt",
                f"Datei zu groß zum direkten Versand:\n`{file_path}`",
            )
            embed.add_field(name="Backups gesamt", value=str(len(existing)), inline=True)
            await ctx.send(embed=embed)
            return

        embed = success_embed("📁 Backup erstellt", f"`{file_path.name}`")
        embed.add_field(name="Backups gesamt", value=str(len(existing)), inline=True)
        await ctx.send(embed=embed, file=discord.File(file_path))

    # ── !system ping ──────────────────────────────────────────────────────────

    @system.command(name="ping")
    async def ping(self, ctx: commands.Context):
        latency_ms = round(self.bot.latency * 1000)
        await ctx.send(embed=success_embed("🏓 Pong!", f"Latenz: **{latency_ms} ms**"))

    # ── !system stats ─────────────────────────────────────────────────────────

    @system.command(name="stats")
    async def stats(self, ctx: commands.Context):
        self._log(ctx, "Stats")
        ping_ms     = round(self.bot.latency * 1000)
        guild_count = len(self.bot.guilds)
        user_count  = sum(g.member_count or 0 for g in self.bot.guilds)

        if self._process is not None:
            ram_mb      = self._process.memory_info().rss / (1024 * 1024)
            cpu_percent = self._process.cpu_percent(interval=0.1)
        else:
            ram_mb      = 0.0
            cpu_percent = 0.0

        state        = await get_maintenance_state()
        open_tickets = await _open_tickets_store.read()
        rating_stats = await compute_rating_stats()
        backup_dir   = Path("backups")
        backup_count = len(list(backup_dir.glob("avoke-data-*.zip"))) if backup_dir.exists() else 0
        cogs_loaded  = len(self.bot.extensions)

        launch_time = getattr(self.bot, "launch_time", None)
        uptime_str  = (
            _format_uptime(datetime.datetime.now(datetime.timezone.utc) - launch_time)
            if launch_time else "Unbekannt"
        )

        embed = discord.Embed(
            title="📊  Bot-Statistik",
            color=COLOR_INFO,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        embed.add_field(name="🟢 Health",         value="Online" if not state["maintenance"] else "🟠 Wartung", inline=True)
        embed.add_field(name="🌐 Latenz",          value=f"{ping_ms} ms",             inline=True)
        embed.add_field(name="⏱️ Uptime",          value=uptime_str,                  inline=True)
        embed.add_field(name="💾 RAM",             value=f"{ram_mb:.1f} MB",          inline=True)
        embed.add_field(name="⚙️ CPU",             value=f"{cpu_percent:.1f}%",       inline=True)
        embed.add_field(name="🔄 Cogs",            value=str(cogs_loaded),            inline=True)
        embed.add_field(name="🏰 Server",          value=str(guild_count),            inline=True)
        embed.add_field(name="👥 User",            value=str(user_count),             inline=True)
        embed.add_field(name="🎫 Offene Tickets",  value=str(len(open_tickets)),      inline=True)
        embed.add_field(
            name="⭐ Ø Bewertung",
            value=f"{rating_stats['average']}/5 ({rating_stats['count']} Bewertungen)",
            inline=True,
        )
        embed.add_field(name="📁 Backups",         value=str(backup_count),           inline=True)
        embed.add_field(name="📦 discord.py",      value=discord.__version__,         inline=True)
        embed.add_field(name="🐍 Python",          value=platform.python_version(),   inline=True)
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)

    # ── !system reload / reloadall / sync ─────────────────────────────────────

    @system.command(name="reload")
    async def reload(self, ctx: commands.Context, cog: str):
        self._log(ctx, f"Reload ({cog})")
        extension = cog if cog.startswith("cogs.") else f"cogs.{cog}"
        try:
            await self.bot.reload_extension(extension)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(extension)
            except Exception as e:
                await ctx.send(embed=error_embed("❌ Reload fehlgeschlagen", f"`{extension}`\n```{e}```"))
                return
        except commands.ExtensionNotFound:
            await ctx.send(embed=error_embed("❌ Cog nicht gefunden", f"`{extension}` existiert nicht."))
            return
        except Exception as e:
            await ctx.send(embed=error_embed("❌ Reload fehlgeschlagen", f"`{extension}`\n```{e}```"))
            return
        await ctx.send(embed=success_embed("✅ Cog neu geladen", f"`{extension}` wurde erfolgreich neu geladen."))

    @system.command(name="reloadall")
    async def reload_all(self, ctx: commands.Context):
        self._log(ctx, "ReloadAll")
        loaded = list(self.bot.extensions.keys())
        succeeded, failed = [], []
        for extension in loaded:
            try:
                await self.bot.reload_extension(extension)
                succeeded.append(extension)
            except Exception as e:
                failed.append((extension, str(e)))
                log.exception(f"Fehler beim Reload von {extension}: {e}")

        embed = success_embed("✅ Alle Cogs neu geladen", f"**{len(succeeded)}/{len(loaded)}** erfolgreich neu geladen.")
        if failed:
            embed.color = discord.Color.orange()
            embed.add_field(
                name="⚠️ Fehler",
                value="\n".join(f"`{ext}` — {err[:100]}" for ext, err in failed)[:1024],
                inline=False,
            )
        await ctx.send(embed=embed)

    @system.command(name="sync")
    async def sync(self, ctx: commands.Context):
        self._log(ctx, "Sync")
        try:
            synced = await self.bot.tree.sync()
        except discord.HTTPException as e:
            await ctx.send(embed=error_embed("❌ Sync fehlgeschlagen", str(e)))
            return
        await ctx.send(embed=success_embed(
            "✅ Slash-Commands synchronisiert",
            f"**{len(synced)}** Command(s) global gesynct.\n"
            "Für guild-spezifischen Sync oder Clear: `/sync`",
        ))

    # ── !system restart / shutdown ────────────────────────────────────────────

    @system.command(name="restart")
    async def restart(self, ctx: commands.Context):
        self._log(ctx, "Restart")
        log.info("Bot wird neu gestartet — Exit-Code 42 → start.cmd startet neu.")
        await ctx.send(embed=info_embed(
            "🔄 Neustart",
            "Der Bot wird neu gestartet.\n`start.cmd` startet ihn in 2 Sekunden automatisch wieder.",
        ))
        await self.bot.close()
        # Exit-Code 42 = Restart-Signal für start.cmd
        os._exit(42)

    @system.command(name="shutdown")
    async def shutdown(self, ctx: commands.Context):
        self._log(ctx, "Shutdown")
        log.info("Bot wird heruntergefahren — Exit-Code 0.")
        await ctx.send(embed=info_embed("🛑 Shutdown", "Der Bot wird jetzt sauber heruntergefahren …"))
        await self.bot.close()
        os._exit(0)


async def setup(bot: commands.Bot):
    await bot.add_cog(SystemTools(bot))
