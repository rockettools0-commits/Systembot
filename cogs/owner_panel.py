"""
Owner-Panel — /owner Subcommand-Gruppe (zählt als 1 Command).

Alle Subcommands sind Owner-Only (interaction.user.id == OWNER_ID).

  /owner stats        — Live-Statistiken
  /owner health       — Ticket-Konfig & Berechtigungen
  /owner ping         — Latenz
  /owner uptime       — Uptime mit Timestamp
  /owner reload       — Einzelnen Cog neu laden
  /owner reloadall    — Alle Cogs neu laden
  /owner coglist      — Geladene Cogs auflisten
  /owner sync         — Slash-Commands synchronisieren
  /owner logs         — Letzte 20 Zeilen einer Log-Datei
  /owner errors       — Fehler-Ringpuffer anzeigen
  /owner memory       — RAM/CPU-Bericht
  /owner diagnostics  — Vollständiger Systemdiagnose-Report
  /owner backup       — data/ als ZIP sichern
  /owner backuplist   — Vorhandene Backups auflisten
  /owner clearbackups — Alte Backups aufräumen
  /owner maintenance  — Wartungsmodus on/off
  /owner announce     — Embed-Ankündigung in Kanal senden
  /owner botmessage   — Textnachricht als Bot senden
  /owner dm           — DM an einen User
  /owner guilds       — Alle Server auflisten
  /owner cache        — Cache-Statistiken
  /owner clearerrors  — Fehler-Ringpuffer leeren
  /owner restart      — Neustart (Exit 42 → start.cmd)
  /owner shutdown     — Sauberes Herunterfahren
"""

from __future__ import annotations

import asyncio
import datetime
import math
import os
import platform
import shutil
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from utils.errorlog import recent_errors
from utils.logger import get_logger
from utils.owners import is_owner_async, add_trusted, remove_trusted, get_trusted_ids, OWNER_IDS
from utils.storage import JSONStore
from utils.system_state import get_maintenance_state, set_maintenance_state, apply_presence
from utils.theme import (
    success_embed, error_embed, info_embed, warning_embed,
    dark_embed, FOOTER_TEXT, COLOR_INFO, COLOR_DARK, get_footer_text,
)

try:
    import psutil
except ImportError:
    psutil = None

from cogs.tickets import OPEN_TICKETS_PATH
from cogs.ratings import compute_rating_stats

log = get_logger("system")

_open_tickets_store = JSONStore(OPEN_TICKETS_PATH, {})

_LOG_FILES = {
    "bot":        "logs/bot.log",
    "error":      "logs/error.log",
    "command":    "logs/command.log",
    "moderation": "logs/moderation.log",
    "system":     "logs/system.log",
    "startup":    "logs/startup.log",
}
_LOG_CHOICES = [
    app_commands.Choice(name=f"{k}  →  logs/{k}.log", value=k)
    for k in _LOG_FILES
]


def _fmt_uptime(delta: datetime.timedelta) -> str:
    total = int(delta.total_seconds())
    d, r = divmod(total, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


async def _owner_only(interaction: discord.Interaction) -> bool:
    """Prüft .env-IDs UND Trusted-JSON."""
    return await is_owner_async(interaction.user.id)


async def _deny(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        embed=error_embed("❌ Kein Zugriff", "Dieser Befehl ist nur für den Bot-Owner."),
        ephemeral=True,
    )


# ── Subcommand-Gruppe ─────────────────────────────────────────────────────────

class OwnerGroup(app_commands.Group, name="owner", description="[Owner] Bot-Verwaltung & Diagnose."):
    """Alle /owner Subcommands — zählt als 1 Slash-Command."""

    def __init__(self, cog: "OwnerPanel"):
        super().__init__()
        self._cog = cog

    def _log(self, interaction: discord.Interaction, action: str) -> None:
        log.info(f"[OWNER] {action} | {interaction.user} ({interaction.user.id})")

    # ── /owner stats ──────────────────────────────────────────────────────────

    @app_commands.command(name="stats", description="Live-Statistiken des Bots.")
    async def stats(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "stats")
        await interaction.response.defer(ephemeral=True)
        cog = self._cog
        ping_ms     = round(interaction.client.latency * 1000) if math.isfinite(interaction.client.latency) else 0
        guild_count = len(cog.bot.guilds)
        user_count  = sum(g.member_count or 0 for g in cog.bot.guilds)
        chan_count   = sum(len(g.channels) for g in cog.bot.guilds)
        cog_count   = len(cog.bot.extensions)
        cmd_count   = len(cog.bot.tree.get_commands())
        ram_mb = cpu_pct = 0.0
        if cog._process:
            ram_mb  = cog._process.memory_info().rss / (1024 * 1024)
            cpu_pct = cog._process.cpu_percent(interval=0.1)
        state        = await get_maintenance_state()
        open_tickets = await _open_tickets_store.read()
        rating_stats = await compute_rating_stats()
        backup_dir   = Path("backups")
        backup_count = len(list(backup_dir.glob("avoke-data-*.zip"))) if backup_dir.exists() else 0
        launch_time  = getattr(cog.bot, "launch_time", None)
        uptime_str   = _fmt_uptime(datetime.datetime.now(datetime.timezone.utc) - launch_time) if launch_time else "—"
        embed = discord.Embed(title="📊  Owner Stats", color=COLOR_INFO,
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        if cog.bot.user:
            embed.set_thumbnail(url=cog.bot.user.display_avatar.url)
        embed.add_field(name="🟢 Status",        value="Online" if not state["maintenance"] else "🟠 Wartung", inline=True)
        embed.add_field(name="🌐 Ping",           value=f"{ping_ms} ms",           inline=True)
        embed.add_field(name="⏱️ Uptime",         value=uptime_str,                inline=True)
        embed.add_field(name="💾 RAM",            value=f"{ram_mb:.1f} MB",        inline=True)
        embed.add_field(name="⚙️ CPU",            value=f"{cpu_pct:.1f} %",        inline=True)
        embed.add_field(name="🔄 Cogs",           value=str(cog_count),            inline=True)
        embed.add_field(name="🏰 Server",         value=str(guild_count),          inline=True)
        embed.add_field(name="👥 User",           value=str(user_count),           inline=True)
        embed.add_field(name="📡 Kanäle",         value=str(chan_count),           inline=True)
        embed.add_field(name="⚡ Slash-Commands", value=str(cmd_count),            inline=True)
        embed.add_field(name="🎫 Offene Tickets", value=str(len(open_tickets)),    inline=True)
        embed.add_field(name="⭐ Ø Bewertung",    value=f"{rating_stats['average']}/5 ({rating_stats['count']}x)", inline=True)
        embed.add_field(name="📁 Backups",        value=str(backup_count),         inline=True)
        embed.add_field(name="📦 discord.py",     value=discord.__version__,       inline=True)
        embed.add_field(name="🐍 Python",         value=platform.python_version(), inline=True)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /owner health ─────────────────────────────────────────────────────────

    @app_commands.command(name="health", description="Ticket-Konfiguration & Bot-Berechtigungen prüfen.")
    async def health(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        if interaction.guild is None:
            return await interaction.response.send_message(embed=error_embed("❌ Nur auf Servern."), ephemeral=True)
        self._log(interaction, "health")
        await interaction.response.defer(ephemeral=True)
        cog    = self._cog
        config = await cog.ticket_config.read()
        panels = config.get("panels", {})
        issues = []
        for pid, panel in panels.items():
            label = panel.get("anzeige_name", pid)
            if not isinstance(interaction.guild.get_channel(panel.get("channel_id")), discord.TextChannel):
                issues.append(f"`{label}`: Panel-Kanal fehlt")
            if not isinstance(interaction.guild.get_channel(panel.get("kategorie_id")), discord.CategoryChannel):
                issues.append(f"`{label}`: Ticket-Kategorie fehlt")
            if not isinstance(interaction.guild.get_channel(panel.get("log_kanal_id")), discord.TextChannel):
                issues.append(f"`{label}`: Log-Kanal fehlt")
        me      = interaction.guild.me
        missing = [p for p in ("manage_channels","send_messages","read_message_history","embed_links")
                   if me and not getattr(me.guild_permissions, p)]
        color = discord.Color.green() if not issues and not missing else discord.Color.orange()
        embed = discord.Embed(title="🩺  Health-Check", color=color,
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="🎫 Panels",         value=str(len(panels)),                                         inline=True)
        embed.add_field(name="🔐 Berechtigungen", value="✅ OK" if not missing else f"❌ {', '.join(missing)}", inline=True)
        embed.add_field(name="\u200b",            value="\u200b",                                                 inline=True)
        embed.add_field(name="⚠️ Probleme" if issues else "✅ Ergebnis",
                        value="\n".join(f"- {i}" for i in issues)[:1024] if issues else "Keine Probleme.",
                        inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /owner ping ───────────────────────────────────────────────────────────

    @app_commands.command(name="ping", description="Aktuelle Bot-Latenz.")
    async def ping(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        ms = round(self._cog.bot.latency * 1000) if math.isfinite(self._cog.bot.latency) else 0
        await interaction.response.send_message(
            embed=success_embed("🏓 Pong!", f"WebSocket-Latenz: **{ms} ms**"), ephemeral=True)

    # ── /owner uptime ─────────────────────────────────────────────────────────

    @app_commands.command(name="uptime", description="Uptime seit dem letzten Start.")
    async def uptime(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        launch = getattr(self._cog.bot, "launch_time", None)
        if launch:
            delta = datetime.datetime.now(datetime.timezone.utc) - launch
            desc  = f"**{_fmt_uptime(delta)}**\n\nGestartet: <t:{int(launch.timestamp())}:F>  (<t:{int(launch.timestamp())}:R>)"
        else:
            desc = "Startzeit unbekannt."
        await interaction.response.send_message(embed=info_embed("⏱️ Uptime", desc), ephemeral=True)

    # ── /owner reload ─────────────────────────────────────────────────────────

    @app_commands.command(name="reload", description="Einen Cog neu laden.")
    @app_commands.describe(cog="Cog-Name, z.B. giveaways oder cogs.giveaways")
    async def reload(self, interaction: discord.Interaction, cog: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"reload:{cog}")
        await interaction.response.defer(ephemeral=True)
        ext = cog if cog.startswith("cogs.") else f"cogs.{cog}"
        try:
            await self._cog.bot.reload_extension(ext)
        except commands.ExtensionNotLoaded:
            try:
                await self._cog.bot.load_extension(ext)
            except Exception as e:
                return await interaction.followup.send(
                    embed=error_embed("❌ Laden fehlgeschlagen", f"`{ext}`\n```{e}```"), ephemeral=True)
        except Exception as e:
            return await interaction.followup.send(
                embed=error_embed("❌ Reload fehlgeschlagen", f"`{ext}`\n```{e}```"), ephemeral=True)
        await interaction.followup.send(
            embed=success_embed("✅ Cog neu geladen", f"`{ext}` erfolgreich neu geladen."), ephemeral=True)

    # ── /owner reloadall ──────────────────────────────────────────────────────

    @app_commands.command(name="reloadall", description="Alle Cogs neu laden.")
    async def reloadall(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "reloadall")
        await interaction.response.defer(ephemeral=True)
        loaded = list(self._cog.bot.extensions.keys())
        ok, fail = [], []
        for ext in loaded:
            try:
                await self._cog.bot.reload_extension(ext)
                ok.append(ext)
            except Exception as e:
                fail.append((ext, str(e)))
                log.exception(f"Reload-Fehler: {ext}: {e}")
        embed = success_embed("✅ Alle Cogs neu geladen", f"**{len(ok)}/{len(loaded)}** erfolgreich.")
        if fail:
            embed.color = discord.Color.orange()
            embed.add_field(name="⚠️ Fehler",
                            value="\n".join(f"`{e}` — {m[:80]}" for e, m in fail)[:1024], inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /owner coglist ────────────────────────────────────────────────────────

    @app_commands.command(name="coglist", description="Alle geladenen Cogs auflisten.")
    async def coglist(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        cogs  = sorted(self._cog.bot.extensions.keys())
        lines = [f"`{i+1:02d}.` {c}" for i, c in enumerate(cogs)]
        embed = info_embed(f"🔄  Geladene Cogs  ({len(cogs)})", "\n".join(lines) or "*Keine.*")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /owner sync ───────────────────────────────────────────────────────────

    @app_commands.command(name="sync", description="Slash-Commands synchronisieren.")
    @app_commands.describe(scope="guild = sofort · global = überall · clear = aufräumen")
    @app_commands.choices(scope=[
        app_commands.Choice(name="⚡ Guild   — nur dieser Server (sofort)", value="guild"),
        app_commands.Choice(name="🌍 Global  — überall (bis 1h Delay)",    value="global"),
        app_commands.Choice(name="🧹 Clear   — aufräumen + neu sync",       value="clear"),
    ])
    async def sync(self, interaction: discord.Interaction, scope: str = "guild"):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"sync:{scope}")
        await interaction.response.defer(ephemeral=True)
        bot = self._cog.bot
        try:
            if scope == "guild":
                bot.tree.copy_global_to(guild=interaction.guild)
                synced = await bot.tree.sync(guild=interaction.guild)
                label  = f"Server **{interaction.guild.name}**"
            elif scope == "clear":
                bot.tree.clear_commands(guild=interaction.guild)
                await bot.tree.sync(guild=interaction.guild)
                bot.tree.copy_global_to(guild=interaction.guild)
                synced = await bot.tree.sync(guild=interaction.guild)
                label  = f"Server **{interaction.guild.name}** (bereinigt)"
            else:
                synced = await bot.tree.sync()
                label  = "**Global**"
        except discord.HTTPException as e:
            return await interaction.followup.send(embed=error_embed("❌ Sync fehlgeschlagen", str(e)), ephemeral=True)
        await interaction.followup.send(
            embed=success_embed("✅ Sync abgeschlossen", f"**{len(synced)}** Commands für {label} gesynct."),
            ephemeral=True)

    # ── /owner logs ───────────────────────────────────────────────────────────

    @app_commands.command(name="logs", description="Letzte 20 Zeilen einer Log-Datei anzeigen.")
    @app_commands.describe(category="Welche Log-Datei?")
    @app_commands.choices(category=_LOG_CHOICES)
    async def logs(self, interaction: discord.Interaction, category: str = "bot"):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"logs:{category}")
        await interaction.response.defer(ephemeral=True)
        path = Path(_LOG_FILES.get(category, "logs/bot.log"))
        if not path.exists():
            return await interaction.followup.send(
                embed=warning_embed("⚠️ Keine Log-Datei", f"`{path}` existiert noch nicht."), ephemeral=True)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tail = "".join(lines[-20:]).strip() or "(Leer)"
        except OSError as e:
            return await interaction.followup.send(embed=error_embed("❌ Lesefehler", str(e)), ephemeral=True)
        if len(tail) > 1900: tail = "…" + tail[-1900:]
        embed = dark_embed(f"📄  logs/{category}.log  —  letzte 20 Zeilen", f"```\n{tail}\n```")
        embed.set_footer(text=f"{get_footer_text(interaction)}  ·  {path}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /owner errors ─────────────────────────────────────────────────────────

    @app_commands.command(name="errors", description="Letzte Fehler aus dem Ringpuffer.")
    async def errors(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        errs = list(recent_errors)
        if not errs:
            return await interaction.response.send_message(
                embed=success_embed("✅ Keine Fehler", "Der Fehler-Ringpuffer ist leer."), ephemeral=True)
        lines = "\n".join(f"`{i+1}.` {e[-120:]}" for i, e in enumerate(errs))
        await interaction.response.send_message(embed=warning_embed(f"⚠️  Letzte {len(errs)} Fehler", lines), ephemeral=True)

    # ── /owner memory ─────────────────────────────────────────────────────────

    @app_commands.command(name="memory", description="Detaillierter RAM/CPU-Bericht.")
    async def memory(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        await interaction.response.defer(ephemeral=True)
        if self._cog._process is None:
            return await interaction.followup.send(
                embed=warning_embed("⚠️ psutil fehlt", "Installiere psutil für RAM-Statistiken."), ephemeral=True)
        mi  = self._cog._process.memory_info()
        rss = mi.rss / (1024 * 1024)
        vms = mi.vms / (1024 * 1024)
        cpu = self._cog._process.cpu_percent(interval=0.1)
        thr = self._cog._process.num_threads()
        vm  = psutil.virtual_memory()
        embed = info_embed("💾  Memory-Report",
            f"**Bot-Prozess**\nRSS: **{rss:.1f} MB**  |  VMS: **{vms:.1f} MB**\n"
            f"CPU: **{cpu:.1f} %**  |  Threads: **{thr}**\n\n"
            f"**System-RAM**\nGesamt: **{vm.total//(1024*1024)} MB**  |  "
            f"Belegt: **{vm.used//(1024*1024)} MB** (**{vm.percent:.1f} %**)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /owner diagnostics ────────────────────────────────────────────────────

    @app_commands.command(name="diagnostics", description="Vollständiger Systemdiagnose-Bericht.")
    async def diagnostics(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "diagnostics")
        await interaction.response.defer(ephemeral=True)
        cog  = self._cog
        now  = datetime.datetime.now(datetime.timezone.utc)
        la   = getattr(cog.bot, "launch_time", None)
        up   = _fmt_uptime(now - la) if la else "—"
        ping = round(cog.bot.latency * 1000) if math.isfinite(cog.bot.latency) else 0
        state        = await get_maintenance_state()
        open_tickets = await _open_tickets_store.read()
        bd           = Path("backups")
        bc           = len(list(bd.glob("avoke-data-*.zip"))) if bd.exists() else 0
        ram = cpu = 0.0
        if cog._process:
            ram = cog._process.memory_info().rss / (1024 * 1024)
            cpu = cog._process.cpu_percent(interval=0.1)
        log_st = {k: ("✅" if Path(v).exists() else "❌") for k, v in _LOG_FILES.items()}
        embed = discord.Embed(title="🔬  System-Diagnose", color=COLOR_DARK, timestamp=now)
        embed.add_field(name="🟢 Status",        value="Online" if not state["maintenance"] else "🟠 Wartung", inline=True)
        embed.add_field(name="⏱️ Uptime",         value=up,                              inline=True)
        embed.add_field(name="🌐 Ping",           value=f"{ping} ms",                    inline=True)
        embed.add_field(name="💾 RAM",            value=f"{ram:.1f} MB",                 inline=True)
        embed.add_field(name="⚙️ CPU",            value=f"{cpu:.1f} %",                  inline=True)
        embed.add_field(name="🐍 Python",         value=platform.python_version(),       inline=True)
        embed.add_field(name="📦 discord.py",     value=discord.__version__,             inline=True)
        embed.add_field(name="🖥️ OS",             value=f"{platform.system()} {platform.release()}", inline=True)
        embed.add_field(name="🔄 Cogs",           value=str(len(cog.bot.extensions)),    inline=True)
        embed.add_field(name="🎫 Offene Tickets", value=str(len(open_tickets)),          inline=True)
        embed.add_field(name="📁 Backups",        value=str(bc),                         inline=True)
        embed.add_field(name="⚡ Commands",       value=str(len(cog.bot.tree.get_commands())), inline=True)
        embed.add_field(name="📄 Logs",           value="  ".join(f"{k}:{v}" for k, v in log_st.items()), inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /owner backup ─────────────────────────────────────────────────────────

    @app_commands.command(name="backup", description="data/ als ZIP sichern.")
    async def backup(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "backup")
        await interaction.response.defer(ephemeral=True)
        stamp      = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bd         = Path("backups"); bd.mkdir(exist_ok=True)
        archive    = await asyncio.to_thread(shutil.make_archive, str(bd / f"avoke-data-{stamp}"), "zip", "data")
        fp         = Path(archive)
        total      = len(list(bd.glob("avoke-data-*.zip")))
        embed      = success_embed("📁  Backup erstellt", f"`{fp.name}`")
        embed.add_field(name="Größe",          value=f"{fp.stat().st_size/1024:.1f} KB", inline=True)
        embed.add_field(name="Backups gesamt", value=str(total),                         inline=True)
        if fp.stat().st_size <= 8 * 1024 * 1024:
            await interaction.followup.send(embed=embed, file=discord.File(fp), ephemeral=True)
        else:
            embed.description = (embed.description or "") + "\n⚠️ Zu groß zum Hochladen (> 8 MB)."
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /owner backuplist ─────────────────────────────────────────────────────

    @app_commands.command(name="backuplist", description="Alle vorhandenen Backups auflisten.")
    async def backuplist(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        bd    = Path("backups")
        files = sorted(bd.glob("avoke-data-*.zip"), reverse=True) if bd.exists() else []
        if not files:
            return await interaction.response.send_message(
                embed=info_embed("📁  Backups", "Noch keine Backups vorhanden."), ephemeral=True)
        lines = [f"`{f.name}`  —  {f.stat().st_size/1024:.1f} KB" for f in files[:20]]
        await interaction.response.send_message(
            embed=info_embed(f"📁  Backups  ({len(files)} gesamt)", "\n".join(lines)), ephemeral=True)

    # ── /owner clearbackups ───────────────────────────────────────────────────

    @app_commands.command(name="clearbackups", description="Alte Backups löschen (behält die letzten 5).")
    async def clearbackups(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "clearbackups")
        bd    = Path("backups")
        files = sorted(bd.glob("avoke-data-*.zip")) if bd.exists() else []
        gone  = files[:-5]
        for f in gone: f.unlink(missing_ok=True)
        await interaction.response.send_message(
            embed=success_embed("🧹  Backups bereinigt",
                f"**{len(gone)}** gelöscht · **{min(len(files),5)}** behalten."), ephemeral=True)

    # ── /owner maintenance ────────────────────────────────────────────────────

    @app_commands.command(name="maintenance", description="Wartungsmodus ein- oder ausschalten.")
    @app_commands.describe(action="on = aktivieren · off = deaktivieren",
                           message="Optionale Nachricht (nur bei on)")
    @app_commands.choices(action=[
        app_commands.Choice(name="🟠 Aktivieren",   value="on"),
        app_commands.Choice(name="🟢 Deaktivieren", value="off"),
    ])
    async def maintenance(self, interaction: discord.Interaction, action: str,
                          message: str = "Das System befindet sich in Wartung."):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"maintenance:{action}")
        if action == "on":
            await set_maintenance_state(True, message)
            await apply_presence(self._cog.bot)
            embed = warning_embed("🟠  Wartungsmodus aktiviert", message)
        else:
            await set_maintenance_state(False)
            await apply_presence(self._cog.bot)
            embed = success_embed("🟢  Wartungsmodus deaktiviert", "Alle Dienste sind wieder verfügbar.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /owner announce ───────────────────────────────────────────────────────

    @app_commands.command(name="announce", description="Server-weite Ankündigung als Embed senden.")
    @app_commands.describe(channel="Ziel-Kanal", text="Text der Ankündigung", title="Optionaler Titel")
    async def announce(self, interaction: discord.Interaction, channel: discord.TextChannel,
                       text: str, title: str = "📢  Ankündigung"):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"announce → #{channel.name}")
        embed = discord.Embed(title=title, description=text, color=discord.Color.blurple(),
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_footer(text=get_footer_text(interaction))
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("❌ Kein Zugriff", f"Keine Berechtigung in {channel.mention}."), ephemeral=True)
        await interaction.response.send_message(
            embed=success_embed("✅  Ankündigung gesendet", f"Kanal: {channel.mention}"), ephemeral=True)

    # ── /owner botmessage ─────────────────────────────────────────────────────

    @app_commands.command(name="botmessage", description="Einfache Textnachricht als Bot senden.")
    @app_commands.describe(channel="Ziel-Kanal", text="Nachrichtentext")
    async def botmessage(self, interaction: discord.Interaction, channel: discord.TextChannel, text: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"botmessage → #{channel.name}")
        try:
            await channel.send(content=text)
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("❌ Kein Zugriff", f"Keine Berechtigung in {channel.mention}."), ephemeral=True)
        await interaction.response.send_message(
            embed=success_embed("✅  Nachricht gesendet", f"Kanal: {channel.mention}"), ephemeral=True)

    # ── /owner dm ─────────────────────────────────────────────────────────────

    @app_commands.command(name="dm", description="Eine DM an einen User senden.")
    @app_commands.describe(user="Ziel-User", text="Nachrichtentext")
    async def dm(self, interaction: discord.Interaction, user: discord.Member, text: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"dm → {user} ({user.id})")
        try:
            await user.send(content=text)
        except (discord.Forbidden, discord.HTTPException) as e:
            return await interaction.response.send_message(
                embed=error_embed("❌ DM fehlgeschlagen", f"{user.mention} hat DMs deaktiviert.\n`{e}`"), ephemeral=True)
        await interaction.response.send_message(
            embed=success_embed("✅  DM gesendet", f"An: {user.mention}"), ephemeral=True)

    # ── /owner guilds ─────────────────────────────────────────────────────────

    @app_commands.command(name="guilds", description="Alle Server auflisten, auf denen der Bot ist.")
    async def guilds(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        gs    = sorted(self._cog.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        lines = [f"`{i+1:02d}.` **{g.name}**  —  {g.member_count} Mitglieder  (`{g.id}`)" for i, g in enumerate(gs[:20])]
        await interaction.response.send_message(
            embed=info_embed(f"🏰  Server  ({len(gs)} gesamt)", "\n".join(lines) or "*Keine.*"), ephemeral=True)

    # ── /owner cache ──────────────────────────────────────────────────────────

    @app_commands.command(name="cache", description="Cache-Statistiken anzeigen.")
    async def cache(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        bot  = self._cog.bot
        embed = info_embed("🗄️  Cache-Statistiken",
            f"**JSONStore-Locks:** {len(JSONStore._locks)} aktive Dateien\n"
            f"**Mitglieder im Cache:** {sum(len(g.members) for g in bot.guilds)}\n"
            f"**Rollen im Cache:**     {sum(len(g.roles) for g in bot.guilds)}\n"
            f"**Kanäle im Cache:**     {sum(len(g.channels) for g in bot.guilds)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /owner clearerrors ────────────────────────────────────────────────────

    @app_commands.command(name="clearerrors", description="Fehler-Ringpuffer leeren.")
    async def clearerrors(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        count = len(recent_errors)
        recent_errors.clear()
        self._log(interaction, "clearerrors")
        await interaction.response.send_message(
            embed=success_embed("✅  Ringpuffer geleert", f"**{count}** Fehler entfernt."), ephemeral=True)

    # ── /owner restart ────────────────────────────────────────────────────────

    @app_commands.command(name="restart", description="Bot neu starten (start.cmd fängt Exit-Code 42).")
    async def restart(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "RESTART")
        log.info("Bot wird neu gestartet — Exit-Code 42.")
        await interaction.response.send_message(
            embed=info_embed("🔄  Neustart",
                "Der Bot wird neu gestartet.\n`start.cmd` startet ihn in ~2 Sekunden wieder."), ephemeral=True)
        await self._cog.bot.close()
        os._exit(42)

    # ── /owner shutdown ───────────────────────────────────────────────────────

    @app_commands.command(name="shutdown", description="Bot sauber herunterfahren.")
    async def shutdown(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "SHUTDOWN")
        log.info("Bot wird heruntergefahren — Exit-Code 0.")
        await interaction.response.send_message(
            embed=info_embed("🛑  Shutdown", "Der Bot wird jetzt sauber heruntergefahren …"), ephemeral=True)
        await self._cog.bot.close()
        os._exit(0)

    # ── /owner owners ─────────────────────────────────────────────────────────

    @app_commands.command(name="owners", description="Zeigt alle Owner und berechtigten User.")
    async def owners(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        trusted = await get_trusted_ids()
        lines   = []
        for uid in sorted(OWNER_IDS):
            m    = interaction.guild.get_member(uid) if interaction.guild else None
            name = m.mention if m else f"<@{uid}>"
            lines.append(f"🔒 {name}  (`{uid}`)  — *statisch*")
        for uid in sorted(trusted - OWNER_IDS):
            m    = interaction.guild.get_member(uid) if interaction.guild else None
            name = m.mention if m else f"<@{uid}>"
            lines.append(f"✅ {name}  (`{uid}`)  — *per Command*")
        total = len(OWNER_IDS) + len(trusted - OWNER_IDS)
        embed = info_embed(
            f"👑  Owner & Trusted  ({total})",
            "\n".join(lines) or "*Keine Owner konfiguriert.*",
        )
        embed.set_footer(text=f"🔒 .env  ·  ✅ /owneradmin trustuser  ·  Verwalten: /owneradmin trustedlist  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ── /owneradmin Gruppe ────────────────────────────────────────────────────────
# Zweite Gruppe für erweiterte Admin-Aktionen (max 25 Subcommands pro Gruppe).
# Nutzt dieselbe Owner-Prüfung wie /owner.

class OwnerAdminGroup(app_commands.Group, name="owneradmin", description="[Owner] Erweiterte Bot-Administration."):
    """Erweiterte Owner-Werkzeuge — zählt als 1 Slash-Command."""

    def __init__(self, cog: "OwnerPanel"):
        super().__init__()
        self._cog = cog

    def _log(self, interaction: discord.Interaction, action: str) -> None:
        log.info(f"[OWNERADMIN] {action} | {interaction.user} ({interaction.user.id})")

    # ── /owneradmin eval ──────────────────────────────────────────────────────

    @app_commands.command(name="eval", description="[Owner] Python-Ausdruck direkt auswerten (gefährlich!).")
    @app_commands.describe(code="Python-Ausdruck — kein await, kein mehrzeiliges Statement")
    async def eval_cmd(self, interaction: discord.Interaction, code: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"eval: {code!r}")
        import traceback
        try:
            result = eval(code, {"bot": self._cog.bot, "discord": discord})  # noqa: S307
            text   = str(result)[:1900]
        except Exception:
            text = traceback.format_exc()[-1900:]
        embed = dark_embed("🐍  Eval", f"```py\n{text}\n```")
        embed.add_field(name="Input", value=f"```py\n{code[:200]}\n```", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /owneradmin purgelogs ─────────────────────────────────────────────────

    @app_commands.command(name="purgelogs", description="[Owner] Alle Log-Dateien leeren (nicht löschen).")
    async def purgelogs(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "purgelogs")
        cleared = []
        for name, path in _LOG_FILES.items():
            p = Path(path)
            if p.exists():
                p.write_text("", encoding="utf-8")
                cleared.append(name)
        embed = success_embed(
            "🗑️  Logs geleert",
            f"**{len(cleared)}** Log-Datei(en) geleert:\n" + ", ".join(f"`{n}`" for n in cleared),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /owneradmin serverleave ───────────────────────────────────────────────

    @app_commands.command(name="serverleave", description="[Owner] Bot verlässt einen bestimmten Server.")
    @app_commands.describe(guild_id="Die Server-ID die verlassen werden soll")
    async def serverleave(self, interaction: discord.Interaction, guild_id: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        try:
            gid = int(guild_id)
        except ValueError:
            return await interaction.response.send_message(
                embed=error_embed("❌ Ungültige ID", "Bitte eine gültige Server-ID eingeben."), ephemeral=True)
        guild = self._cog.bot.get_guild(gid)
        if guild is None:
            return await interaction.response.send_message(
                embed=error_embed("❌ Server nicht gefunden", f"Kein Server mit ID `{gid}` gefunden."), ephemeral=True)
        self._log(interaction, f"serverleave: {guild.name} ({gid})")
        await guild.leave()
        await interaction.response.send_message(
            embed=success_embed("✅  Server verlassen", f"**{guild.name}**  (`{gid}`)"), ephemeral=True)

    # ── /owneradmin userinfo ──────────────────────────────────────────────────

    @app_commands.command(name="userinfo", description="[Owner] Detaillierte Infos zu einem User (auch nicht auf Server).")
    @app_commands.describe(user_id="Discord User-ID")
    async def userinfo(self, interaction: discord.Interaction, user_id: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        try:
            uid = int(user_id)
        except ValueError:
            return await interaction.response.send_message(
                embed=error_embed("❌ Ungültige ID", "Bitte eine gültige User-ID eingeben."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self._cog.bot.fetch_user(uid)
        except discord.NotFound:
            return await interaction.followup.send(
                embed=error_embed("❌ Nicht gefunden", f"Kein User mit ID `{uid}` gefunden."), ephemeral=True)
        member = interaction.guild.get_member(uid) if interaction.guild else None
        embed  = discord.Embed(
            title=f"👤  {user}",
            color=member.color if member and member.color != discord.Color.default() else discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="🆔 ID",         value=f"`{user.id}`",                                          inline=True)
        embed.add_field(name="🤖 Bot",         value="Ja" if user.bot else "Nein",                            inline=True)
        embed.add_field(name="📅 Erstellt",    value=f"<t:{int(user.created_at.timestamp())}:D>",             inline=True)
        if member:
            embed.add_field(name="📥 Beigetreten", value=f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "—", inline=True)
            embed.add_field(name="🎨 Farbe",       value=str(member.color),                                   inline=True)
            roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"][:10]
            embed.add_field(name=f"🏷️ Rollen ({len(member.roles)-1})", value=" ".join(roles) or "—",          inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /owneradmin say ───────────────────────────────────────────────────────

    @app_commands.command(name="say", description="[Owner] Bot spricht im aktuellen Kanal (löscht Befehl).")
    @app_commands.describe(text="Was der Bot sagen soll")
    async def say(self, interaction: discord.Interaction, text: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"say: {text!r}")
        await interaction.response.send_message("✅", ephemeral=True)
        await interaction.channel.send(content=text)

    # ── /owneradmin activity ──────────────────────────────────────────────────

    @app_commands.command(name="activity", description="[Owner] Bot-Aktivitätsstatus sofort ändern.")
    @app_commands.describe(
        typ="Aktivitätstyp",
        text="Angezeigter Text",
    )
    @app_commands.choices(typ=[
        app_commands.Choice(name="🎮 Playing",   value="playing"),
        app_commands.Choice(name="📺 Watching",  value="watching"),
        app_commands.Choice(name="🎧 Listening", value="listening"),
        app_commands.Choice(name="🏆 Competing", value="competing"),
        app_commands.Choice(name="📡 Streaming", value="streaming"),
    ])
    async def activity(self, interaction: discord.Interaction, typ: str, text: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"activity: {typ} {text!r}")
        type_map = {
            "playing":   discord.ActivityType.playing,
            "watching":  discord.ActivityType.watching,
            "listening": discord.ActivityType.listening,
            "competing": discord.ActivityType.competing,
            "streaming": discord.ActivityType.streaming,
        }
        await self._cog.bot.change_presence(
            activity=discord.Activity(type=type_map[typ], name=text))
        await interaction.response.send_message(
            embed=success_embed("✅  Aktivität gesetzt", f"**{typ.title()}** {text}"), ephemeral=True)

    # ── /owneradmin status ────────────────────────────────────────────────────

    @app_commands.command(name="status", description="[Owner] Bot Online-Status ändern.")
    @app_commands.choices(status=[
        app_commands.Choice(name="🟢 Online",        value="online"),
        app_commands.Choice(name="🟡 Idle",          value="idle"),
        app_commands.Choice(name="🔴 Do Not Disturb", value="dnd"),
        app_commands.Choice(name="⚫ Invisible",      value="invisible"),
    ])
    async def status(self, interaction: discord.Interaction, status: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, f"status: {status}")
        status_map = {
            "online":    discord.Status.online,
            "idle":      discord.Status.idle,
            "dnd":       discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }
        await self._cog.bot.change_presence(status=status_map[status])
        await interaction.response.send_message(
            embed=success_embed("✅  Status geändert", f"Bot-Status: **{status}**"), ephemeral=True)

    # ── /owneradmin slowmode ──────────────────────────────────────────────────

    @app_commands.command(name="slowmode", description="[Owner] Slowmode in einem Kanal setzen.")
    @app_commands.describe(channel="Ziel-Kanal", sekunden="Verzögerung in Sekunden (0 = aus, max 21600)")
    async def slowmode(self, interaction: discord.Interaction,
                       channel: discord.TextChannel, sekunden: int):
        if not await _owner_only(interaction): return await _deny(interaction)
        if not 0 <= sekunden <= 21600:
            return await interaction.response.send_message(
                embed=error_embed("❌ Ungültiger Wert", "Erlaubt: 0–21600 Sekunden."), ephemeral=True)
        self._log(interaction, f"slowmode: #{channel.name} → {sekunden}s")
        try:
            await channel.edit(slowmode_delay=sekunden)
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", f"Kann {channel.mention} nicht bearbeiten."), ephemeral=True)
        msg = f"**{sekunden}s** in {channel.mention}" if sekunden > 0 else f"Slowmode in {channel.mention} deaktiviert."
        await interaction.response.send_message(
            embed=success_embed("⏱️  Slowmode gesetzt", msg), ephemeral=True)

    # ── /owneradmin nickname ──────────────────────────────────────────────────

    @app_commands.command(name="nickname", description="[Owner] Nickname des Bots auf diesem Server ändern.")
    @app_commands.describe(name="Neuer Nickname (leer lassen zum Zurücksetzen)")
    async def nickname(self, interaction: discord.Interaction, name: str = ""):
        if not await _owner_only(interaction): return await _deny(interaction)
        if interaction.guild is None:
            return await interaction.response.send_message(
                embed=error_embed("❌ Nur auf Servern."), ephemeral=True)
        self._log(interaction, f"nickname: {name!r}")
        try:
            await interaction.guild.me.edit(nick=name or None)
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Kann Nickname nicht ändern."), ephemeral=True)
        msg = f"Nickname gesetzt: **{name}**" if name else "Nickname zurückgesetzt."
        await interaction.response.send_message(
            embed=success_embed("✏️  Nickname geändert", msg), ephemeral=True)

    # ── /owneradmin massrole ──────────────────────────────────────────────────

    @app_commands.command(name="massrole", description="[Owner] Alle Mitglieder bekommen eine Rolle (add/remove).")
    @app_commands.describe(
        action="Hinzufügen oder Entfernen",
        role="Ziel-Rolle",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="➕ Hinzufügen", value="add"),
        app_commands.Choice(name="➖ Entfernen",  value="remove"),
    ])
    async def massrole(self, interaction: discord.Interaction,
                       action: str, role: discord.Role):
        if not await _owner_only(interaction): return await _deny(interaction)
        if interaction.guild is None:
            return await interaction.response.send_message(
                embed=error_embed("❌ Nur auf Servern."), ephemeral=True)
        self._log(interaction, f"massrole: {action} {role.name}")
        await interaction.response.defer(ephemeral=True)
        members = [m for m in interaction.guild.members if not m.bot]
        ok = fail = 0
        for member in members:
            try:
                if action == "add":
                    await member.add_roles(role, reason=f"massrole von {interaction.user}")
                else:
                    await member.remove_roles(role, reason=f"massrole von {interaction.user}")
                ok += 1
            except discord.HTTPException:
                fail += 1
            await asyncio.sleep(0.5)   # Rate-Limit-Schutz
        await interaction.followup.send(
            embed=success_embed(
                f"✅  Massrole {'vergeben' if action == 'add' else 'entfernt'}",
                f"Rolle {role.mention}\n✅ **{ok}** erfolgreich · ❌ **{fail}** fehlgeschlagen",
            ), ephemeral=True)

    # ── /owneradmin dmowner ───────────────────────────────────────────────────

    @app_commands.command(name="dmowners", description="[Owner] Nachricht an alle Bot-Owner schicken.")
    @app_commands.describe(text="Nachrichtentext")
    async def dmowners(self, interaction: discord.Interaction, text: str):
        if not await _owner_only(interaction): return await _deny(interaction)
        from utils.owners import OWNER_IDS
        self._log(interaction, f"dmowners: {text!r}")
        sent = fail = 0
        for uid in OWNER_IDS:
            if uid == interaction.user.id:
                continue
            try:
                user = await self._cog.bot.fetch_user(uid)
                await user.send(
                    embed=info_embed(
                        f"📩  Nachricht von {interaction.user}",
                        text,
                    )
                )
                sent += 1
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                fail += 1
        await interaction.response.send_message(
            embed=success_embed(
                "✅  DM an Owner gesendet",
                f"**{sent}** Owner erreicht · **{fail}** fehlgeschlagen",
            ), ephemeral=True)

    # ── /owneradmin serverinfo ────────────────────────────────────────────────

    @app_commands.command(name="serverinfo", description="[Owner] Detaillierte Infos zum aktuellen Server.")
    async def serverinfo(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        g = interaction.guild
        if g is None:
            return await interaction.response.send_message(
                embed=error_embed("❌ Nur auf Servern."), ephemeral=True)
        bots    = sum(1 for m in g.members if m.bot)
        humans  = g.member_count - bots if g.member_count else 0
        embed   = discord.Embed(
            title=f"🏰  {g.name}",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if g.icon: embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="🆔 ID",            value=f"`{g.id}`",                                    inline=True)
        embed.add_field(name="👑 Owner",          value=f"<@{g.owner_id}>",                             inline=True)
        embed.add_field(name="📅 Erstellt",       value=f"<t:{int(g.created_at.timestamp())}:D>",       inline=True)
        embed.add_field(name="👥 Mitglieder",     value=f"{humans} Menschen · {bots} Bots",             inline=True)
        embed.add_field(name="📡 Kanäle",         value=str(len(g.channels)),                           inline=True)
        embed.add_field(name="🏷️ Rollen",         value=str(len(g.roles)),                              inline=True)
        embed.add_field(name="😀 Emojis",         value=str(len(g.emojis)),                             inline=True)
        embed.add_field(name="🔒 Verifikation",   value=str(g.verification_level).title(),              inline=True)
        embed.add_field(name="⭐ Boosts",         value=f"{g.premium_subscription_count} (Stufe {g.premium_tier})", inline=True)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /owneradmin trustuser ─────────────────────────────────────────────────

    @app_commands.command(name="trustuser", description="[Owner] Einem User /owner-Zugriff erteilen.")
    @app_commands.describe(user="Der User der Zugriff bekommen soll")
    async def trustuser(self, interaction: discord.Interaction, user: discord.Member):
        if not await _owner_only(interaction): return await _deny(interaction)
        # Nur statische .env-Owner dürfen trust vergeben — keine Kette
        from utils.owners import OWNER_IDS
        if interaction.user.id not in OWNER_IDS:
            return await interaction.response.send_message(
                embed=error_embed(
                    "❌ Nur echte Owner",
                    "Nur in der `.env` konfigurierte Owner dürfen weitere User berechtigen.",
                ), ephemeral=True)
        if user.id in OWNER_IDS:
            return await interaction.response.send_message(
                embed=info_embed("ℹ️  Bereits Owner", f"{user.mention} ist bereits ein statischer Owner."),
                ephemeral=True)
        added = await add_trusted(user.id)
        self._log(interaction, f"trustuser: {user} ({user.id})")
        if added:
            await interaction.response.send_message(
                embed=success_embed(
                    "✅  Zugriff erteilt",
                    f"{user.mention} hat jetzt Zugriff auf alle `/owner`- und `/owneradmin`-Commands.\n"
                    f"⚠️ Dieser Zugriff wird in `data/trusted_owners.json` gespeichert und bleibt nach einem Neustart erhalten.",
                ), ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=info_embed("ℹ️  Bereits berechtigt", f"{user.mention} hat den Zugriff bereits."),
                ephemeral=True)

    # ── /owneradmin untrustuser ───────────────────────────────────────────────

    @app_commands.command(name="untrustuser", description="[Owner] Einem User den /owner-Zugriff entziehen.")
    @app_commands.describe(user="Der User dem der Zugriff entzogen werden soll")
    async def untrustuser(self, interaction: discord.Interaction, user: discord.Member):
        if not await _owner_only(interaction): return await _deny(interaction)
        from utils.owners import OWNER_IDS
        if user.id in OWNER_IDS:
            return await interaction.response.send_message(
                embed=error_embed(
                    "❌ Statischer Owner",
                    f"{user.mention} ist ein statischer Owner aus der `.env` — kann nicht entfernt werden.",
                ), ephemeral=True)
        removed = await remove_trusted(user.id)
        self._log(interaction, f"untrustuser: {user} ({user.id})")
        if removed:
            await interaction.response.send_message(
                embed=success_embed("✅  Zugriff entzogen", f"{user.mention} hat keinen `/owner`-Zugriff mehr."),
                ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=info_embed("ℹ️  Nicht berechtigt", f"{user.mention} hatte keinen erteilten Zugriff."),
                ephemeral=True)

    # ── /owneradmin trustedlist ───────────────────────────────────────────────

    @app_commands.command(name="trustedlist", description="[Owner] Alle berechtigten User anzeigen.")
    async def trustedlist(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        from utils.owners import OWNER_IDS
        trusted = await get_trusted_ids()
        lines = []
        # Statische Owner
        for uid in sorted(OWNER_IDS):
            m    = interaction.guild.get_member(uid) if interaction.guild else None
            name = m.mention if m else f"<@{uid}>"
            lines.append(f"🔒 {name}  (`{uid}`)  — *statisch (.env)*")
        # Dynamische Trusted
        for uid in sorted(trusted):
            if uid in OWNER_IDS:
                continue   # bereits oben aufgeführt
            m    = interaction.guild.get_member(uid) if interaction.guild else None
            name = m.mention if m else f"<@{uid}>"
            lines.append(f"✅ {name}  (`{uid}`)  — *per Command erteilt*")
        embed = info_embed(
            f"👑  Owner-Berechtigungen  ({len(OWNER_IDS) + len(trusted - OWNER_IDS)})",
            "\n".join(lines) or "*Keine Einträge.*",
        )
        embed.set_footer(text=f"🔒 = .env  ·  ✅ = per /owneradmin trustuser erteilt  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /owneradmin clearcache ────────────────────────────────────────────────

    @app_commands.command(name="clearcache", description="[Owner] JSONStore TTL-Cache für alle Dateien invalidieren.")
    async def clearcache(self, interaction: discord.Interaction):
        if not await _owner_only(interaction): return await _deny(interaction)
        self._log(interaction, "clearcache")
        count = 0
        for store in JSONStore._locks:
            # Cache-Invalidierung: alle bekannten JSONStore-Instanzen finden wir über die Locks
            count += 1
        # Direkte Cache-Invalidierung: setze _cache_ts aller Instanzen auf 0
        # Da JSONStore._locks nur die Pfade kennt, erstellen wir kurz neue Instanzen
        # und setzen deren Cache — einfachste zuverlässige Methode:
        from utils.storage import JSONStore as _JS
        cleared = len(_JS._locks)
        for path in list(_JS._locks.keys()):
            # Jeden Cache direkt invalidieren via gc
            import gc
            for obj in gc.get_objects():
                if isinstance(obj, _JS) and obj.path == path:
                    obj._cache    = None
                    obj._cache_ts = 0.0
                    break
        await interaction.response.send_message(
            embed=success_embed("🗄️  Cache geleert", f"**{cleared}** JSONStore-Einträge invalidiert."),
            ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class OwnerPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot           = bot
        self._process      = psutil.Process(os.getpid()) if psutil else None
        self.ticket_config = JSONStore("data/tickets_config.json", {"panels": {}})
        # Gruppen registrieren
        self._group        = OwnerGroup(self)
        self._admin_group  = OwnerAdminGroup(self)
        self.bot.tree.add_command(self._group)
        self.bot.tree.add_command(self._admin_group)

    def cog_unload(self):
        self.bot.tree.remove_command(self._group.name)
        self.bot.tree.remove_command(self._admin_group.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerPanel(bot))
