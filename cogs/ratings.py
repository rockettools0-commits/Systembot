"""
Ticketbewertungs-System für AVOKE.

Ablauf:
  1. Ein Ticket wird geschlossen (cogs/tickets.py → handle_ticket_close).
  2. Der Ticket-Ersteller bekommt eine DM mit einem Sterne-Auswahlmenü (1–5).
  3. Nach der Sternewahl öffnet sich ein Modal für optionales Textfeedback.
  4. Die Bewertung wird gespeichert (Ticket-ID, User, Supporter, Datum) und
     automatisch in den konfigurierten Bewertungs-Log-Kanal gesendet.

Command:
  !ratingstats — zeigt Durchschnitt, Anzahl und Sterne-Verteilung an.

Persistenz:
  data/ticket_ratings.json   — Liste aller abgegebenen Bewertungen.
  data/ratings_pending.json  — offene Bewertungsanfragen (überlebt Neustarts,
                                damit die persistente View weiterhin reagiert).
"""

from __future__ import annotations

import datetime
import time

import discord
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, gold_embed, FOOTER_TEXT, COLOR_GOLD, get_footer_text
from utils.permissions import check_role_permission

RATINGS_PATH = "data/ticket_ratings.json"
PENDING_PATH = "data/ratings_pending.json"

STAR_LABELS = {
    1: "⭐ 1 — Sehr unzufrieden",
    2: "⭐⭐ 2 — Unzufrieden",
    3: "⭐⭐⭐ 3 — Okay",
    4: "⭐⭐⭐⭐ 4 — Gut",
    5: "⭐⭐⭐⭐⭐ 5 — Sehr gut",
}

# Module-Level Stores: JSONStore nutzt pro Pfad ein gemeinsames Lock,
# daher können andere Cogs (owner_admin, dashboard) problemlos eigene
# JSONStore-Instanzen auf denselben Pfad öffnen, um z.B. den Durchschnitt
# für !stats / das CMD-Dashboard mit auszulesen.
_ratings_store = JSONStore(RATINGS_PATH, [])
_pending_store = JSONStore(PENDING_PATH, {})


async def compute_rating_stats() -> dict:
    """Berechnet Durchschnitt, Anzahl und Sterne-Verteilung aller Bewertungen."""
    ratings = await _ratings_store.read()
    if not ratings:
        return {"count": 0, "average": 0.0, "distribution": {i: 0 for i in range(1, 6)}}

    distribution = {i: 0 for i in range(1, 6)}
    total = 0
    for entry in ratings:
        stars = int(entry.get("stars", 0))
        if 1 <= stars <= 5:
            distribution[stars] += 1
            total += stars

    count = len(ratings)
    average = round(total / count, 2) if count else 0.0
    return {"count": count, "average": average, "distribution": distribution}


class FeedbackModal(discord.ui.Modal, title="📋 Ticket-Feedback"):
    """Optionales Textfeedback nach der Sterne-Auswahl."""

    feedback = discord.ui.TextInput(
        label="Feedback (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Was können wir besser machen? (optional)",
        required=False,
        max_length=500,
    )

    def __init__(self, cog: "Ratings", token: str, stars: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.token = token
        self.stars = stars

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.finalize_rating(interaction, self.token, self.stars, str(self.feedback))


class RatingSelect(discord.ui.Select):
    """Sterne-Auswahl (1–5), persistenter custom_id enthält das Anfrage-Token."""

    def __init__(self, token: str):
        options = [
            discord.SelectOption(label=label, value=str(stars))
            for stars, label in STAR_LABELS.items()
        ]
        super().__init__(
            placeholder="⭐ Wie zufrieden warst du mit deinem Ticket?",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"rating_select:{token}",
        )
        self.token = token

    async def callback(self, interaction: discord.Interaction):
        cog: "Ratings" = interaction.client.get_cog("Ratings")
        if cog is None:
            await interaction.response.send_message(
                "❌ Bewertungssystem aktuell nicht verfügbar.", ephemeral=True
            )
            return

        pending = await _pending_store.read()
        if self.token not in pending:
            await interaction.response.send_message(
                "❌ Diese Bewertung wurde bereits abgegeben oder ist nicht mehr gültig.",
                ephemeral=True,
            )
            return

        stars = int(self.values[0])
        await interaction.response.send_modal(FeedbackModal(cog, self.token, stars))


class RatingView(discord.ui.View):
    """Persistente View, damit die Bewertung auch nach einem Bot-Neustart noch funktioniert."""

    def __init__(self, token: str):
        super().__init__(timeout=None)
        self.add_item(RatingSelect(token))


class Ratings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Persistente Views für alle noch offenen Bewertungsanfragen registrieren
        pending = await _pending_store.read()
        for token in pending:
            self.bot.add_view(RatingView(token))

    # ── Wird von cogs/tickets.py beim Schließen eines Tickets aufgerufen ────────

    async def request_rating(
        self,
        *,
        guild: discord.Guild,
        user_id: int,
        supporter_id: int,
        ticket_channel_id: int,
        ticket_name: str,
        panel_name: str,
        rating_log_channel_id: int | None,
    ) -> None:
        """Sendet dem Ticket-Ersteller eine DM mit der Sterne-Bewertungs-Anfrage."""
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return  # User nicht mehr auf dem Server erreichbar

        token = f"{ticket_channel_id}-{int(time.time())}"

        pending_record = {
            "guild_id": guild.id,
            "user_id": user_id,
            "supporter_id": supporter_id,
            "ticket_channel_id": ticket_channel_id,
            "ticket_name": ticket_name,
            "panel_name": panel_name,
            "rating_log_channel_id": rating_log_channel_id,
            "requested_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        async def mutate(data):
            data[token] = pending_record
            return data

        await _pending_store.update(mutate)

        embed = discord.Embed(
            title="⭐ Wie war dein Ticket-Erlebnis?",
            description=(
                f"Dein Ticket **{panel_name}** (`#{ticket_name}`) auf **{guild.name}** wurde geschlossen.\n\n"
                f"Bitte bewerte den Support mit **1 bis 5 Sternen** über das Menü unten. "
                f"Danach kannst du optional noch ein kurzes Feedback dalassen."
            ),
            color=COLOR_GOLD,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=get_footer_text(guild))

        view = RatingView(token)
        self.bot.add_view(view)  # persistent registrieren

        try:
            await member.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            # DMs deaktiviert → Anfrage wieder entfernen, kann nicht bewertet werden
            async def remove(data):
                data.pop(token, None)
                return data
            await _pending_store.update(remove)

    # ── Abschluss der Bewertung (aus dem Feedback-Modal) ─────────────────────

    async def finalize_rating(
        self,
        interaction: discord.Interaction,
        token: str,
        stars: int,
        feedback: str,
    ) -> None:
        pending = await _pending_store.read()
        record = pending.get(token)

        if record is None:
            await interaction.response.send_message(
                "❌ Diese Bewertung wurde bereits abgegeben oder ist abgelaufen.", ephemeral=True
            )
            return

        closed_at = datetime.datetime.now(datetime.timezone.utc)
        rating_entry = {
            "ticket_channel_id": record["ticket_channel_id"],
            "ticket_name": record["ticket_name"],
            "panel_name": record["panel_name"],
            "guild_id": record["guild_id"],
            "user_id": record["user_id"],
            "supporter_id": record["supporter_id"],
            "stars": stars,
            "feedback": feedback.strip() if feedback else "",
            "date": closed_at.isoformat(),
        }

        async def append(data):
            data.append(rating_entry)
            return data

        await _ratings_store.update(append)

        async def remove(data):
            data.pop(token, None)
            return data

        await _pending_store.update(remove)

        # Nutzer bestätigen
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Danke für deine Bewertung!",
                f"Du hast **{stars}/5 ⭐** vergeben.\n"
                + (f"Feedback: *{rating_entry['feedback']}*" if rating_entry["feedback"] else ""),
            ),
            ephemeral=True,
        )

        # Automatisch in den Bewertungs-/Ticket-Log senden
        log_channel_id = record.get("rating_log_channel_id")
        guild = self.bot.get_guild(record["guild_id"])
        if guild is not None and log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel is not None:
                stars_display = "⭐" * stars + "☆" * (5 - stars)
                log_embed = discord.Embed(
                    title="⭐ Neue Ticketbewertung",
                    color=COLOR_GOLD,
                    timestamp=closed_at,
                )
                log_embed.add_field(name="📋 Panel", value=record["panel_name"], inline=True)
                log_embed.add_field(name="📁 Ticket", value=f"#{record['ticket_name']}", inline=True)
                log_embed.add_field(name="\u200b", value="\u200b", inline=True)
                log_embed.add_field(name="👤 Bewertet von", value=f"<@{record['user_id']}>", inline=True)
                log_embed.add_field(name="🛡️ Supporter", value=f"<@{record['supporter_id']}>", inline=True)
                log_embed.add_field(name="🕐 Datum", value=f"<t:{int(closed_at.timestamp())}:F>", inline=True)
                log_embed.add_field(name="Bewertung", value=f"{stars_display}  ({stars}/5)", inline=False)
                log_embed.add_field(
                    name="💬 Feedback",
                    value=rating_entry["feedback"] or "*Kein Feedback abgegeben.*",
                    inline=False,
                )
                log_embed.set_footer(text=get_footer_text(guild))
                try:
                    await log_channel.send(embed=log_embed)
                except discord.HTTPException:
                    pass

    # ── !ratingstats ──────────────────────────────────────────────────────────

    @commands.command(name="ratingstats")
    async def rating_stats(self, ctx: commands.Context):
        """Zeigt Durchschnitt, Anzahl und Sterne-Verteilung aller Ticketbewertungen."""
        if ctx.guild is not None:
            # Nutzt das bestehende Rollen-Berechtigungssystem (Gruppe "moderation")
            fake_interaction_ok = ctx.author.guild_permissions.administrator
            if not fake_interaction_ok:
                # check_role_permission erwartet ein Interaction-artiges Objekt mit .user/.guild_id
                allowed = await self._check_prefix_permission(ctx)
                if not allowed:
                    await ctx.send(embed=error_embed(
                        "❌ Keine Berechtigung",
                        "Deine Rolle darf diesen Command nicht nutzen.",
                    ))
                    return

        stats = await compute_rating_stats()
        if stats["count"] == 0:
            await ctx.send(embed=gold_embed("⭐ Ticketbewertungen", "Es liegen noch keine Bewertungen vor."))
            return

        distribution = stats["distribution"]
        max_count = max(distribution.values()) or 1
        bar_lines = []
        for star in range(5, 0, -1):
            count = distribution[star]
            bar_len = int((count / max_count) * 15) if max_count else 0
            bar = "█" * bar_len + "░" * (15 - bar_len)
            bar_lines.append(f"{star}⭐ `{bar}` {count}")

        embed = discord.Embed(
            title="⭐ Ticketbewertungs-Statistik",
            color=COLOR_GOLD,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="📊 Durchschnitt", value=f"**{stats['average']} / 5** ⭐", inline=True)
        embed.add_field(name="🗳️ Anzahl Bewertungen", value=str(stats["count"]), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="📈 Verteilung", value="\n".join(bar_lines), inline=False)
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)

    @staticmethod
    async def _check_prefix_permission(ctx: commands.Context) -> bool:
        """Kleiner Adapter, damit check_role_permission (für Interactions gebaut)
        auch von einem Prefix-Command (ctx) genutzt werden kann."""

        class _FakeInteraction:
            def __init__(self, ctx):
                self.user = ctx.author
                self.guild_id = ctx.guild.id if ctx.guild else None

        return await check_role_permission(_FakeInteraction(ctx), "moderation")


async def setup(bot: commands.Bot):
    await bot.add_cog(Ratings(bot))
