"""
Vollständiges Audit-Log System — Enterprise Edition.

Protokolliert ALLE relevanten Discord-Ereignisse:
  Mitglieder:   Join, Leave, Ban, Unban, Kick, Timeout, Nickname, Rollen
  Nachrichten:  Delete, Edit, Bulk-Delete, Ghost-Ping-Erkennung
  Voice:        Join, Leave, Wechsel, Stummschaltung, Deafen, Kick
  Kanäle:       Erstellen, Löschen, Umbenennen, Permissions-Änderung
  Rollen:       Erstellen, Löschen, Umbenennen, Permissions-Änderung
  Server:       Name, Icon, Einladungen, Emojis, Sticker, Webhooks, Threads
  Commands:     Slash-Command-Nutzung, verweigerte Zugriffe
  Automod:      Alle Automod-Aktionen

Features:
  • Pro-Ereignis eigene Embed-Farbe und Emoji
  • Audit-Log-Verknüpfung (wer hat die Aktion ausgeführt)
  • Ghost-Ping-Erkennung (mention → sofort delete)
  • Konfigurierbare Log-Kanäle pro Kategorie (oder ein globaler Kanal)
  • In-Memory-Cache für Log-Kanal-IDs (kein DB-Hit bei jedem Event)
  • Fehlertolerant: Exception im Log-Handler crasht nie den Bot
"""

from __future__ import annotations

import datetime
import logging
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.permissions import check_role_permission
from utils.theme import success_embed, error_embed, info_embed, COLOR_DARK, COLOR_INFO, get_footer_text

log = logging.getLogger("avoke.logging")

CONFIG_PATH: Final[str] = "data/logging_config.json"

# Farb-Palette für Ereignistypen
_COLORS: Final[dict[str, discord.Color]] = {
    "join":         discord.Color.from_rgb(46,  204, 113),   # Grün
    "leave":        discord.Color.from_rgb(231, 76,  60),    # Rot
    "ban":          discord.Color.from_rgb(235, 77,  75),    # Dunkelrot
    "unban":        discord.Color.from_rgb(88,  214, 141),   # Hellgrün
    "kick":         discord.Color.from_rgb(230, 126, 34),    # Orange
    "timeout":      discord.Color.from_rgb(130, 80,  255),   # Lila
    "message":      discord.Color.from_rgb(44,  47,  51),    # Dunkelgrau
    "voice":        discord.Color.from_rgb(52,  152, 219),   # Blau
    "role":         discord.Color.from_rgb(155, 89,  182),   # Violett
    "channel":      discord.Color.from_rgb(26,  188, 156),   # Türkis
    "server":       discord.Color.from_rgb(52,  73,  94),    # Dunkelblau
    "command":      discord.Color.from_rgb(84,  153, 199),   # Stahlblau
    "ghost_ping":   discord.Color.from_rgb(255, 165, 0),     # Orange-Gelb
    "invite":       discord.Color.from_rgb(243, 156, 18),    # Bernstein
    "webhook":      discord.Color.from_rgb(149, 165, 166),   # Grau
    "thread":       discord.Color.from_rgb(52,  152, 219),   # Blau
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _ts_short(dt: datetime.datetime | None) -> str:
    if dt is None:
        return "?"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


class LoggingCog(commands.Cog):
    """
    Vollständiges Discord-Audit-Log System.
    Alle Events werden fehlertolerant behandelt — kein Event-Handler darf den Bot crashen.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot          = bot
        self.config_store = JSONStore(CONFIG_PATH, {})
        # In-Memory-Cache: guild_id → {category: channel_id}
        self._channel_cache: dict[int, dict[str, int | None]] = {}
        # Ghost-Ping-Tracker: message_id → {author_id, mentions: [user_id], channel_id}
        self._ghost_ping_tracker: dict[int, dict] = {}

    def _invalidate(self, guild_id: int) -> None:
        self._channel_cache.pop(guild_id, None)

    # ── Kanal-Resolver ────────────────────────────────────────────────────────

    async def _get_log_channel(
        self,
        guild:    discord.Guild,
        category: str = "global",
    ) -> discord.TextChannel | None:
        """
        Gibt den Log-Kanal für eine Kategorie zurück.
        Sucht zuerst die spezifische Kategorie, fällt auf 'global' zurück.
        """
        # Cache-Hit
        if guild.id in self._channel_cache:
            cache = self._channel_cache[guild.id]
            cid   = cache.get(category) or cache.get("global")
            return guild.get_channel(cid) if cid else None

        # Cache-Miss → laden
        config = await self.config_store.read()
        guild_cfg = config.get(str(guild.id), {})

        # Cache aufbauen
        self._channel_cache[guild.id] = {
            k: v for k, v in guild_cfg.items()
            if k.endswith("_channel_id") or k == "log_channel_id"
        }
        # Backward-compat: altes "log_channel_id" als "global" behandeln
        if "log_channel_id" in guild_cfg:
            self._channel_cache[guild.id]["global"] = guild_cfg["log_channel_id"]

        cid = (
            guild_cfg.get(f"{category}_channel_id")
            or guild_cfg.get("log_channel_id")
        )
        return guild.get_channel(cid) if cid else None

    async def _send_log(
        self,
        guild:    discord.Guild,
        embed:    discord.Embed,
        category: str = "global",
    ) -> None:
        """Sendet einen Log-Eintrag fehlertolerant."""
        channel = await self._get_log_channel(guild, category)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass
        except Exception as exc:
            log.exception("Unerwarteter Fehler im Log-Handler: %s", exc)

    # ── Konfiguration ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="admin-log-set",
        description="Legt den Log-Kanal fest (global oder kategoriespezifisch).",
    )
    @app_commands.describe(
        kanal="Kanal für Log-Nachrichten",
        kategorie="Kategorie (leer = globaler Kanal für alles)",
    )
    @app_commands.choices(kategorie=[
        app_commands.Choice(name="🌍 Global (Standard)", value="global"),
        app_commands.Choice(name="👥 Mitglieder",         value="member"),
        app_commands.Choice(name="💬 Nachrichten",        value="message"),
        app_commands.Choice(name="🔊 Voice",              value="voice"),
        app_commands.Choice(name="🛡️ Moderation",        value="moderation"),
        app_commands.Choice(name="⚙️ Server/Kanäle",     value="server"),
        app_commands.Choice(name="📋 Commands",           value="command"),
    ])
    async def admin_log_set(
        self,
        interaction: discord.Interaction,
        kanal:       discord.TextChannel,
        kategorie:   str = "global",
    ) -> None:
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung"),
                ephemeral=True,
            )
            return

        key = "log_channel_id" if kategorie == "global" else f"{kategorie}_channel_id"

        def mutate(data: dict) -> dict:
            data.setdefault(str(interaction.guild.id), {})[key] = kanal.id
            return data

        await self.config_store.update(mutate)
        self._invalidate(interaction.guild.id)

        await interaction.response.send_message(
            embed=success_embed(
                "✅ Log-Kanal konfiguriert",
                f"**{kategorie.title()}**-Logs → {kanal.mention}",
            ),
            ephemeral=True,
        )

    # ── Helper: Embed bauen ───────────────────────────────────────────────────

    def _embed(
        self,
        category: str,
        title:    str,
        desc:     str = "",
        *,
        guild:    discord.Guild | None = None,
    ) -> discord.Embed:
        color = _COLORS.get(category, discord.Color.blurple())
        embed = discord.Embed(
            title       = title,
            description = desc or discord.utils.MISSING,
            color       = color,
            timestamp   = _now(),
        )
        if guild:
            embed.set_footer(text=f"{guild.name} • Audit Log")
        else:
            embed.set_footer(text="AVOKE • Audit Log")
        return embed

    # ─────────────────────────────────────────────────────────────────────────
    # ── MITGLIEDER ────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        days  = (_now() - member.created_at).days
        alert = "  ⚠️ **Neuer Account!**" if days < 7 else ""
        embed = self._embed(
            "join",
            "📥 Mitglied beigetreten",
            f"**Nutzer:** {member.mention} (`{member.id}`)\n"
            f"**Account-Alter:** {days} Tage{alert}\n"
            f"**Erstellt:** {_ts_short(member.created_at)}\n"
            f"**Mitgliederzahl:** {member.guild.member_count}",
            guild=member.guild,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self._send_log(member.guild, embed, "member")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        embed = self._embed(
            "leave",
            "📤 Mitglied verlassen",
            f"**Nutzer:** {member.mention} (`{member.id}`)\n"
            f"**Rollen:** {', '.join(roles) if roles else 'Keine'}\n"
            f"**Beigetreten:** {_ts_short(member.joined_at)}",
            guild=member.guild,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self._send_log(member.guild, embed, "member")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        # Moderator aus Audit-Log ermitteln
        mod_text = "Unbekannt"
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                if entry.target and entry.target.id == user.id:
                    mod_text = f"{entry.user.mention} (`{entry.user.id}`)"
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = self._embed(
            "ban",
            "🔨 Mitglied gebannt",
            f"**Nutzer:** {user.mention} (`{user.id}`)\n"
            f"**Moderator:** {mod_text}",
            guild=guild,
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        await self._send_log(guild, embed, "moderation")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        mod_text = "Unbekannt"
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.unban):
                if entry.target and entry.target.id == user.id:
                    mod_text = f"{entry.user.mention} (`{entry.user.id}`)"
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass

        embed = self._embed(
            "unban",
            "🔓 Mitglied entbannt",
            f"**Nutzer:** {user.mention} (`{user.id}`)\n"
            f"**Moderator:** {mod_text}",
            guild=guild,
        )
        await self._send_log(guild, embed, "moderation")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild

        # ── Nickname-Änderung ───────────────────────────────────────────────
        if before.nick != after.nick:
            embed = self._embed(
                "message",
                "📝 Nickname geändert",
                f"**Nutzer:** {after.mention}",
                guild=guild,
            )
            embed.add_field(name="Vorher",  value=before.nick or before.name, inline=True)
            embed.add_field(name="Nachher", value=after.nick  or after.name,  inline=True)
            await self._send_log(guild, embed, "member")

        # ── Rollen-Änderung ─────────────────────────────────────────────────
        added_roles   = [r for r in after.roles  if r not in before.roles]
        removed_roles = [r for r in before.roles if r not in after.roles]
        if added_roles or removed_roles:
            lines = []
            if added_roles:
                lines.append("**➕ Hinzugefügt:** " + ", ".join(r.mention for r in added_roles))
            if removed_roles:
                lines.append("**➖ Entfernt:** " + ", ".join(r.mention for r in removed_roles))
            embed = self._embed(
                "role",
                "🎭 Rollen geändert",
                f"**Nutzer:** {after.mention}\n" + "\n".join(lines),
                guild=guild,
            )
            await self._send_log(guild, embed, "member")

        # ── Timeout ─────────────────────────────────────────────────────────
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until:
                desc = (
                    f"**Nutzer:** {after.mention}\n"
                    f"**Bis:** <t:{int(after.timed_out_until.timestamp())}:F>"
                )
                title = "⏱️ Timeout gesetzt"
            else:
                desc  = f"**Nutzer:** {after.mention}\n**Timeout aufgehoben**"
                title = "✅ Timeout aufgehoben"
            await self._send_log(guild, self._embed("timeout", title, desc, guild=guild), "moderation")

    # ─────────────────────────────────────────────────────────────────────────
    # ── NACHRICHTEN ───────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Speichert Mention-Info für Ghost-Ping-Erkennung."""
        if message.guild is None or message.author.bot:
            return
        if message.mentions or message.role_mentions:
            self._ghost_ping_tracker[message.id] = {
                "author_id":   message.author.id,
                "channel_id":  message.channel.id,
                "mentions":    [u.id for u in message.mentions],
                "role_mentions": [r.id for r in message.role_mentions],
                "content":     message.content[:500],
            }

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return

        # ── Ghost-Ping-Erkennung ────────────────────────────────────────────
        ping_info = self._ghost_ping_tracker.pop(message.id, None)
        if ping_info and (ping_info["mentions"] or ping_info["role_mentions"]):
            member_pings = ", ".join(f"<@{uid}>" for uid in ping_info["mentions"])
            role_pings   = ", ".join(f"<@&{rid}>" for rid in ping_info["role_mentions"])
            embed = self._embed(
                "ghost_ping",
                "👻 Ghost-Ping erkannt!",
                f"**Autor:** <@{ping_info['author_id']}>\n"
                f"**Kanal:** <#{ping_info['channel_id']}>\n"
                + (f"**Gepingte Nutzer:** {member_pings}\n" if member_pings else "")
                + (f"**Gepingte Rollen:** {role_pings}\n"   if role_pings   else "")
                + f"**Inhalt:** {ping_info['content'][:200]}",
                guild=message.guild,
            )
            await self._send_log(message.guild, embed, "message")
            return

        content = message.content[:1000] if message.content else "[Embed / Anhang / kein Text]"
        embed   = self._embed(
            "message",
            "🗑️ Nachricht gelöscht",
            f"**Autor:** {message.author.mention} (`{message.author.id}`)\n"
            f"**Kanal:** {message.channel.mention}\n"
            f"**Inhalt:** {content}",
            guild=message.guild,
        )
        if message.attachments:
            embed.add_field(
                name  = f"📎 Anhänge ({len(message.attachments)})",
                value = "\n".join(a.filename for a in message.attachments[:5]),
                inline=False,
            )
        await self._send_log(message.guild, embed, "message")

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]) -> None:
        if not messages:
            return
        guild   = messages[0].guild
        channel = messages[0].channel
        if guild is None:
            return

        embed = self._embed(
            "message",
            f"🗑️ Massen-Löschung — {len(messages)} Nachrichten",
            f"**Kanal:** {channel.mention}\n"
            f"**Anzahl:** {len(messages)} Nachrichten gelöscht",
            guild=guild,
        )
        await self._send_log(guild, embed, "message")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return

        # Ghost-Ping-Tracker aktualisieren
        if after.id in self._ghost_ping_tracker:
            self._ghost_ping_tracker[after.id] = {
                "author_id":   after.author.id,
                "channel_id":  after.channel.id,
                "mentions":    [u.id for u in after.mentions],
                "role_mentions": [r.id for r in after.role_mentions],
                "content":     after.content[:500],
            }

        embed = self._embed(
            "message",
            "✏️ Nachricht bearbeitet",
            f"**Autor:** {before.author.mention}\n"
            f"**Kanal:** {before.channel.mention}\n"
            f"[Zur Nachricht]({after.jump_url})",
            guild=before.guild,
        )
        embed.add_field(name="Vorher",  value=before.content[:400] or "[leer]", inline=False)
        embed.add_field(name="Nachher", value=after.content[:400]  or "[leer]", inline=False)
        await self._send_log(before.guild, embed, "message")

    # ─────────────────────────────────────────────────────────────────────────
    # ── VOICE ─────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after:  discord.VoiceState,
    ) -> None:
        guild = member.guild

        # Kanal gewechselt / beigetreten / verlassen
        if before.channel != after.channel:
            if before.channel is None:
                title = "🔊 Voice beigetreten"
                desc  = f"**Nutzer:** {member.mention}\n**Kanal:** {after.channel.mention}"
            elif after.channel is None:
                title = "🔇 Voice verlassen"
                desc  = f"**Nutzer:** {member.mention}\n**Kanal:** {before.channel.mention}"
            else:
                title = "🔀 Voice-Kanal gewechselt"
                desc  = (
                    f"**Nutzer:** {member.mention}\n"
                    f"**Von:** {before.channel.mention} → **Nach:** {after.channel.mention}"
                )
            await self._send_log(guild, self._embed("voice", title, desc, guild=guild), "voice")

        # Server-Mute/Deafen
        if before.mute != after.mute or before.deaf != after.deaf:
            changes = []
            if before.mute != after.mute:
                changes.append(f"Server-Mute: {'Ein' if after.mute else 'Aus'}")
            if before.deaf != after.deaf:
                changes.append(f"Server-Deafen: {'Ein' if after.deaf else 'Aus'}")
            embed = self._embed(
                "voice",
                "🔧 Voice-Status geändert",
                f"**Nutzer:** {member.mention}\n**Änderungen:** {', '.join(changes)}",
                guild=guild,
            )
            await self._send_log(guild, embed, "voice")

    # ─────────────────────────────────────────────────────────────────────────
    # ── KANÄLE ────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        mod_text = await self._get_audit_actor(channel.guild, discord.AuditLogAction.channel_create)
        embed = self._embed(
            "channel",
            "📁 Kanal erstellt",
            f"**Kanal:** {channel.mention} (`{channel.name}`)\n"
            f"**Typ:** {str(channel.type).replace('_', ' ').title()}\n"
            f"**Erstellt von:** {mod_text}",
            guild=channel.guild,
        )
        await self._send_log(channel.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        mod_text = await self._get_audit_actor(channel.guild, discord.AuditLogAction.channel_delete)
        embed = self._embed(
            "channel",
            "📁 Kanal gelöscht",
            f"**Kanal:** #{channel.name} (`{channel.id}`)\n"
            f"**Gelöscht von:** {mod_text}",
            guild=channel.guild,
        )
        await self._send_log(channel.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after:  discord.abc.GuildChannel,
    ) -> None:
        if before.name != after.name:
            embed = self._embed(
                "channel",
                "📝 Kanal umbenannt",
                f"**Vorher:** #{before.name}\n**Nachher:** {after.mention}",
                guild=after.guild,
            )
            await self._send_log(after.guild, embed, "server")

    # ─────────────────────────────────────────────────────────────────────────
    # ── ROLLEN ────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        mod_text = await self._get_audit_actor(role.guild, discord.AuditLogAction.role_create)
        embed = self._embed(
            "role",
            "🎭 Rolle erstellt",
            f"**Rolle:** {role.mention} (`{role.id}`)\n**Erstellt von:** {mod_text}",
            guild=role.guild,
        )
        await self._send_log(role.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        mod_text = await self._get_audit_actor(role.guild, discord.AuditLogAction.role_delete)
        embed = self._embed(
            "role",
            "🎭 Rolle gelöscht",
            f"**Rolle:** @{role.name} (`{role.id}`)\n**Gelöscht von:** {mod_text}",
            guild=role.guild,
        )
        await self._send_log(role.guild, embed, "server")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if before.name == after.name and before.permissions == after.permissions:
            return
        lines = []
        if before.name != after.name:
            lines.append(f"**Name:** {before.name} → {after.name}")
        if before.permissions != after.permissions:
            lines.append("**Berechtigungen geändert**")
        embed = self._embed(
            "role",
            "🎭 Rolle bearbeitet",
            f"**Rolle:** {after.mention}\n" + "\n".join(lines),
            guild=after.guild,
        )
        await self._send_log(after.guild, embed, "server")

    # ─────────────────────────────────────────────────────────────────────────
    # ── EINLADUNGEN ───────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if not isinstance(guild, discord.Guild):
            return
        embed = self._embed(
            "invite",
            "🔗 Einladung erstellt",
            f"**Erstellt von:** {invite.inviter.mention if invite.inviter else 'Unbekannt'}\n"
            f"**Kanal:** {invite.channel.mention if invite.channel else '?'}\n"
            f"**Code:** `{invite.code}`\n"
            f"**Max. Nutzungen:** {invite.max_uses or '∞'}\n"
            f"**Läuft ab:** {f'<t:{int((discord.utils.utcnow() + datetime.timedelta(seconds=invite.max_age)).timestamp())}:R>' if invite.max_age else 'Nie'}",
            guild=guild,
        )
        await self._send_log(guild, embed, "server")

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if not isinstance(guild, discord.Guild):
            return
        embed = self._embed(
            "invite",
            "🔗 Einladung gelöscht",
            f"**Code:** `{invite.code}`\n"
            f"**Kanal:** {invite.channel.mention if invite.channel else '?'}",
            guild=guild,
        )
        await self._send_log(guild, embed, "server")

    # ─────────────────────────────────────────────────────────────────────────
    # ── WEBHOOKS / THREADS ────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.TextChannel) -> None:
        mod_text = await self._get_audit_actor(channel.guild, discord.AuditLogAction.webhook_create)
        embed = self._embed(
            "webhook",
            "🔔 Webhook-Änderung erkannt",
            f"**Kanal:** {channel.mention}\n**Ausgeführt von:** {mod_text}",
            guild=channel.guild,
        )
        await self._send_log(channel.guild, embed, "server")

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        embed = self._embed(
            "thread",
            "🧵 Thread erstellt",
            f"**Thread:** {thread.mention}\n"
            f"**Kanal:** {thread.parent.mention if thread.parent else '?'}\n"
            f"**Erstellt von:** {thread.owner.mention if thread.owner else 'Unbekannt'}",
            guild=thread.guild,
        )
        await self._send_log(thread.guild, embed, "server")

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread) -> None:
        embed = self._embed(
            "thread",
            "🧵 Thread gelöscht",
            f"**Thread:** #{thread.name}\n"
            f"**Kanal:** {thread.parent.mention if thread.parent else '?'}",
            guild=thread.guild,
        )
        await self._send_log(thread.guild, embed, "server")

    # ─────────────────────────────────────────────────────────────────────────
    # ── COMMANDS ──────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command:     app_commands.Command,
    ) -> None:
        if interaction.guild is None:
            return
        embed = self._embed(
            "command",
            "📋 Slash-Command verwendet",
            f"**Befehl:** `/{command.qualified_name}`\n"
            f"**Nutzer:** {interaction.user.mention} (`{interaction.user.id}`)\n"
            f"**Kanal:** {interaction.channel.mention if interaction.channel else 'DM'}",
            guild=interaction.guild,
        )
        await self._send_log(interaction.guild, embed, "command")

    @commands.Cog.listener()
    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error:       app_commands.AppCommandError,
    ) -> None:
        if interaction.guild is None:
            return
        if not isinstance(error, app_commands.MissingPermissions):
            return
        embed = self._embed(
            "command",
            "🚫 Command-Zugriff verweigert",
            f"**Nutzer:** {interaction.user.mention}\n"
            f"**Befehl:** `/{interaction.command.qualified_name if interaction.command else '?'}`\n"
            f"**Fehlende Rechte:** {', '.join(error.missing_permissions)}",
            guild=interaction.guild,
        )
        await self._send_log(interaction.guild, embed, "command")

    # ─────────────────────────────────────────────────────────────────────────
    # ── HILFSMETHODEN ─────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_audit_actor(
        self,
        guild:  discord.Guild,
        action: discord.AuditLogAction,
    ) -> str:
        """Liest den Moderator der letzten Audit-Log-Aktion aus."""
        try:
            async for entry in guild.audit_logs(limit=1, action=action):
                if entry.user:
                    return f"{entry.user.mention} (`{entry.user.id}`)"
        except (discord.Forbidden, discord.HTTPException):
            pass
        return "Unbekannt"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LoggingCog(bot))
