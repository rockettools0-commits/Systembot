"""
Verifikations-System: Button-Panel für neue Mitglieder.
Admins erstellen ein Panel; Klick auf "Verifizieren" vergibt die konfigurierte Rolle.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, COLOR_PURPLE, FOOTER_TEXT, get_footer_text

VERIF_CONFIG_PATH = "data/verification_config.json"


def default_config() -> dict:
    return {}  # guild_id -> {"role_id": int, "message_id": int, "channel_id": int}


class VerifyButton(discord.ui.View):
    """Persistenter View mit dem Verifizierungs-Button."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅  Jetzt verifizieren",
        style=discord.ButtonStyle.success,
        custom_id="verify:confirm",
    )
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "Verification" = interaction.client.get_cog("Verification")
        if cog is None:
            await interaction.response.send_message("Verifikations-System nicht verfügbar.", ephemeral=True)
            return
        await cog.handle_verify(interaction)


class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = JSONStore(VERIF_CONFIG_PATH, default_config())

    async def cog_load(self):
        self.bot.add_view(VerifyButton())

    # ── /verify-setup ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="verify-setup",
        description="Erstellt das Verifizierungs-Panel in einem Kanal.",
    )
    @app_commands.describe(
        kanal="Kanal, in dem das Panel gesendet wird",
        rolle="Rolle, die beim Verifizieren vergeben wird",
        beschreibung="Optionaler Beschreibungstext im Panel-Embed",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def verify_setup(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel,
        rolle: discord.Role,
        beschreibung: str = "Klicke auf den Button, um dich zu verifizieren und Zugriff auf den Server zu erhalten.",
    ):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="🔐  Verifizierung",
            description=beschreibung,
            color=COLOR_PURPLE,
        )
        embed.add_field(name="🎭 Vergebe Rolle", value=rolle.mention, inline=False)
        embed.add_field(
            name="💡 Hinweis",
            value="Klicke auf den Button — du erhältst die Rolle sofort und automatisch.",
            inline=False,
        )
        embed.set_footer(text=get_footer_text(interaction))

        try:
            msg = await kanal.send(embed=embed, view=VerifyButton())
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("❌ Kein Zugriff", f"Ich kann nicht in {kanal.mention} senden."),
                ephemeral=True,
            )
            return

        def mutate(data):
            data[str(interaction.guild.id)] = {
                "role_id": rolle.id,
                "message_id": msg.id,
                "channel_id": kanal.id,
            }
            return data

        await self.store.update(mutate)
        await interaction.followup.send(
            embed=success_embed("✅ Panel erstellt", f"Verifizierungs-Panel wurde in {kanal.mention} gesetzt."),
            ephemeral=True,
        )

    # ── Button-Handler ─────────────────────────────────────────────────────────

    async def handle_verify(self, interaction: discord.Interaction):
        data = await self.store.read()
        guild_conf = data.get(str(interaction.guild.id))

        if guild_conf is None:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht konfiguriert", "Kein Verifizierungs-Setup gefunden."),
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(guild_conf["role_id"])
        if role is None:
            await interaction.response.send_message(
                embed=error_embed("❌ Rolle nicht gefunden", "Die konfigurierte Rolle existiert nicht mehr."),
                ephemeral=True,
            )
            return

        if role in interaction.user.roles:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Bereits verifiziert", "Du hast die Rolle bereits."),
                ephemeral=True,
            )
            return

        try:
            await interaction.user.add_roles(role, reason="Verifizierungs-Button")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehlende Berechtigung", "Ich kann die Rolle nicht vergeben."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="✅  Erfolgreich verifiziert!",
            description=(
                f"Du hast die Rolle {role.mention} erhalten.\n"
                "**Willkommen auf dem Server!** 🎉"
            ),
            color=discord.Color.from_rgb(88, 214, 141),
        )
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
    await bot.add_cog(Verification(bot))
