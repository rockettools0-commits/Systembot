"""
Clan-Action-Log System — vollständig überarbeitet.

/clanlog-setup  — Setzt den dedizierten Clan-Log-Kanal.
/warn-log       — Verwarnung manuell in den Log schicken.
/uprank         — Rang-Aufstieg eines Mitglieds loggen + Rolle tauschen.
/derank         — Rang-Abstieg eines Mitglieds loggen + Rolle tauschen.
/clan-kick      — Clan-Kick loggen (Server-Kick optional).

Alle anderen Mod-Aktionen (Ban, Mute, Unban, Unmute) werden über
bot.dispatch("clan_action", ...) von anderen Cogs ausgelöst und landen
automatisch mit modernem Embed-Design im konfigurierten Kanal.

Jedes Log-Embed enthält:
  • Farbcodiertes Banner-Design per Aktion
  • Avatar des betroffenen Members
  • Moderator mit Avatar
  • Grund / Aktion
  • Zeitstempel + Case-Nummer
"""

import datetime
import time as _time

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import FOOTER_TEXT, error_embed, get_footer_text
from utils.permissions import check_role_permission

CLANLOG_CONFIG_PATH = "data/clanlog_config.json"
CLANLOG_CASES_PATH  = "data/clanlog_cases.json"

# ── Farben & Icons je Aktion ──────────────────────────────────────────────────
ACTION_STYLES: dict[str, tuple[discord.Color, str]] = {
    "warn":     (discord.Color.from_rgb(230, 126, 34),  "⚠️"),
    "uprank":   (discord.Color.from_rgb(46,  204, 113), "⬆️"),
    "derank":   (discord.Color.from_rgb(231, 76,  60),  "⬇️"),
    "kick":     (discord.Color.from_rgb(231, 76,  60),  "👢"),
    "ban":      (discord.Color.from_rgb(120, 20,  20),  "🔨"),
    "mute":     (discord.Color.from_rgb(149, 165, 166), "🔇"),
    "unmute":   (discord.Color.from_rgb(46,  204, 113), "🔊"),
    "unban":    (discord.Color.from_rgb(46,  204, 113), "✅"),
    "verify":   (discord.Color.from_rgb(52,  152, 219), "🔐"),
    "join":     (discord.Color.from_rgb(46,  204, 113), "📥"),
    "leave":    (discord.Color.from_rgb(231, 76,  60),  "📤"),
    "role_add": (discord.Color.from_rgb(52,  152, 219), "🎭"),
    "role_rem": (discord.Color.from_rgb(149, 165, 166), "🎭"),
}

ACTION_LABELS: dict[str, str] = {
    "warn":     "Verwarnung",
    "uprank":   "Rang-Aufstieg",
    "derank":   "Rang-Abstieg",
    "kick":     "Clan-Kick",
    "ban":      "Ban",
    "mute":     "Mute",
    "unmute":   "Unmute",
    "unban":    "Unban",
    "verify":   "Verifiziert",
    "join":     "Beigetreten",
    "leave":    "Verlassen",
    "role_add": "Rolle erhalten",
    "role_rem": "Rolle entfernt",
}


def default_config() -> dict:
    return {}  # guild_id -> {"channel_id": int}


def default_cases() -> dict:
    return {}  # guild_id -> {"count": int}


def _next_case(data: dict, guild_id: str) -> int:
    guild = data.setdefault(guild_id, {"count": 0})
    guild["count"] += 1
    return guild["count"]


def _build_action_embed(
    action:    str,
    member:    discord.Member | discord.User,
    moderator: discord.Member | None,
    grund:     str,
    case_nr:   int,
    extra:     str = "",
    guild:     discord.Guild | None = None,
) -> discord.Embed:
    """Baut ein vollständiges, modernes Clan-Action-Embed."""
    color, icon = ACTION_STYLES.get(action, (discord.Color.blurple(), "📋"))
    label       = ACTION_LABELS.get(action, action.title())

    embed = discord.Embed(
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    # Titel-Zeile mit Case-Nummer
    embed.set_author(
        name=f"{icon}  {label}  ·  Case #{case_nr:04d}",
        icon_url=member.display_avatar.url if hasattr(member, "display_avatar") else None,
    )

    # Member-Info
    embed.add_field(
        name="👤 Mitglied",
        value=f"{member.mention}\n`{member}` · `{member.id}`",
        inline=True,
    )

    # Moderator-Info
    if moderator:
        embed.add_field(
            name="🛡️ Moderator",
            value=f"{moderator.mention}\n`{moderator}`",
            inline=True,
        )
    else:
        embed.add_field(name="🤖 Ausgelöst durch", value="Automatik / System", inline=True)

    # Leerzeile für Layout
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    # Grund
    embed.add_field(
        name="📝 Grund / Details",
        value=grund or "Kein Grund angegeben",
        inline=False,
    )

    # Extra-Info (z.B. alte/neue Rolle)
    if extra:
        embed.add_field(name="ℹ️ Info", value=extra, inline=False)

    embed.set_thumbnail(url=member.display_avatar.url if hasattr(member, "display_avatar") else None)
    from utils.theme import get_footer_text
    embed.set_footer(text=f"{get_footer_text(guild)}  ·  Case #{case_nr:04d}")

    # Farbiger Rand-Balken simuliert durch Thumbnail-Position — nichts weiter nötig
    return embed


class ClanLog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self.config_store = JSONStore(CLANLOG_CONFIG_PATH, default_config())
        self.cases_store  = JSONStore(CLANLOG_CASES_PATH,  default_cases())
        # Debounce für on_member_update: verhindert Spam bei Bulk-Rollen-Änderungen
        self._role_log_cooldown: dict[int, float] = {}  # member_id -> monotonic timestamp

    # ── Hilfsmethode: Kanal holen & Embed schicken ────────────────────────────

    async def _get_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        config = await self.config_store.read()
        cid    = config.get(str(guild.id), {}).get("channel_id")
        return guild.get_channel(cid) if cid else None

    async def post(
        self,
        guild:     discord.Guild,
        action:    str,
        member:    discord.Member | discord.User,
        moderator: discord.Member | None = None,
        grund:     str = "",
        extra:     str = "",
    ) -> None:
        """Öffentliche Methode — andere Cogs rufen dies über ClanLog-Instanz auf."""
        channel = await self._get_channel(guild)
        if channel is None:
            return

        # Case-Nummer atomar hochzählen
        case_nr_holder: dict = {}
        def inc(data):
            case_nr_holder["nr"] = _next_case(data, str(guild.id))
            return data
        await self.cases_store.update(inc)

        embed = _build_action_embed(
            action, member, moderator, grund,
            case_nr_holder["nr"], extra, guild,
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    clanlog = app_commands.Group(name="clanlog", description="Clan-Log-Verwaltung.")

    # ── /clanlog setup ────────────────────────────────────────────────────────

    @clanlog.command(
        name="setup",
        description="Setzt den dedizierten Kanal für alle Clan-Aktions-Logs.",
    )
    @app_commands.describe(kanal="Kanal der alle Clan-Aktionen protokolliert")
    @app_commands.checks.has_permissions(administrator=True)
    async def clanlog_setup(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        def mutate(data):
            data[str(interaction.guild.id)] = {"channel_id": kanal.id}
            return data
        await self.config_store.update(mutate)

        embed = discord.Embed(
            title="✅ Clan-Log konfiguriert",
            description=f"Alle Clan-Aktionen werden ab jetzt in {kanal.mention} protokolliert.",
            color=discord.Color.from_rgb(46, 204, 113),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /uprank ───────────────────────────────────────────────────────────────

    @clanlog.command(name="uprank", description="Stuft ein Mitglied hoch und loggt die Aktion.")
    @app_commands.describe(
        member="Das Mitglied das hochgestuft wird",
        alte_rolle="Die bisherige Rang-Rolle",
        neue_rolle="Die neue höhere Rang-Rolle",
        grund="Grund für den Aufstieg",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def uprank(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        alte_rolle: discord.Role,
        neue_rolle: discord.Role,
        grund: str = "Rang-Aufstieg",
    ):
        if not await check_role_permission(interaction, "clan"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await member.remove_roles(alte_rolle, reason=f"Uprank von {interaction.user}")
            await member.add_roles(neue_rolle,    reason=f"Uprank von {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("❌ Mir fehlen die Berechtigungen für diesen Rollen-Tausch.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Fehler: {e}", ephemeral=True)
            return

        await self.post(
            guild     = interaction.guild,
            action    = "uprank",
            member    = member,
            moderator = interaction.user,
            grund     = grund,
            extra     = f"{alte_rolle.mention} → {neue_rolle.mention}",
        )
        embed = discord.Embed(
            title=f"⬆️ {member.display_name} wurde hochgestuft",
            description=f"{alte_rolle.mention} → {neue_rolle.mention}\n**Grund:** {grund}",
            color=discord.Color.from_rgb(46, 204, 113),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /derank ───────────────────────────────────────────────────────────────

    @clanlog.command(name="derank", description="Stuft ein Mitglied herab und loggt die Aktion.")
    @app_commands.describe(
        member="Das Mitglied das herabgestuft wird",
        alte_rolle="Die bisherige Rang-Rolle",
        neue_rolle="Die neue niedrigere Rang-Rolle",
        grund="Grund für den Abstieg",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    async def derank(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        alte_rolle: discord.Role,
        neue_rolle: discord.Role,
        grund: str = "Rang-Abstieg",
    ):
        if not await check_role_permission(interaction, "clan"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await member.remove_roles(alte_rolle, reason=f"Derank von {interaction.user}")
            await member.add_roles(neue_rolle,    reason=f"Derank von {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("❌ Mir fehlen die Berechtigungen für diesen Rollen-Tausch.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Fehler: {e}", ephemeral=True)
            return

        await self.post(
            guild     = interaction.guild,
            action    = "derank",
            member    = member,
            moderator = interaction.user,
            grund     = grund,
            extra     = f"{alte_rolle.mention} → {neue_rolle.mention}",
        )
        embed = discord.Embed(
            title=f"⬇️ {member.display_name} wurde herabgestuft",
            description=f"{alte_rolle.mention} → {neue_rolle.mention}\n**Grund:** {grund}",
            color=discord.Color.from_rgb(231, 76, 60),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /clan-kick ────────────────────────────────────────────────────────────

    @clanlog.command(name="kick", description="Entfernt ein Mitglied aus dem Clan (optionaler Server-Kick).")
    @app_commands.describe(
        member="Das zu entfernende Mitglied",
        grund="Grund für den Clan-Kick",
        server_kick="Mitglied auch vom Server kicken? (Standard: Nein)",
    )
    @app_commands.checks.has_permissions(kick_members=True)
    async def clan_kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        grund: str = "Clan-Kick",
        server_kick: bool = False,
    ):
        if not await check_role_permission(interaction, "clan"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        if server_kick:
            try:
                await member.kick(reason=f"Clan-Kick | {grund} | Von: {interaction.user}")
            except discord.Forbidden:
                await interaction.followup.send("❌ Mir fehlen die Berechtigungen für den Server-Kick.", ephemeral=True)
                return
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ Fehler: {e}", ephemeral=True)
                return

        await self.post(
            guild     = interaction.guild,
            action    = "kick",
            member    = member,
            moderator = interaction.user,
            grund     = grund,
            extra     = f"Server-Kick: {'✅ Ja' if server_kick else '❌ Nein'}",
        )
        embed = discord.Embed(
            title=f"👢 {member.display_name} wurde aus dem Clan geworfen",
            description=f"**Grund:** {grund}\n**Server-Kick:** {'Ja' if server_kick else 'Nein'}",
            color=discord.Color.from_rgb(231, 76, 60),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Listener: Mod-Aktionen aus anderen Cogs empfangen ────────────────────

    @commands.Cog.listener()
    async def on_clan_action(
        self,
        guild:     discord.Guild,
        action:    str,
        member:    discord.Member | discord.User,
        moderator: discord.Member | None = None,
        grund:     str = "",
        extra:     str = "",
    ):
        """Andere Cogs dispatchen: bot.dispatch('clan_action', guild, action, member, ...)"""
        await self.post(guild, action, member, moderator, grund, extra)

    # ── Member Join / Leave ───────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        days = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
        extra = f"Account-Alter: **{days} Tage**"
        if days < 7:
            extra += "  ⚠️ *Neuer Account!*"
        await self.post(member.guild, "join", member, grund="Dem Server beigetreten", extra=extra)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        extra = "Hatte Rollen: " + (", ".join(roles) if roles else "Keine")
        await self.post(member.guild, "leave", member, grund="Den Server verlassen", extra=extra)

    # ── Rollen-Änderungen loggen (mit Debounce gegen Bulk-Spam) ──────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        added   = [r for r in after.roles  if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if not added and not removed:
            return

        now = _time.monotonic()
        # Max. einmal alle 2 Sekunden pro Mitglied loggen
        if now - self._role_log_cooldown.get(after.id, 0.0) < 2.0:
            return
        self._role_log_cooldown[after.id] = now

        for role in added:
            await self.post(
                after.guild, "role_add", after,
                grund=f"Rolle **{role.name}** erhalten",
                extra=role.mention,
            )
        for role in removed:
            await self.post(
                after.guild, "role_rem", after,
                grund=f"Rolle **{role.name}** entfernt",
                extra=role.mention,
            )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = "❌ Du hast nicht die nötigen Berechtigungen." if isinstance(error, app_commands.MissingPermissions) else f"❌ Fehler: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanLog(bot))
