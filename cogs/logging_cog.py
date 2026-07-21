"""
Lückenlose Protokollierung: Command-Nutzung, verweigerte Zugriffe,
gelöschte/editierte Nachrichten — alles landet im konfigurierten Admin-Log-Kanal.

Performance: In-Memory-Config-Cache vermeidet store.read() bei jedem Event.
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, FOOTER_TEXT, COLOR_DARK, COLOR_INFO, get_footer_text
from utils.permissions import check_role_permission

CONFIG_PATH = "data/logging_config.json"


def default_config():
    return {}  # guild_id -> {"log_channel_id": int}


class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self.config_store = JSONStore(CONFIG_PATH, default_config())
        # In-Memory-Cache für den Log-Kanal (guild_id -> channel_id | None)
        self._channel_cache: dict[int, int | None] = {}

    def _invalidate(self, guild_id: int) -> None:
        self._channel_cache.pop(guild_id, None)

    async def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        # Cache-Hit: kein Store-Zugriff nötig
        if guild.id in self._channel_cache:
            cid = self._channel_cache[guild.id]
            return guild.get_channel(cid) if cid else None
        # Cache-Miss: aus Store laden und cachen
        config = await self.config_store.read()
        cid    = config.get(str(guild.id), {}).get("log_channel_id")
        self._channel_cache[guild.id] = cid
        return guild.get_channel(cid) if cid else None

    # ── /admin-log-set ────────────────────────────────────────────────────────

    @app_commands.command(name="admin-log-set", description="Legt den Kanal für Admin-Logs fest.")
    async def admin_log_set(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)

        def mutate(data):
            data.setdefault(guild_id, {})["log_channel_id"] = kanal.id
            return data

        await self.config_store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed("✅ Admin-Log konfiguriert",
                                f"Alle Admin-Logs werden ab jetzt in {kanal.mention} protokolliert."),
            ephemeral=True,
        )

    # ── Command-Nutzung protokollieren ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command: app_commands.Command):
        if interaction.guild is None:
            return
        log_channel = await self._get_log_channel(interaction.guild)
        if log_channel is None:
            return

        embed = discord.Embed(
            title="📜 Command genutzt",
            description=(
                f"**Befehl:** `/{command.qualified_name}`\n"
                f"**User:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                f"**Kanal:** {interaction.channel.mention if interaction.channel else 'DM'}"
            ),
            color=COLOR_INFO,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=get_footer_text(interaction))
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if interaction.guild is None:
            return
        if not isinstance(error, app_commands.MissingPermissions):
            return
        log_channel = await self._get_log_channel(interaction.guild)
        if log_channel is None:
            return

        embed = discord.Embed(
            title="🚫 Zugriff verweigert",
            description=(
                f"**User:** {interaction.user.mention} hat versucht, einen Befehl ohne "
                f"ausreichende Rechte zu nutzen.\n"
                f"**Befehl:** `/{interaction.command.qualified_name if interaction.command else 'unbekannt'}`"
            ),
            color=COLOR_DARK,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=get_footer_text(interaction))
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ── Message-Logs ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        log_channel = await self._get_log_channel(message.guild)
        if log_channel is None:
            return

        embed = discord.Embed(
            title="🗑️ Nachricht gelöscht",
            description=(
                f"**Autor:** {message.author.mention} (`{message.author.id}`)\n"
                f"**Kanal:** {message.channel.mention}\n"
                f"**Inhalt:** {message.content[:1000] or '[kein Textinhalt / Embed / Anhang]'}"
            ),
            color=discord.Color.dark_grey(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=get_footer_text(message.guild))
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return
        log_channel = await self._get_log_channel(before.guild)
        if log_channel is None:
            return

        embed = discord.Embed(
            title="✏️ Nachricht bearbeitet",
            description=f"**Autor:** {before.author.mention}\n**Kanal:** {before.channel.mention}",
            color=discord.Color.dark_gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="Vorher",  value=before.content[:500] or "[leer]", inline=False)
        embed.add_field(name="Nachher", value=after.content[:500]  or "[leer]", inline=False)
        embed.set_footer(text=get_footer_text(before.guild))
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ── Voice-Logs ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after:  discord.VoiceState,
    ):
        if member.guild is None:
            return
        log_channel = await self._get_log_channel(member.guild)
        if log_channel is None:
            return

        if before.channel == after.channel:
            return

        if before.channel is None and after.channel:
            title  = "🔊 Voice beigetreten"
            color  = discord.Color.from_rgb(46, 204, 113)
            desc   = f"**User:** {member.mention}\n**Kanal:** {after.channel.mention}"
        elif before.channel and after.channel is None:
            title  = "🔇 Voice verlassen"
            color  = discord.Color.from_rgb(231, 76, 60)
            desc   = f"**User:** {member.mention}\n**Kanal:** {before.channel.mention}"
        else:
            title  = "🔀 Voice-Kanal gewechselt"
            color  = discord.Color.dark_gold()
            desc   = (f"**User:** {member.mention}\n"
                      f"**Von:** {before.channel.mention}\n"
                      f"**Nach:** {after.channel.mention}")

        embed = discord.Embed(
            title=title,
            description=desc,
            color=color,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=get_footer_text(member.guild))
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ── Nickname-Änderung loggen ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick == after.nick:
            return
        log_channel = await self._get_log_channel(after.guild)
        if log_channel is None:
            return

        embed = discord.Embed(
            title="📝 Nickname geändert",
            description=f"**User:** {after.mention}",
            color=discord.Color.dark_gold(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="Vorher", value=before.nick or before.name, inline=True)
        embed.add_field(name="Nachher", value=after.nick or after.name,  inline=True)
        embed.set_footer(text=get_footer_text(after.guild))
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ── Member Join / Leave Logs ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        log_channel = await self._get_log_channel(member.guild)
        if log_channel is None:
            return
        days  = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
        embed = discord.Embed(
            title="📥 Mitglied beigetreten",
            description=(
                f"**User:** {member.mention} (`{member.id}`)\n"
                f"**Account-Alter:** {days} Tage"
                + ("  ⚠️ *Neuer Account!*" if days < 7 else "")
            ),
            color=discord.Color.from_rgb(46, 204, 113),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=get_footer_text(member.guild))
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        log_channel = await self._get_log_channel(member.guild)
        if log_channel is None:
            return
        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        embed = discord.Embed(
            title="📤 Mitglied verlassen",
            description=(
                f"**User:** {member.mention} (`{member.id}`)\n"
                f"**Rollen:** {', '.join(roles) if roles else 'Keine'}"
            ),
            color=discord.Color.from_rgb(231, 76, 60),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=get_footer_text(member.guild))
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(LoggingCog(bot))
