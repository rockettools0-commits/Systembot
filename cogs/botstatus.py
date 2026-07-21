"""
Bot-Status & Streamer-Ankündigungs-System (Komplett-Überarbeitung).

/bot-status        — Bot-Presence live ändern (Typ + Text).
/stream-setup      — Ankündigungskanal + Ping-Rolle konfigurieren.
/stream-add        — Mitglied zur Streamer-Whitelist hinzufügen.
/stream-remove     — Mitglied von der Whitelist entfernen.
/stream-list       — Alle whitegelisteten Streamer anzeigen + letztes Live.
/stream-all        — Alle Mitglieder dürfen angekündigt werden (kein Whitelist-Modus).

Merkmale:
  • Whitelist-Modus (nur bestimmte Mitglieder) ODER Alle-Modus
  • Pro Session nur ein Ping pro Streamer (kein Spam)
  • Letzte Live-Zeit wird in JSON gespeichert
  • Stream-Titel, Spiel und Twitch-URL werden im Embed angezeigt
  • Eigene Embed-Farbe: Twitch-Lila für Go-Live-Embeds
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, warning_embed, FOOTER_TEXT, get_footer_text
from utils.permissions import check_role_permission

STREAMER_CONFIG_PATH = "data/streamer_config.json"
STREAMER_STATE_PATH  = "data/streamer_state.json"

# Twitch-Lila
COLOR_LIVE = discord.Color.from_rgb(100, 65, 165)

ACTIVITY_TYPES = {
    "playing":   discord.ActivityType.playing,
    "watching":  discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}


def default_streamer_config() -> dict:
    """
    Schema:
    {
      "guild_id": {
        "channel_id": int,
        "role_id": int,
        "all_members": bool,          # True = jeder wird angekündigt
        "whitelist": [user_id, ...]   # nur relevant wenn all_members=False
      }
    }
    """
    return {}


def default_streamer_state() -> dict:
    """
    Schema:
    {
      "guild_id": {
        "user_id": {
          "pinged": bool,             # True = wurde diese Session bereits gepingt
          "last_live": "ISO-string"   # letzte Go-Live-Zeit
        }
      }
    }
    """
    return {}


class BotStatus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self.config = JSONStore(STREAMER_CONFIG_PATH, default_streamer_config())
        self.state  = JSONStore(STREAMER_STATE_PATH,  default_streamer_state())

    stream = app_commands.Group(name="stream", description="Streamer-Ankündigungs-System.")

    # ─────────────────────────────────────────────────────────────────────────
    # /bot-status  (bleibt flat — eigenständige Funktion)
    # ─────────────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="bot-status",
        description="Ändert den Bot-Status (Aktivitätstyp + Text).",
    )
    @app_commands.describe(
        typ="Aktivitätstyp: playing / watching / listening / competing",
        text="Text der im Status angezeigt wird",
    )
    @app_commands.choices(typ=[
        app_commands.Choice(name="🎮 Playing",   value="playing"),
        app_commands.Choice(name="📺 Watching",  value="watching"),
        app_commands.Choice(name="🎧 Listening", value="listening"),
        app_commands.Choice(name="🏆 Competing", value="competing"),
    ])
    async def change_status(
        self,
        interaction: discord.Interaction,
        typ: str,
        text: str,
    ):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        activity_type = ACTIVITY_TYPES.get(typ, discord.ActivityType.watching)
        await self.bot.change_presence(activity=discord.Activity(type=activity_type, name=text))

        type_labels = {
            "playing":   "🎮 Playing",
            "watching":  "📺 Watching",
            "listening": "🎧 Listening",
            "competing": "🏆 Competing",
        }
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Bot-Status geändert",
                f"**Typ:** {type_labels[typ]}\n**Text:** {text}",
            ),
            ephemeral=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /stream setup
    # ─────────────────────────────────────────────────────────────────────────

    @stream.command(
        name="setup",
        description="Konfiguriert Ankündigungskanal und Ping-Rolle für Go-Live-Alerts.",
    )
    @app_commands.describe(
        kanal="Kanal für die Live-Ankündigung",
        rolle="Rolle, die bei Go-Live gepingt wird",
    )
    async def stream_setup(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel,
        rolle: discord.Role,
    ):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        def mutate(data):
            existing = data.get(str(interaction.guild.id), {})
            data[str(interaction.guild.id)] = {
                "channel_id":  kanal.id,
                "role_id":     rolle.id,
                "all_members": existing.get("all_members", False),
                "whitelist":   existing.get("whitelist", []),
            }
            return data

        await self.config.update(mutate)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Stream-Ping konfiguriert",
                f"**Ankündigungskanal:** {kanal.mention}\n"
                f"**Ping-Rolle:** {rolle.mention}\n\n"
                f"Nutze `/stream add` um Mitglieder zur Whitelist hinzuzufügen,\n"
                f"oder `/stream all` damit alle Mitglieder angekündigt werden.",
            ),
            ephemeral=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /stream all  — Whitelist-Modus umschalten
    # ─────────────────────────────────────────────────────────────────────────

    @stream.command(
        name="all",
        description="Legt fest ob alle Mitglieder oder nur Gewhitelistete angekündigt werden.",
    )
    @app_commands.describe(aktiv="True = alle Mitglieder, False = nur Whitelist")
    async def stream_all(self, interaction: discord.Interaction, aktiv: bool):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        cfg_data = await self.config.read()
        if guild_id not in cfg_data:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht konfiguriert",
                                  "Bitte zuerst `/stream-setup` ausführen."),
                ephemeral=True,
            )
            return

        def mutate(data):
            data[guild_id]["all_members"] = aktiv
            return data

        await self.config.update(mutate)
        mode = "**Alle Mitglieder**" if aktiv else "**Nur Whitelist**"
        await interaction.response.send_message(
            embed=success_embed("✅ Stream-Modus geändert", f"Modus: {mode}"),
            ephemeral=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /stream add
    # ─────────────────────────────────────────────────────────────────────────

    @stream.command(
        name="add",
        description="Fügt ein Mitglied zur Streamer-Whitelist hinzu.",
    )
    @app_commands.describe(mitglied="Das Mitglied, das Go-Live-Alerts erhalten soll")
    async def stream_add(self, interaction: discord.Interaction, mitglied: discord.Member):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        cfg_data = await self.config.read()
        if guild_id not in cfg_data:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht konfiguriert",
                                  "Bitte zuerst `/stream-setup` ausführen."),
                ephemeral=True,
            )
            return

        result: dict = {}

        def mutate(data):
            wl = data[guild_id].setdefault("whitelist", [])
            if mitglied.id in wl:
                result["already"] = True
            else:
                wl.append(mitglied.id)
                result["added"] = True
            return data

        await self.config.update(mutate)

        if result.get("already"):
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Bereits in der Liste",
                                 f"{mitglied.mention} ist bereits auf der Whitelist."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=success_embed("✅ Streamer hinzugefügt",
                                    f"{mitglied.mention} wird ab jetzt bei Go-Live angekündigt."),
                ephemeral=True,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # /stream remove
    # ─────────────────────────────────────────────────────────────────────────

    @stream.command(
        name="remove",
        description="Entfernt ein Mitglied von der Streamer-Whitelist.",
    )
    @app_commands.describe(mitglied="Das Mitglied, das entfernt werden soll")
    async def stream_remove(self, interaction: discord.Interaction, mitglied: discord.Member):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        result: dict = {}

        def mutate(data):
            wl = data.get(guild_id, {}).get("whitelist", [])
            if mitglied.id in wl:
                wl.remove(mitglied.id)
                data[guild_id]["whitelist"] = wl
                result["removed"] = True
            else:
                result["not_found"] = True
            return data

        await self.config.update(mutate)

        if result.get("not_found"):
            await interaction.response.send_message(
                embed=warning_embed("⚠️ Nicht gefunden",
                                    f"{mitglied.mention} war nicht auf der Whitelist."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=success_embed("✅ Streamer entfernt",
                                    f"{mitglied.mention} wird nicht mehr angekündigt."),
                ephemeral=True,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # /stream list
    # ─────────────────────────────────────────────────────────────────────────

    @stream.command(
        name="list",
        description="Zeigt die Streamer-Konfiguration und alle gewhitelisteten Mitglieder.",
    )
    async def stream_list(self, interaction: discord.Interaction):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        cfg_data   = await self.config.read()
        state_data = await self.state.read()
        guild_conf = cfg_data.get(guild_id)

        if not guild_conf:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht konfiguriert",
                                  "Bitte zuerst `/stream-setup` ausführen."),
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(guild_conf["channel_id"])
        role    = interaction.guild.get_role(guild_conf["role_id"])
        mode    = "Alle Mitglieder" if guild_conf.get("all_members") else "Nur Whitelist"

        embed = discord.Embed(
            title="📡 Streamer-Konfiguration",
            color=COLOR_LIVE,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="📢 Kanal", value=channel.mention if channel else "⚠️ Nicht gefunden", inline=True)
        embed.add_field(name="🔔 Rolle",  value=role.mention if role else "⚠️ Nicht gefunden",       inline=True)
        embed.add_field(name="👥 Modus",  value=mode,                                                 inline=True)

        whitelist  = guild_conf.get("whitelist", [])
        guild_state = state_data.get(guild_id, {})

        if not guild_conf.get("all_members"):
            if whitelist:
                lines = []
                for uid in whitelist:
                    member = interaction.guild.get_member(uid)
                    name   = member.mention if member else f"Unbekannt (`{uid}`)"
                    ustate = guild_state.get(str(uid), {})
                    last   = ustate.get("last_live")
                    last_str = f"<t:{int(datetime.datetime.fromisoformat(last).timestamp())}:R>" if last else "Noch nie"
                    lines.append(f"{name} — Zuletzt live: {last_str}")
                embed.add_field(
                    name=f"🎮 Whitelist ({len(whitelist)})",
                    value="\n".join(lines),
                    inline=False,
                )
            else:
                embed.add_field(name="🎮 Whitelist", value="Leer — nutze `/stream-add`", inline=False)
        else:
            embed.add_field(
                name="🎮 Zuletzt live",
                value="Im Alle-Modus wird keine Whitelist angezeigt.",
                inline=False,
            )

        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Presence-Listener — erkennt Go-Live
    # ─────────────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if guild is None:
            return

        was_streaming = any(isinstance(a, discord.Streaming) for a in before.activities)
        is_streaming  = any(isinstance(a, discord.Streaming) for a in after.activities)

        # Neuer Stream → ankündigen
        if is_streaming and not was_streaming:
            await self._handle_go_live(after)

        # Stream beendet → Session-State zurücksetzen (nächstes Mal wieder pingen)
        if was_streaming and not is_streaming:
            guild_id = str(guild.id)
            user_id  = str(after.id)

            def clear(data):
                user_state = data.setdefault(guild_id, {}).setdefault(user_id, {})
                user_state["pinged"] = False
                return data

            await self.state.update(clear)

    async def _handle_go_live(self, member: discord.Member):
        guild    = member.guild
        guild_id = str(guild.id)
        user_id  = str(member.id)

        # Konfiguration laden
        cfg_data   = await self.config.read()
        guild_conf = cfg_data.get(guild_id)
        if not guild_conf:
            return

        # Whitelist-Prüfung (wenn all_members=False)
        if not guild_conf.get("all_members", False):
            if member.id not in guild_conf.get("whitelist", []):
                return

        # Anti-Spam: wurde schon gepingt diese Session?
        state_data  = await self.state.read()
        user_state  = state_data.get(guild_id, {}).get(user_id, {})
        if user_state.get("pinged"):
            return

        # Session-State sofort setzen + last_live speichern
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def mark(data):
            us = data.setdefault(guild_id, {}).setdefault(user_id, {})
            us["pinged"]    = True
            us["last_live"] = now_iso
            return data

        await self.state.update(mark)

        channel = guild.get_channel(guild_conf["channel_id"])
        role    = guild.get_role(guild_conf["role_id"])
        if channel is None or role is None:
            return

        # Stream-Aktivität auslesen
        stream_activity: discord.Streaming | None = next(
            (a for a in member.activities if isinstance(a, discord.Streaming)), None
        )
        stream_title = stream_activity.name if stream_activity and stream_activity.name else "Kein Titel"
        stream_url   = stream_activity.url  if stream_activity and stream_activity.url  else None
        game         = stream_activity.game if stream_activity and stream_activity.game else "Unbekannt"

        embed = discord.Embed(
            title=f"🔴  {member.display_name} ist jetzt LIVE!",
            description=f"**{stream_title}**",
            color=COLOR_LIVE,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        if member.guild.icon:
            embed.set_author(name=member.guild.name, icon_url=member.guild.icon.url)
        embed.add_field(name="🎮 Spiel",    value=f"`{game}`",      inline=True)
        embed.add_field(name="👤 Streamer", value=member.mention,   inline=True)
        if stream_url:
            embed.add_field(name="\u200b",  value="\u200b",         inline=True)
            embed.add_field(name="🔗 Link", value=f"[**Jetzt zuschauen →**]({stream_url})", inline=False)
        embed.set_footer(text=get_footer_text(member.guild))

        try:
            await channel.send(
                content=role.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except discord.HTTPException:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Error-Handler
    # ─────────────────────────────────────────────────────────────────────────

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        msg = error_embed("❌ Fehler", str(error))
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BotStatus(bot))
