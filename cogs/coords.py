"""
HugoSMP Coords-Safe.
Sichere Speicherung von Basen-/Farm-Koordinaten, nur für Clan-Mitglieder sichtbar.
Optimierungen:
- Theme-Embeds
- Welt-Typ (Overworld / Nether / End) als Pflichtfeld mit Dropdown
"""

import re
import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, FOOTER_TEXT, get_footer_text

COORDS_PATH   = "data/coords.json"
COORD_PATTERN = re.compile(r"^-?\d+\s+-?\d+\s+-?\d+$")
MEMBER_ROLE_NAME = "Member"

WELT_EMOJIS = {"overworld": "🌍", "nether": "🔥", "end": "🌑"}


def default_coords():
    return {}


class Coords(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self.store = JSONStore(COORDS_PATH, default_coords())

    def _is_member(self, interaction: discord.Interaction) -> bool:
        role = discord.utils.get(interaction.guild.roles, name=MEMBER_ROLE_NAME)
        return role is not None and role in interaction.user.roles

    @app_commands.command(name="coords-add", description="Speichere Koordinaten einer Base oder Farm.")
    @app_commands.describe(
        name="Name des Ortes (z.B. 'Basis Nord')",
        koordinaten="Koordinaten im Format 'X Y Z'",
        welt="Welt-Typ (Overworld / Nether / End)",
    )
    @app_commands.choices(welt=[
        app_commands.Choice(name="🌍 Overworld", value="overworld"),
        app_commands.Choice(name="🔥 Nether",    value="nether"),
        app_commands.Choice(name="🌑 End",        value="end"),
    ])
    async def coords_add(self, interaction: discord.Interaction,
                         name: str, koordinaten: str, welt: str = "overworld"):
        if not self._is_member(interaction):
            await interaction.response.send_message(
                embed=error_embed("❌ Kein Zugriff",
                                  f"Nur Mitglieder mit der Rolle **{MEMBER_ROLE_NAME}** dürfen Koordinaten speichern."),
                ephemeral=True)
            return

        koordinaten = koordinaten.strip()
        if not COORD_PATTERN.match(koordinaten):
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültiges Format", "Bitte nutze: `X Y Z` (z.B. `120 64 -340`)."),
                ephemeral=True)
            return

        x, y, z  = koordinaten.split()
        guild_id = str(interaction.guild.id)
        entry    = {
            "x":          int(x),
            "y":          int(y),
            "z":          int(z),
            "welt":       welt,
            "added_by":   str(interaction.user),
            "added_by_id": interaction.user.id,
            "timestamp":  datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        def mutate(data):
            data.setdefault(guild_id, {})[name] = entry
            return data

        try:
            await self.store.update(mutate)
        except Exception as e:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehler beim Speichern", str(e)), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(
                f"📍 Koordinaten gespeichert",
                f"**Name:** {name}\n**Koordinaten:** `{x} {y} {z}`\n"
                f"**Welt:** {WELT_EMOJIS[welt]} {welt.title()}",
            ),
            ephemeral=True)

    @app_commands.command(name="coords-get", description="Rufe gespeicherte Koordinaten ab.")
    @app_commands.describe(
        name="Name des Ortes (leer lassen für alle Einträge)",
        welt="Optional nach Welt filtern",
    )
    @app_commands.choices(welt=[
        app_commands.Choice(name="🌍 Overworld", value="overworld"),
        app_commands.Choice(name="🔥 Nether",    value="nether"),
        app_commands.Choice(name="🌑 End",        value="end"),
    ])
    async def coords_get(self, interaction: discord.Interaction,
                         name: str = None, welt: str = None):
        if not self._is_member(interaction):
            await interaction.response.send_message(
                embed=error_embed("❌ Kein Zugriff",
                                  f"Nur Mitglieder mit der Rolle **{MEMBER_ROLE_NAME}**."),
                ephemeral=True)
            return

        data         = await self.store.read()
        guild_coords = data.get(str(interaction.guild.id), {})

        if not guild_coords:
            await interaction.response.send_message(
                embed=info_embed("📍 Keine Koordinaten", "Es sind noch keine Koordinaten gespeichert."),
                ephemeral=True)
            return

        if name:
            entry = guild_coords.get(name)
            if entry is None:
                await interaction.response.send_message(
                    embed=error_embed("❌ Nicht gefunden", f"Kein Eintrag mit dem Namen **{name}**."),
                    ephemeral=True)
                return
            w = entry.get("welt", "overworld")
            embed = info_embed(
                f"📍 {name}",
                f"**Koordinaten:** `{entry['x']} {entry['y']} {entry['z']}`\n"
                f"**Welt:** {WELT_EMOJIS.get(w,'🌍')} {w.title()}\n"
                f"**Hinzugefügt von:** {entry['added_by']}",
            )
            embed.set_footer(text=get_footer_text(interaction))
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Alle Einträge — optional nach Welt filtern
        items = list(guild_coords.items())
        if welt:
            items = [(n, e) for n, e in items if e.get("welt", "overworld") == welt]

        if not items:
            await interaction.response.send_message(
                embed=info_embed("📍 Keine Einträge",
                                 f"Keine Koordinaten für {WELT_EMOJIS.get(welt,'')} {(welt or '').title()} gespeichert."),
                ephemeral=True)
            return

        embed = info_embed("📍 Gespeicherte Koordinaten")
        for loc_name, e in items[:25]:
            w = e.get("welt", "overworld")
            embed.add_field(
                name=f"{WELT_EMOJIS.get(w,'🌍')} {loc_name}",
                value=f"`{e['x']} {e['y']} {e['z']}` — {e['added_by']}",
                inline=False,
            )
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="coords-remove", description="Entferne einen gespeicherten Koordinaten-Eintrag.")
    @app_commands.describe(name="Name des zu löschenden Ortes")
    async def coords_remove(self, interaction: discord.Interaction, name: str):
        if not self._is_member(interaction):
            await interaction.response.send_message(
                embed=error_embed("❌ Kein Zugriff",
                                  f"Nur Mitglieder mit der Rolle **{MEMBER_ROLE_NAME}**."),
                ephemeral=True)
            return

        guild_id = str(interaction.guild.id)
        removed  = {"ok": False}

        def mutate(data):
            gc = data.get(guild_id, {})
            if name in gc:
                del gc[name]
                removed["ok"] = True
            return data

        await self.store.update(mutate)

        if removed["ok"]:
            await interaction.response.send_message(
                embed=success_embed("✅ Eintrag gelöscht", f"**{name}** wurde entfernt."),
                ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht gefunden", f"Kein Eintrag mit dem Namen **{name}**."),
                ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Coords(bot))
