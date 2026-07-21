"""
HugoSMP Trading-Vouch-System.
Optimierungen:
- Vouch-Sperre: 1 Bewertung pro User-Paar (kein Rating-Farming)
- Theme-Embeds für einheitliches Erscheinungsbild
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, gold_embed, FOOTER_TEXT, get_footer_text

VOUCH_PATH = "data/trading_vouches.json"


def default_vouches():
    return {}  # user_id (str) -> list[vouch dict]


class Trading(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self.store = JSONStore(VOUCH_PATH, default_vouches())

    @app_commands.command(name="trade-vouch", description="Bewerte einen Handelspartner mit 1-5 Sternen.")
    @app_commands.describe(
        user="Der bewertete Handelspartner",
        sterne="Bewertung von 1 (schlecht) bis 5 (exzellent)",
        grund="Kurze Begründung der Bewertung",
    )
    async def trade_vouch(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        sterne: app_commands.Range[int, 1, 5],
        grund: str,
    ):
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht möglich", "Du kannst dich nicht selbst bewerten."),
                ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht möglich", "Bots können nicht bewertet werden."),
                ephemeral=True)
            return

        from_id = interaction.user.id
        result_holder: dict = {}

        def mutate(data):
            entries = data.setdefault(str(user.id), [])
            # Vouch-Sperre: bereits bewertet? → Update statt Append
            for existing in entries:
                if existing.get("from_id") == from_id:
                    existing["sterne"]    = sterne
                    existing["grund"]     = grund
                    existing["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    result_holder["updated"] = True
                    return data
            entries.append({
                "from_id":   from_id,
                "from_name": str(interaction.user),
                "sterne":    sterne,
                "grund":     grund,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
            return data

        try:
            await self.store.update(mutate)
        except Exception as e:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehler beim Speichern", str(e)), ephemeral=True)
            return

        stars_display = "⭐" * sterne + "☆" * (5 - sterne)
        action = "aktualisiert" if result_holder.get("updated") else "gespeichert"
        embed  = success_embed(
            f"✅ Bewertung {action}",
            f"**Handelspartner:** {user.mention}\n"
            f"**Bewertung:** {stars_display} ({sterne}/5)\n"
            f"**Grund:** {grund}",
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="trade-stats", description="Zeigt die Trading-Statistiken eines Users an.")
    @app_commands.describe(user="Der User, dessen Stats angezeigt werden sollen")
    async def trade_stats(self, interaction: discord.Interaction, user: discord.Member = None):
        target  = user or interaction.user
        data    = await self.store.read()
        vouches = data.get(str(target.id), [])

        if not vouches:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Keine Bewertungen", f"{target.mention} hat noch keine Bewertungen erhalten."),
                ephemeral=True)
            return

        total = len(vouches)
        avg   = sum(v["sterne"] for v in vouches) / total

        embed = gold_embed(
            f"📊 Trading-Stats von {target.display_name}",
            f"**Durchschnitt:** {avg:.2f} / 5 ⭐\n**Anzahl:** {total} Bewertungen",
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        recent = sorted(vouches, key=lambda v: v["timestamp"], reverse=True)[:5]
        lines  = [f"{'⭐' * v['sterne']} von **{v['from_name']}** — {v['grund']}" for v in recent]
        embed.add_field(name="Letzte Bewertungen", value="\n".join(lines), inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Trading(bot))
