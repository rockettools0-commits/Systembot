"""Sichtbarer Ueberblick ueber die aktuellen System-Erweiterungen."""

import discord
from discord import app_commands
from discord.ext import commands


class Changelog(commands.Cog):
    """Stellt den aktuellen Funktionsstand direkt in Discord bereit."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="changelog", description="Zeigt die neuesten Bot-Updates und Features an.")
    async def changelog(self, interaction: discord.Interaction):
        """Zeigt die wichtigsten Neuerungen des Bot-Systems an."""
        guild_name = interaction.guild.name if interaction.guild else "System"
        embed = discord.Embed(
            title=f"{guild_name} — Changelog",
            description="Die neuesten Erweiterungen auf einen Blick.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🎫 Ticket-System",
            value=(
                "[+] Server-Name in allen Ticket-Embeds, Transkripten und Log-Nachrichten\n"
                "[+] Mehrere Support-Rollen pro Panel — Zugriff + Ping beim Öffnen\n"
                "[+] Ticketbewertung mit 1–5 Sternen und optionalem Feedback\n"
                "[+] `/ticket setup` — Panel mit bis zu 5 Support-Rollen einrichten\n"
                "[+] `/ticket info` — Alle offenen Tickets des Servers anzeigen"
            ),
            inline=False,
        )
        embed.add_field(
            name="🖥️ Server-Branding",
            value=(
                "[+] Alle Embeds, Transkripte und Footer zeigen den echten Server-Namen\n"
                "[/] Kein hardcodiertes 'AVOKE | System' mehr — vollständig dynamisch"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛡️ Moderation — Neue Befehle",
            value=(
                "[+] `/mod warn-stats` — Verwarnsstatistik für einen User\n"
                "[+] `/mod clearnick-bulk` — Nicknames einer ganzen Rolle zurücksetzen\n"
                "[+] `/mod massban` — Mehrere User-IDs auf einmal bannen\n"
                "[+] `/mod nick-history` — Letzte 10 Nicknames eines Users"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎉 Giveaway-System",
            value=(
                "[+] Dauer in Stunden (`dauer_stunden: float`) — Dezimalwerte erlaubt\n"
                "[+] Live-Teilnehmerzähler auf dem Giveaway-Embed\n"
                "[+] Deaktivierter End-Button nach Giveaway-Abschluss\n"
                "[/] Embed-Design überarbeitet — Theme-Farben und einheitliche Felder"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ Owner & Monitoring",
            value=(
                "[+] `/owner stats`, `/owner health`, `/owner diagnostics`\n"
                "[+] `/owner backup` / `/owner backuplist` — Data-Backups aus Discord\n"
                "[+] Live CMD-Dashboard mit Status, RAM, CPU und Fehler-Ringpuffer\n"
                "[/] Alle Owner-Commands in `/owner` und `/owneradmin` Gruppen"
            ),
            inline=False,
        )
        embed.set_footer(text=f"{guild_name} • Changelog")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Changelog(bot))
