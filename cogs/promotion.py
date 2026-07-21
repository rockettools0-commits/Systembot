"""
Promotion / Demotion System für AVOKE.

Admins definieren über /rank-setup eine geordnete Rang-Hierarchie (Rollen-Liste).
/promote  — Befördert ein Mitglied auf den nächsten Rang
/demote   — Stuft ein Mitglied auf den vorigen Rang herab
/rank-setup  — Legt die Rang-Reihenfolge fest (niedrigster → höchster Rang)
/rank-list   — Zeigt die aktuelle Rang-Hierarchie

Aktionen werden automatisch im Clan-Log geloggt (bot.dispatch clan_action).
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, warning_embed, FOOTER_TEXT, get_footer_text
from utils.permissions import check_role_permission

RANK_CONFIG_PATH = "data/rank_config.json"


def default_rank_config() -> dict:
    return {}  # guild_id -> {"ranks": [role_id, ...]}  aufsteigend sortiert


class Promotion(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self.store = JSONStore(RANK_CONFIG_PATH, default_rank_config())

    clan = app_commands.Group(name="clan", description="Clan-Verwaltung: Ränge, Promote, Demote.")

    # ─────────────────────────────────────────────────────────────────────────
    # /clan setup — Rang-Hierarchie festlegen
    # ─────────────────────────────────────────────────────────────────────────

    @clan.command(
        name="setup",
        description="Legt die Rang-Reihenfolge fest (von niedrigst bis höchst).",
    )
    @app_commands.describe(
        rang1="Niedrigster Rang",
        rang2="Rang 2",
        rang3="Rang 3 (optional)",
        rang4="Rang 4 (optional)",
        rang5="Rang 5 (optional)",
        rang6="Rang 6 (optional)",
        rang7="Rang 7 (optional)",
        rang8="Höchster Rang (optional)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def rank_setup(
        self,
        interaction: discord.Interaction,
        rang1: discord.Role,
        rang2: discord.Role,
        rang3: discord.Role = None,
        rang4: discord.Role = None,
        rang5: discord.Role = None,
        rang6: discord.Role = None,
        rang7: discord.Role = None,
        rang8: discord.Role = None,
    ):
        ranks = [r for r in [rang1, rang2, rang3, rang4, rang5, rang6, rang7, rang8] if r]
        guild_id = str(interaction.guild_id)

        def mutate(data: dict) -> dict:
            data[guild_id] = {"ranks": [r.id for r in ranks]}
            return data

        await self.store.update(mutate)

        lines = "\n".join(
            f"`{i + 1}.` {r.mention}" for i, r in enumerate(ranks)
        )
        embed = success_embed(
            "✅ Rang-Hierarchie gespeichert",
            f"Folgende Reihenfolge wurde gesetzt (aufsteigend):\n\n{lines}",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # /clan ranks — aktuelle Hierarchie anzeigen
    # ─────────────────────────────────────────────────────────────────────────

    @clan.command(name="ranks", description="Zeigt die konfigurierte Rang-Hierarchie an.")
    async def rank_list(self, interaction: discord.Interaction):
        data     = await self.store.read()
        guild_id = str(interaction.guild_id)
        ranks    = data.get(guild_id, {}).get("ranks", [])

        if not ranks:
            await interaction.response.send_message(
                embed=info_embed(
                    "📋 Keine Hierarchie",
                    "Es wurde noch keine Rang-Hierarchie konfiguriert.\nNutze `/rank-setup` um Ränge festzulegen.",
                ),
                ephemeral=True,
            )
            return

        lines = []
        for i, rid in enumerate(ranks):
            role = interaction.guild.get_role(rid)
            name = role.mention if role else f"~~Gelöschte Rolle ({rid})~~"
            crown = " 👑" if i == len(ranks) - 1 else ""
            lines.append(f"`{i + 1}.` {name}{crown}")

        embed = discord.Embed(
            title="📋 Rang-Hierarchie",
            description="\n".join(lines),
            color=discord.Color.from_rgb(100, 65, 165),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=f"{get_footer_text(interaction)}  ·  {len(ranks)} Ränge konfiguriert")
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # /clan promote
    # ─────────────────────────────────────────────────────────────────────────

    @clan.command(name="promote", description="Befördert ein Mitglied auf den nächsten Rang.")
    @app_commands.describe(
        member="Das Mitglied das befördert wird",
        grund="Grund für die Beförderung",
    )
    async def promote(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        grund: str = "Beförderung",
    ):
        if not await check_role_permission(interaction, "promotion"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        result = await self._change_rank(interaction, member, direction=+1, grund=grund)
        if result:
            old_role, new_role = result
            embed = discord.Embed(
                title=f"⬆️ {member.display_name} wurde befördert!",
                description=(
                    f"**Von:** {old_role.mention}\n"
                    f"**Zu:** {new_role.mention}\n"
                    f"**Grund:** {grund}"
                ),
                color=discord.Color.from_rgb(46, 204, 113),
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=get_footer_text(interaction))
            await interaction.response.send_message(embed=embed)
            self.bot.dispatch(
                "clan_action", interaction.guild, "uprank",
                member, interaction.user, grund,
                f"{old_role.mention} → {new_role.mention}",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # /clan demote
    # ─────────────────────────────────────────────────────────────────────────

    @clan.command(name="demote", description="Stuft ein Mitglied auf den vorigen Rang herab.")
    @app_commands.describe(
        member="Das Mitglied das herabgestuft wird",
        grund="Grund für die Herabstufung",
    )
    async def demote(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        grund: str = "Herabstufung",
    ):
        if not await check_role_permission(interaction, "promotion"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        result = await self._change_rank(interaction, member, direction=-1, grund=grund)
        if result:
            old_role, new_role = result
            embed = discord.Embed(
                title=f"⬇️ {member.display_name} wurde herabgestuft.",
                description=(
                    f"**Von:** {old_role.mention}\n"
                    f"**Zu:** {new_role.mention}\n"
                    f"**Grund:** {grund}"
                ),
                color=discord.Color.from_rgb(231, 76, 60),
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=get_footer_text(interaction))
            await interaction.response.send_message(embed=embed)
            self.bot.dispatch(
                "clan_action", interaction.guild, "derank",
                member, interaction.user, grund,
                f"{old_role.mention} → {new_role.mention}",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Interne Hilfsmethode
    # ─────────────────────────────────────────────────────────────────────────

    async def _change_rank(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        direction: int,    # +1 promote, -1 demote
        grund: str,
    ) -> tuple[discord.Role, discord.Role] | None:
        data     = await self.store.read()
        guild_id = str(interaction.guild_id)
        rank_ids = data.get(guild_id, {}).get("ranks", [])

        if not rank_ids:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ Keine Hierarchie",
                    "Es wurde noch keine Rang-Hierarchie konfiguriert.\nNutze `/rank-setup`.",
                ),
                ephemeral=True,
            )
            return None

        # Aktuellen Rang des Mitglieds finden
        member_role_ids = {r.id for r in member.roles}
        current_idx     = None
        for i, rid in enumerate(rank_ids):
            if rid in member_role_ids:
                current_idx = i
                break

        if current_idx is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ Kein Rang gefunden",
                    f"{member.mention} hat keinen der konfigurierten Ränge.",
                ),
                ephemeral=True,
            )
            return None

        new_idx = current_idx + direction

        if new_idx < 0:
            await interaction.response.send_message(
                embed=warning_embed(
                    "⚠️ Niedrigster Rang",
                    f"{member.mention} ist bereits auf dem niedrigsten Rang.",
                ),
                ephemeral=True,
            )
            return None

        if new_idx >= len(rank_ids):
            await interaction.response.send_message(
                embed=warning_embed(
                    "⚠️ Höchster Rang",
                    f"{member.mention} ist bereits auf dem höchsten Rang.",
                ),
                ephemeral=True,
            )
            return None

        old_role = interaction.guild.get_role(rank_ids[current_idx])
        new_role = interaction.guild.get_role(rank_ids[new_idx])

        if old_role is None or new_role is None:
            await interaction.response.send_message(
                embed=error_embed("❌ Rolle nicht gefunden",
                                  "Eine der konfigurierten Rang-Rollen existiert nicht mehr."),
                ephemeral=True,
            )
            return None

        try:
            await member.remove_roles(old_role, reason=f"{grund} | Von: {interaction.user}")
            await member.add_roles(new_role, reason=f"{grund} | Von: {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehlende Berechtigung",
                                  "Ich kann diese Rollen nicht vergeben/entfernen."),
                ephemeral=True,
            )
            return None
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehler", str(e)), ephemeral=True,
            )
            return None

        return old_role, new_role

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            msg = error_embed("❌ Keine Berechtigung", "Du benötigst Administrator-Rechte.")
        else:
            msg = error_embed("❌ Fehler", str(error))
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Promotion(bot))
