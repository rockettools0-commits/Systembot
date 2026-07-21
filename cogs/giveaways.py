"""
Giveaways — /giveaway Subcommand-Gruppe (zählt als 1 Command).

  /giveaway start   — Startet ein neues Giveaway
  /giveaway end     — Beendet ein Giveaway sofort
  /giveaway reroll  — Zieht einen neuen Gewinner
"""

from __future__ import annotations

import datetime
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.storage import JSONStore
from utils.theme import (
    success_embed, error_embed, info_embed,
    FOOTER_TEXT, COLOR_GOLD, COLOR_INFO, COLOR_DARK, get_footer_text,
)
from utils.permissions import check_role_permission

GIVEAWAY_PATH = "data/giveaways.json"

# ── Embed-Baustein ────────────────────────────────────────────────────────────

def _build_active_embed(
    preis: str,
    gewinner_anzahl: int,
    end_time: datetime.datetime,
    host_id: int,
    teilnehmer_count: int,
    guild=None,
) -> discord.Embed:
    """Erstellt das laufende Giveaway-Embed."""
    embed = discord.Embed(
        title="🎉  Giveaway",
        description=(
            f"### {preis}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Drücke **🎉 Teilnehmen** um mitzumachen!"
        ),
        color=COLOR_GOLD,
        timestamp=end_time,
    )
    embed.add_field(
        name="🏆 Preis",
        value=f"```{preis}```",
        inline=True,
    )
    embed.add_field(
        name="🎯 Gewinner",
        value=f"```{gewinner_anzahl}```",
        inline=True,
    )
    embed.add_field(
        name="👥 Teilnehmer",
        value=f"```{teilnehmer_count}```",
        inline=True,
    )
    embed.add_field(
        name="⏰ Endet",
        value=f"<t:{int(end_time.timestamp())}:R>  ·  <t:{int(end_time.timestamp())}:f>",
        inline=False,
    )
    embed.set_footer(text=f"Gestartet von UserID {host_id}  ·  {get_footer_text(guild)}  ·  Endet")
    return embed


def _build_ended_embed(
    preis: str,
    gewinner_mentions: str | None,
    teilnehmer_count: int,
    guild=None,
) -> discord.Embed:
    """Erstellt das beendete Giveaway-Embed (ersetzt das Original)."""
    if gewinner_mentions:
        color       = COLOR_GOLD
        title       = "🎊  Giveaway beendet"
        winner_text = gewinner_mentions
    else:
        color       = COLOR_INFO
        title       = "😔  Giveaway beendet"
        winner_text = "*Niemand hat teilgenommen.*"

    embed = discord.Embed(
        title=title,
        description=(
            f"### {preis}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.add_field(name="🏆 Preis",        value=f"```{preis}```",        inline=True)
    embed.add_field(name="👥 Teilnehmer",   value=f"```{teilnehmer_count}```", inline=True)
    embed.add_field(name="\u200b",          value="\u200b",               inline=True)
    embed.add_field(name="🎯 Gewinner",     value=winner_text,            inline=False)
    embed.set_footer(text=f"Beendet  ·  {get_footer_text(guild)}")
    return embed


# ── View ─────────────────────────────────────────────────────────────────────

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.join_button.custom_id = f"giveaway_join:{giveaway_id}"

    @discord.ui.button(label="🎉 Teilnehmen", style=discord.ButtonStyle.blurple)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Giveaways | None = interaction.client.get_cog("Giveaways")
        if cog is None:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehler", "Giveaway-System nicht verfügbar."),
                ephemeral=True,
            )
            return
        await cog.handle_join(interaction, self.giveaway_id)


class GiveawayEndedView(discord.ui.View):
    """Leere, deaktivierte View die den beendeten Button anzeigt."""
    def __init__(self):
        super().__init__(timeout=None)
        btn = discord.ui.Button(
            label="🎉 Giveaway beendet",
            style=discord.ButtonStyle.grey,
            disabled=True,
        )
        self.add_item(btn)


# ── Cog ──────────────────────────────────────────────────────────────────────

class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self.store = JSONStore(GIVEAWAY_PATH, {})
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    async def cog_load(self):
        data = await self.store.read()
        for gid, info in data.items():
            if not info.get("ended", False):
                self.bot.add_view(GiveawayView(gid))

    giveaway = app_commands.Group(name="giveaway", description="Giveaway-System.")

    # ── /giveaway start ───────────────────────────────────────────────────────

    @giveaway.command(name="start", description="Startet ein neues Giveaway.")
    @app_commands.describe(
        preis="Was wird verlost?",
        dauer_stunden="Dauer des Giveaways in Stunden (z. B. 1.5 für 1,5 Stunden)",
        gewinner_anzahl="Anzahl der Gewinner (Standard: 1)",
    )
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        preis: str,
        dauer_stunden: float,
        gewinner_anzahl: int = 1,
    ):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        if dauer_stunden <= 0:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültige Dauer",
                                  "Die Dauer muss größer als 0 Stunden sein."),
                ephemeral=True,
            )
            return

        if gewinner_anzahl <= 0:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültige Anzahl",
                                  "Es muss mindestens 1 Gewinner geben."),
                ephemeral=True,
            )
            return

        end_time = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=dauer_stunden)
        )

        embed = _build_active_embed(
            preis=preis,
            gewinner_anzahl=gewinner_anzahl,
            end_time=end_time,
            host_id=interaction.user.id,
            teilnehmer_count=0,
            guild=interaction.guild,
        )

        await interaction.response.send_message(
            embed=success_embed("✅ Giveaway wird gestartet…"), ephemeral=True
        )

        message = await interaction.channel.send(embed=embed)
        giveaway_id = str(message.id)
        view        = GiveawayView(giveaway_id)
        await message.edit(view=view)
        self.bot.add_view(view)

        def mutate(data: dict) -> dict:
            data[giveaway_id] = {
                "channel_id":      interaction.channel.id,
                "preis":           preis,
                "end_time":        end_time.isoformat(),
                "gewinner_anzahl": gewinner_anzahl,
                "teilnehmer":      [],
                "ended":           False,
                "host_id":         interaction.user.id,
            }
            return data

        await self.store.update(mutate)

    # ── Button-Handler ────────────────────────────────────────────────────────

    async def handle_join(self, interaction: discord.Interaction, giveaway_id: str):
        data     = await self.store.read()
        giveaway = data.get(giveaway_id)

        if giveaway is None or giveaway.get("ended"):
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht aktiv",
                                  "Dieses Giveaway ist nicht mehr aktiv."),
                ephemeral=True,
            )
            return

        if interaction.user.id in giveaway["teilnehmer"]:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Bereits eingetragen",
                                 "Du nimmst bereits an diesem Giveaway teil!"),
                ephemeral=True,
            )
            return

        # Teilnehmer eintragen
        def mutate(data: dict) -> dict:
            data[giveaway_id]["teilnehmer"].append(interaction.user.id)
            return data

        await self.store.update(mutate)

        # Embed mit aktueller Teilnehmerzahl aktualisieren
        updated_data = await self.store.read()
        updated      = updated_data.get(giveaway_id, {})
        new_count    = len(updated.get("teilnehmer", []))

        try:
            end_time = datetime.datetime.fromisoformat(updated["end_time"])
            new_embed = _build_active_embed(
                preis=updated["preis"],
                gewinner_anzahl=updated["gewinner_anzahl"],
                end_time=end_time,
                host_id=updated["host_id"],
                teilnehmer_count=new_count,
                guild=interaction.guild,
            )
            message = await interaction.channel.fetch_message(int(giveaway_id))
            await message.edit(embed=new_embed)
        except (discord.HTTPException, KeyError, ValueError):
            pass  # Embed-Update ist nicht kritisch

        await interaction.response.send_message(
            embed=success_embed(
                "✅ Eingetragen!",
                f"Du nimmst jetzt am Giveaway teil!\n"
                f"👥 Aktuell **{new_count}** Teilnehmer.",
            ),
            ephemeral=True,
        )

    # ── Task-Loop ─────────────────────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        data = await self.store.read()
        now  = datetime.datetime.now(datetime.timezone.utc)
        for giveaway_id, giveaway in list(data.items()):
            if giveaway.get("ended"):
                continue
            try:
                end_time = datetime.datetime.fromisoformat(giveaway["end_time"])
            except (ValueError, KeyError):
                continue
            if now >= end_time:
                await self._end_giveaway(giveaway_id, giveaway)

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        await self.bot.wait_until_ready()

    async def _end_giveaway(self, giveaway_id: str, giveaway: dict):
        channel    = self.bot.get_channel(giveaway["channel_id"])
        teilnehmer = giveaway.get("teilnehmer", [])

        # Als beendet markieren
        def mutate(data: dict) -> dict:
            if giveaway_id in data:
                data[giveaway_id]["ended"] = True
            return data

        await self.store.update(mutate)

        if channel is None:
            return

        # Gewinner auslosen
        if teilnehmer:
            anzahl   = min(giveaway["gewinner_anzahl"], len(teilnehmer))
            gewinner = random.sample(teilnehmer, anzahl)
            mentions = ", ".join(f"<@{uid}>" for uid in gewinner)
        else:
            mentions = None

        end_embed = _build_ended_embed(
            preis=giveaway["preis"],
            gewinner_mentions=mentions,
            teilnehmer_count=len(teilnehmer),
            guild=channel.guild if channel else None,
        )
        ended_view = GiveawayEndedView()

        try:
            # Originale Nachricht bearbeiten statt neue senden
            message = await channel.fetch_message(int(giveaway_id))
            await message.edit(embed=end_embed, view=ended_view)
        except (discord.NotFound, discord.HTTPException):
            # Fallback: neue Nachricht
            try:
                await channel.send(embed=end_embed)
            except discord.HTTPException:
                return

        # Gewinner anpingen
        if mentions:
            try:
                await channel.send(
                    content=f"🎊 Glückwunsch {mentions}! Du hast **{giveaway['preis']}** gewonnen!",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass

    # ── /giveaway end ─────────────────────────────────────────────────────────

    @giveaway.command(name="end", description="Beendet ein Giveaway sofort.")
    @app_commands.describe(nachrichten_id="Die Nachrichten-ID des Giveaway-Embeds")
    async def giveaway_end(self, interaction: discord.Interaction, nachrichten_id: str):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        try:
            giveaway_id = str(int(nachrichten_id))
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültige ID",
                                  "Bitte gib eine gültige Nachrichten-ID an."),
                ephemeral=True,
            )
            return

        data     = await self.store.read()
        giveaway = data.get(giveaway_id)

        if giveaway is None:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht gefunden",
                                  "Kein Giveaway mit dieser ID gefunden."),
                ephemeral=True,
            )
            return

        if giveaway.get("ended"):
            await interaction.response.send_message(
                embed=error_embed("❌ Bereits beendet",
                                  "Dieses Giveaway ist bereits beendet."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self._end_giveaway(giveaway_id, giveaway)
        await interaction.followup.send(
            embed=success_embed("✅ Giveaway beendet",
                                "Das Giveaway wurde sofort beendet."),
            ephemeral=True,
        )

    # ── /giveaway reroll ──────────────────────────────────────────────────────

    @giveaway.command(name="reroll", description="Zieht einen neuen Gewinner für ein beendetes Giveaway.")
    @app_commands.describe(nachrichten_id="Die Nachrichten-ID des beendeten Giveaway-Embeds")
    async def giveaway_reroll(self, interaction: discord.Interaction, nachrichten_id: str):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        try:
            giveaway_id = str(int(nachrichten_id))
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültige ID",
                                  "Bitte gib eine gültige Nachrichten-ID an."),
                ephemeral=True,
            )
            return

        data     = await self.store.read()
        giveaway = data.get(giveaway_id)

        if giveaway is None:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht gefunden",
                                  "Kein Giveaway mit dieser ID gefunden."),
                ephemeral=True,
            )
            return

        if not giveaway.get("ended"):
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ Noch aktiv",
                    "Dieses Giveaway läuft noch. Beende es zuerst mit `/giveaway-end`.",
                ),
                ephemeral=True,
            )
            return

        teilnehmer = giveaway.get("teilnehmer", [])
        if not teilnehmer:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Teilnehmer",
                                  "Es gab keine Teilnehmer an diesem Giveaway."),
                ephemeral=True,
            )
            return

        channel = self.bot.get_channel(giveaway["channel_id"])
        if channel is None:
            await interaction.response.send_message(
                embed=error_embed("❌ Kanal nicht gefunden",
                                  "Der ursprüngliche Kanal existiert nicht mehr."),
                ephemeral=True,
            )
            return

        gewinner_id = random.choice(teilnehmer)
        mention     = f"<@{gewinner_id}>"

        try:
            await channel.send(
                content=f"🎊 Glückwunsch {mention}! Du hast **{giveaway['preis']}** gewonnen! *(Re-Roll)*",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:
            pass

        await interaction.response.send_message(
            embed=success_embed("✅ Re-Roll durchgeführt", f"Neuer Gewinner: {mention}"),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))
