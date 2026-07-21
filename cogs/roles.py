"""
Sticky-Roles & Auto-Role.
Speichert Rollen beim Verlassen und gibt sie beim Rejoin automatisch zurück.
Vergibt außerdem eine konfigurierbare Autorole an neue Mitglieder.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed
from utils.permissions import check_role_permission

STICKY_PATH = "data/sticky_roles.json"
CONFIG_PATH = "data/roles_config.json"


def default_sticky():
    return {}  # guild_id -> {user_id: [role_id, ...]}


def default_config():
    return {}  # guild_id -> {"autorole_id": int}


class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self.sticky_store = JSONStore(STICKY_PATH, default_sticky())
        self.config_store = JSONStore(CONFIG_PATH, default_config())
        # In-Memory-Cache für Autorole (guild_id -> role_id | None)
        self._autorole_cache: dict[int, int | None] = {}

    def _invalidate(self, guild_id: int) -> None:
        self._autorole_cache.pop(guild_id, None)

    # ── /autorole-set ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="autorole-set",
        description="Legt die Rolle fest, die neue Mitglieder automatisch erhalten.",
    )
    async def autorole_set(self, interaction: discord.Interaction, rolle: discord.Role):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)

        def mutate(data):
            data.setdefault(guild_id, {})["autorole_id"] = rolle.id
            return data

        await self.config_store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed("✅ Autorole gesetzt",
                                f"Neue Mitglieder erhalten automatisch {rolle.mention}."),
            ephemeral=True,
        )

    # ── Listener ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        role_ids = [r.id for r in member.roles if r != member.guild.default_role]
        if not role_ids:
            return

        guild_id = str(member.guild.id)

        def mutate(data):
            data.setdefault(guild_id, {})[str(member.id)] = role_ids
            return data

        try:
            await self.sticky_store.update(mutate)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild_id = str(member.guild.id)

        # Sticky Roles zurückgeben
        sticky_data    = await self.sticky_store.read()
        saved_role_ids = sticky_data.get(guild_id, {}).get(str(member.id))

        if saved_role_ids:
            roles_to_add = [
                member.guild.get_role(rid)
                for rid in saved_role_ids
                if member.guild.get_role(rid) is not None
                and member.guild.get_role(rid) < member.guild.me.top_role
            ]
            if roles_to_add:
                try:
                    await member.add_roles(*roles_to_add, reason="Sticky-Roles Wiederherstellung")
                except discord.HTTPException:
                    pass

            def cleanup(data):
                data.get(guild_id, {}).pop(str(member.id), None)
                return data

            await self.sticky_store.update(cleanup)

        # Autorole vergeben — cache-first
        autorole_id = self._autorole_cache.get(member.guild.id, -1)
        if autorole_id == -1:
            config      = await self.config_store.read()
            autorole_id = config.get(guild_id, {}).get("autorole_id")
            self._autorole_cache[member.guild.id] = autorole_id

        if autorole_id:
            role = member.guild.get_role(autorole_id)
            if role is not None and role < member.guild.me.top_role:
                try:
                    await member.add_roles(role, reason="Autorole")
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
