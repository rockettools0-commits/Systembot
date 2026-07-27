"""
Server-Tools: /serverinfo, /botinfo, /backup (Admin).
Gibt detaillierte Infos über den Server oder Bot aus,
und exportiert alle JSON-Datendateien als ZIP-Archiv.
"""

import datetime
import io
import os
import platform
import sys
import time
import zipfile

import discord
from discord import app_commands
from discord.ext import commands

from utils.theme import info_embed, success_embed, error_embed, FOOTER_TEXT, COLOR_INFO, COLOR_DARK, COLOR_PRIMARY, get_footer_text

DATA_DIR = "data"
START_TIME = time.monotonic()


def _uptime_str() -> str:
    secs  = int(time.monotonic() - START_TIME)
    hours, rem = divmod(secs, 3600)
    mins, sec  = divmod(rem, 60)
    return f"{hours}h {mins}m {sec}s"


class ServerTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._start = time.monotonic()

    # ── /serverinfo ───────────────────────────────────────────────────────────

    @app_commands.command(name="serverinfo", description="Zeigt detaillierte Informationen über den Server.")
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild

        text_ch  = sum(1 for c in g.channels if isinstance(c, discord.TextChannel))
        voice_ch = sum(1 for c in g.channels if isinstance(c, discord.VoiceChannel))
        cats     = sum(1 for c in g.channels if isinstance(c, discord.CategoryChannel))
        bots     = sum(1 for m in g.members if m.bot)
        humans   = g.member_count - bots
        online   = sum(1 for m in g.members if not m.bot and m.status != discord.Status.offline)

        embed = discord.Embed(
            title=f"🏰  {g.name}",
            color=discord.Color.from_rgb(84, 153, 199),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)

        embed.add_field(name="🆔 Server-ID",      value=f"`{g.id}`",                                  inline=True)
        embed.add_field(name="👑 Besitzer",        value=g.owner.mention if g.owner else "N/A",        inline=True)
        embed.add_field(name="📅 Erstellt",        value=discord.utils.format_dt(g.created_at, "D"),   inline=True)
        embed.add_field(name="👥 Mitglieder",      value=f"**{g.member_count:,}** gesamt",             inline=True)
        embed.add_field(name="🧑 Menschen",        value=f"{humans:,}  ·  🟢 {online:,} online",       inline=True)
        embed.add_field(name="🤖 Bots",            value=str(bots),                                    inline=True)
        embed.add_field(name="💬 Kanäle",          value=f"{text_ch} Text · {voice_ch} Voice · {cats} Kat.", inline=True)
        embed.add_field(name="🎭 Rollen",          value=str(len(g.roles)),                            inline=True)
        embed.add_field(name="😀 Emojis",          value=str(len(g.emojis)),                           inline=True)
        embed.add_field(name="🔒 Verifikation",    value=str(g.verification_level).title(),            inline=True)
        embed.add_field(name="💎 Boosts",          value=f"Stufe {g.premium_tier} · {g.premium_subscription_count}×", inline=True)
        embed.add_field(name="🌐 Region",          value=str(g.preferred_locale),                      inline=True)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    # ── /botinfo ──────────────────────────────────────────────────────────────

    @app_commands.command(name="botinfo", description="Zeigt Informationen über den Bot.")
    async def botinfo(self, interaction: discord.Interaction):
        bot = self.bot
        guilds   = len(bot.guilds)
        members  = sum(g.member_count for g in bot.guilds)
        latency  = round(bot.latency * 1000)
        uptime   = _uptime_str()
        ping_color = (
            discord.Color.from_rgb(88, 214, 141) if latency < 100
            else discord.Color.from_rgb(243, 156, 18) if latency < 200
            else discord.Color.from_rgb(235, 77, 75)
        )

        guild_name = interaction.guild.name if interaction.guild else "System"
        embed = discord.Embed(
            title=f"🤖  {bot.user.name}",
            description=f"{guild_name} — Bot-System",
            color=ping_color,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.add_field(name="📡 Ping",         value=f"`{latency} ms`",           inline=True)
        embed.add_field(name="⏱️ Uptime",       value=f"`{uptime}`",               inline=True)
        embed.add_field(name="🏰 Server",       value=f"`{guilds}`",               inline=True)
        embed.add_field(name="👥 User gesamt",  value=f"`{members:,}`",            inline=True)
        embed.add_field(name="🐍 Python",       value=f"`{platform.python_version()}`", inline=True)
        embed.add_field(name="📦 discord.py",   value=f"`{discord.__version__}`",  inline=True)
        embed.add_field(name="💻 System",
                        value=f"`{platform.system()} {platform.release()}`",       inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    # ── /backup ───────────────────────────────────────────────────────────────

    @app_commands.command(
        name="backup",
        description="Exportiert alle Bot-Datendateien als ZIP-Archiv (nur Admins).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def backup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        buf = io.BytesIO()
        json_files = [
            f for f in os.listdir(DATA_DIR)
            if f.endswith(".json") and os.path.isfile(os.path.join(DATA_DIR, f))
        ]

        if not json_files:
            await interaction.followup.send(
                embed=error_embed("❌ Keine Dateien", "Keine JSON-Dateien im data/-Ordner gefunden."),
                ephemeral=True,
            )
            return

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname in json_files:
                path = os.path.join(DATA_DIR, fname)
                zf.write(path, arcname=fname)

        buf.seek(0)
        ts   = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        file = discord.File(buf, filename=f"hugosmp_backup_{ts}.zip")

        embed = success_embed(
            "📦 Backup erstellt",
            f"**{len(json_files)}** Datei(en) exportiert.\n"
            f"Zeitstempel: `{ts}`",
        )
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            msg = error_embed("❌ Keine Berechtigung", "Du benötigst Administrator-Rechte.")
        else:
            msg = error_embed("❌ Fehler", str(error))
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerTools(bot))
