"""Interaktives Discord-Control-Center für Administratoren."""
from __future__ import annotations

import datetime
import math

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ratings import compute_rating_stats
from cogs.tickets import OPEN_TICKETS_PATH
from utils.storage import JSONStore
from utils.system_state import get_maintenance_state
from utils.theme import error_embed, info_embed, success_embed


class PanelView(discord.ui.View):
    def __init__(self, cog: "ControlPanel"):
        super().__init__(timeout=300)
        self.cog = cog
        self.message: discord.InteractionMessage | None = None

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message(embed=error_embed("❌ Kein Zugriff", "Dieses Dashboard ist nur für die Serververwaltung."), ephemeral=True)
        return False

    @discord.ui.button(label="Übersicht", emoji="📊", style=discord.ButtonStyle.primary)
    async def overview(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self._guard(interaction):
            await interaction.response.edit_message(embed=await self.cog.overview_embed(interaction.guild), view=self)

    @discord.ui.button(label="Security", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def security(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self._guard(interaction):
            await interaction.response.edit_message(embed=await self.cog.security_embed(interaction.guild), view=self)

    @discord.ui.button(label="Tickets", emoji="🎫", style=discord.ButtonStyle.secondary)
    async def tickets(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self._guard(interaction):
            await interaction.response.edit_message(embed=await self.cog.tickets_embed(interaction.guild), view=self)

    @discord.ui.button(label="Health", emoji="❤️", style=discord.ButtonStyle.secondary)
    async def health(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self._guard(interaction):
            await interaction.response.edit_message(embed=await self.cog.health_embed(interaction.guild), view=self)

    @discord.ui.button(label="Aktualisieren", emoji="🔄", style=discord.ButtonStyle.success, row=1)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if await self._guard(interaction):
            await interaction.response.edit_message(embed=await self.cog.overview_embed(interaction.guild), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try: await self.message.edit(view=self)
            except discord.HTTPException: pass


class ControlPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tickets = JSONStore(OPEN_TICKETS_PATH, {})

    @staticmethod
    def _footer(embed: discord.Embed, guild: discord.Guild) -> discord.Embed:
        embed.set_footer(text=f"{guild.name} • Control Center")
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        return embed

    async def overview_embed(self, guild: discord.Guild) -> discord.Embed:
        tickets = await self.tickets.read()
        guild_tickets = [item for channel_id, item in tickets.items() if guild.get_channel(int(channel_id))]
        ratings = await compute_rating_stats()
        maintenance = (await get_maintenance_state()).get("maintenance", False)
        ping = round(self.bot.latency * 1000) if math.isfinite(self.bot.latency) else 0
        launch = getattr(self.bot, "launch_time", datetime.datetime.now(datetime.timezone.utc))
        uptime = datetime.datetime.now(datetime.timezone.utc) - launch
        hours, rem = divmod(int(uptime.total_seconds()), 3600); minutes = rem // 60
        embed = info_embed("📊 Server Control Center", "Deine Live-Zentrale für System, Sicherheit und Support.")
        embed.add_field(name="👥 Community", value=f"**{guild.member_count or 0}** Mitglieder\n**{len(guild.channels)}** Kanäle", inline=True)
        embed.add_field(name="🎫 Tickets", value=f"**{len(guild_tickets)}** offen\nSupport: **{ratings['average']:.1f}/5**", inline=True)
        embed.add_field(name="🤖 Bot", value=f"Ping: **{ping} ms**\nUptime: **{hours}h {minutes}m**", inline=True)
        embed.add_field(name="⚙️ Schnellzugriff", value="`/automod preset` · `/security status` · `/ticket info`", inline=False)
        embed.add_field(name="Systemstatus", value="⚠️ Wartung aktiv" if maintenance else "✅ Alle Systeme normal", inline=False)
        return self._footer(embed, guild)

    async def security_embed(self, guild: discord.Guild) -> discord.Embed:
        security = self.bot.get_cog("Security")
        if security is None:
            return self._footer(error_embed("🛡️ Security nicht verfügbar"), guild)
        config = await security._config(guild.id)
        active = [name for key, name in security.RULES.items()] if False else [key for key, on in config["rules"].items() if on]
        history = (await security.history_store.read()).get(str(guild.id), [])
        embed = info_embed("🛡️ Security Center", f"**{len(active)}/{len(config['rules'])}** Schutzregeln aktiv · **{len(history)}** gespeicherte Vorfälle")
        embed.add_field(name="Reaktion", value=f"`{config['action']}`", inline=True)
        embed.add_field(name="Notfallmodus", value="🚨 Aktiv" if config["emergency"] else "○ Inaktiv", inline=True)
        embed.add_field(name="Audit-Log", value="✅ Konfiguriert" if config["log_channel_id"] else "❌ Nicht gesetzt", inline=True)
        embed.add_field(name="Nächste Schritte", value="`/security incidents` · `/security score` · `/security emergency`", inline=False)
        return self._footer(embed, guild)

    async def tickets_embed(self, guild: discord.Guild) -> discord.Embed:
        data = await self.tickets.read()
        entries = [(guild.get_channel(int(channel_id)), info) for channel_id, info in data.items() if guild.get_channel(int(channel_id))]
        claimed = sum(1 for _, info in entries if info.get("claimed_by"))
        urgent = sum(1 for _, info in entries if info.get("priority") in ("hoch", "dringend"))
        preview = "\n".join(f"• {channel.mention} — `{info.get('priority', 'normal')}`" for channel, info in entries[:8]) or "Keine offenen Tickets."
        embed = info_embed("🎫 Ticket-Dashboard", preview)
        embed.add_field(name="Offen", value=str(len(entries)), inline=True)
        embed.add_field(name="Übernommen", value=str(claimed), inline=True)
        embed.add_field(name="Hoch/Dringend", value=str(urgent), inline=True)
        embed.add_field(name="Support-Workflow", value="`/ticket claim` · `/ticket priority` · `/ticket add`", inline=False)
        return self._footer(embed, guild)

    async def health_embed(self, guild: discord.Guild) -> discord.Embed:
        me = guild.me
        required = ("manage_channels", "manage_roles", "manage_messages", "moderate_members", "ban_members", "kick_members", "view_audit_log")
        missing = [permission for permission in required if not getattr(me.guild_permissions, permission, False)]
        embed = success_embed("❤️ System Health", "Alle kritischen Rechte vorhanden." if not missing else "Einige Funktionen können eingeschränkt sein.")
        embed.add_field(name="Fehlende Rechte", value=", ".join(f"`{item}`" for item in missing) if missing else "Keine", inline=False)
        embed.add_field(name="Empfehlung", value="Nutze `/panel` regelmäßig und prüfe nach Rollenänderungen die Bot-Hierarchie.", inline=False)
        return self._footer(embed, guild)

    @app_commands.command(name="panel", description="Öffnet das interaktive Server-Control-Center.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        view = PanelView(self)
        await interaction.response.send_message(embed=await self.overview_embed(interaction.guild), view=view, ephemeral=True)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ControlPanel(bot))
