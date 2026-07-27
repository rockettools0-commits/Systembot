"""
/setup — Geführtes Server-Setup in einem einzigen Command.

Führt den Admin Schritt für Schritt durch die wichtigsten Konfigurationen:
  1. Welcome / Leave  → Welcome-Kanal + Leave-Kanal
  2. Verifikation     → Verifikations-Kanal + Rolle
  3. Clan-Log         → Log-Kanal für alle Mod-Aktionen
  4. Admin-Log        → Kanal für Command-Logs / gelöschte Nachrichten
  5. Stream-Ping      → Ankündigungskanal + Ping-Rolle
  6. Autorole         → Rolle die neue Mitglieder automatisch erhalten

Jeder Schritt ist ein eigener View mit Channel/Role-Selects.
Der Admin kann einzelne Schritte überspringen (Schaltfläche "Überspringen").
Am Ende erscheint eine Zusammenfassung aller konfigurierten Einstellungen.
"""

import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.permissions import save_group_roles
from utils.theme import (
    success_embed, error_embed, info_embed, warning_embed,
    COLOR_PRIMARY, COLOR_INFO, COLOR_GOLD, COLOR_WARNING, FOOTER_TEXT,
)

# ── Pfade (identisch zu den jeweiligen Cogs) ──────────────────────────────────
WELCOME_CONFIG_PATH = "data/welcome_config.json"
VERIF_CONFIG_PATH   = "data/verification_config.json"
CLANLOG_CONFIG_PATH = "data/clanlog_config.json"
LOGGING_CONFIG_PATH = "data/logging_config.json"
STREAMER_CONFIG_PATH= "data/streamer_config.json"
ROLES_CONFIG_PATH   = "data/roles_config.json"

# Berechtigungs-Gruppen: (key, label, beschreibung)
PERM_GROUPS = [
    ("moderation", "🛡️ Moderation",  "ban, kick, mute, timeout, warn, lockdown, say, announce, lock, unlock, automod-*, mod *, giveaway-*, admin-log-set, autorole-set, bot-status, stream-*"),
    ("utility",    "🔧 Utility",      "clear, slowmode, nick, addxp, removexp, setxp, resetxp"),
    ("promotion",  "⬆️ Promotion",    "promote, demote"),
    ("clan",       "📋 Clan-Aktionen", "uprank, derank, clan-kick"),
]

# Schritte in Reihenfolge: (key, label, emoji, farbe, beschreibung)
STEPS = [
    ("welcome",    "Welcome / Leave",  "👋", discord.Color.from_rgb(46,  204, 113), "Wo sollen Beitritts- und Abschiedsnachrichten erscheinen?"),
    ("verify",     "Verifizierung",    "🔐", discord.Color.from_rgb(155, 89,  182), "In welchem Kanal soll das Verifizierungs-Panel erscheinen und welche Rolle bekommt der User?"),
    ("clanlog",    "Clan-Log",         "📋", discord.Color.from_rgb(100, 65,  165), "In welchen Kanal sollen alle Mod-Aktionen (Ban, Kick, Mute, ...) geloggt werden?"),
    ("adminlog",   "Admin-Log",        "📜", discord.Color.from_rgb(52,  152, 219), "In welchen Kanal sollen Command-Nutzung und gelöschte Nachrichten geloggt werden?"),
    ("stream",     "Stream-Ping",      "📡", discord.Color.from_rgb(100, 65,  165), "Wo sollen Go-Live-Ankündigungen erscheinen und welche Rolle wird gepingt?"),
    ("autorole",   "Autorole",         "🎭", discord.Color.from_rgb(241, 196, 15),  "Welche Rolle sollen neue Mitglieder automatisch erhalten?"),
    ("perms",      "Berechtigungen",   "🔒", discord.Color.from_rgb(52,  73, 94),   "Welche Rollen dürfen welche Command-Gruppen nutzen?\n\nWähle zuerst eine Gruppe, dann die erlaubten Rollen."),
]


def _step_embed(step_index: int, total: int) -> discord.Embed:
    key, label, emoji, color, desc = STEPS[step_index]
    embed = discord.Embed(
        title=f"{emoji}  Setup — {label}",
        description=desc,
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.set_footer(text=f"Setup  ·  Schritt {step_index + 1} / {total}")
    return embed


def _summary_embed(results: dict) -> discord.Embed:
    """Baut die finale Zusammenfassungs-Embed."""
    embed = discord.Embed(
        title="✅ Setup abgeschlossen",
        description="Hier ist eine Übersicht aller konfigurierten Einstellungen:",
        color=COLOR_PRIMARY,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    labels = {
        "welcome_kanal":  "👋 Welcome-Kanal",
        "leave_kanal":    "👋 Leave-Kanal",
        "verify_kanal":   "🔐 Verifikations-Kanal",
        "verify_rolle":   "🔐 Verifikations-Rolle",
        "clanlog_kanal":  "📋 Clan-Log-Kanal",
        "adminlog_kanal": "📜 Admin-Log-Kanal",
        "stream_kanal":   "📡 Stream-Ping-Kanal",
        "stream_rolle":   "📡 Stream-Ping-Rolle",
        "autorole":       "🎭 Autorole",
    }
    skipped = []
    for key, display in labels.items():
        val = results.get(key)
        if val:
            embed.add_field(name=display, value=val, inline=True)
        else:
            skipped.append(display.split(" ", 1)[1])

    # Berechtigungen zusammenfassen
    perms_lines = []
    for gkey, glabel, _ in PERM_GROUPS:
        val = results.get(f"perm_{gkey}")
        if val:
            perms_lines.append(f"{glabel}: {val}")
    if perms_lines:
        embed.add_field(name="🔒 Berechtigungen", value="\n".join(perms_lines), inline=False)

    if skipped:
        embed.add_field(
            name="⏭️ Übersprungen",
            value="\n".join(f"• {s}" for s in skipped),
            inline=False,
        )
    embed.set_footer(text="Setup abgeschlossen")
    return embed


# ── Step-Views ─────────────────────────────────────────────────────────────────

class SetupWelcomeView(discord.ui.View):
    def __init__(self, wizard: "SetupWizard"):
        super().__init__(timeout=120)
        self.wizard = wizard

        self.welcome_select = discord.ui.ChannelSelect(
            placeholder="👋 Welcome-Kanal wählen …",
            channel_types=[discord.ChannelType.text],
            row=0,
        )
        self.leave_select = discord.ui.ChannelSelect(
            placeholder="👋 Leave-Kanal wählen (optional) …",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            row=1,
        )
        self.welcome_select.callback = self._noop
        self.leave_select.callback   = self._noop
        self.add_item(self.welcome_select)
        self.add_item(self.leave_select)

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="✅ Bestätigen", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.welcome_select.values:
            await interaction.response.send_message(
                embed=error_embed("❌ Pflichtfeld", "Bitte wähle mindestens den Welcome-Kanal."),
                ephemeral=True,
            )
            return
        wch = self.welcome_select.values[0]
        lch = self.leave_select.values[0] if self.leave_select.values else wch
        self.wizard.results["welcome_kanal"] = wch.mention
        self.wizard.results["leave_kanal"]   = lch.mention
        await self.wizard.save_welcome(wch.id, lch.id, interaction.guild_id)
        await self.wizard.advance(interaction)

    @discord.ui.button(label="⏭️ Überspringen", style=discord.ButtonStyle.secondary, row=2)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.wizard.advance(interaction)

    async def on_timeout(self):
        self._disable_all()

    def _disable_all(self):
        for item in self.children:
            item.disabled = True


class SetupVerifyView(discord.ui.View):
    def __init__(self, wizard: "SetupWizard"):
        super().__init__(timeout=120)
        self.wizard = wizard

        self.kanal_select = discord.ui.ChannelSelect(
            placeholder="🔐 Verifikations-Kanal wählen …",
            channel_types=[discord.ChannelType.text],
            row=0,
        )
        self.rolle_select = discord.ui.RoleSelect(
            placeholder="🔐 Verifikations-Rolle wählen …",
            row=1,
        )
        self.kanal_select.callback = self._noop
        self.rolle_select.callback = self._noop
        self.add_item(self.kanal_select)
        self.add_item(self.rolle_select)

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="✅ Bestätigen", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.kanal_select.values or not self.rolle_select.values:
            await interaction.response.send_message(
                embed=error_embed("❌ Pflichtfeld", "Bitte wähle Kanal und Rolle aus."),
                ephemeral=True,
            )
            return
        kanal = self.kanal_select.values[0]
        rolle = self.rolle_select.values[0]
        self.wizard.results["verify_kanal"] = kanal.mention
        self.wizard.results["verify_rolle"] = rolle.mention
        await self.wizard.save_verify(kanal, rolle, interaction)
        await self.wizard.advance(interaction)

    @discord.ui.button(label="⏭️ Überspringen", style=discord.ButtonStyle.secondary, row=2)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.wizard.advance(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class SetupSingleChannelView(discord.ui.View):
    """Wiederverwendbar für Clan-Log und Admin-Log (nur 1 Kanal-Auswahl)."""
    def __init__(self, wizard: "SetupWizard", result_key: str, save_fn, placeholder: str):
        super().__init__(timeout=120)
        self.wizard     = wizard
        self.result_key = result_key
        self.save_fn    = save_fn

        self.kanal_select = discord.ui.ChannelSelect(
            placeholder=placeholder,
            channel_types=[discord.ChannelType.text],
            row=0,
        )
        self.kanal_select.callback = self._noop
        self.add_item(self.kanal_select)

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="✅ Bestätigen", style=discord.ButtonStyle.success, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.kanal_select.values:
            await interaction.response.send_message(
                embed=error_embed("❌ Pflichtfeld", "Bitte wähle einen Kanal aus."),
                ephemeral=True,
            )
            return
        kanal = self.kanal_select.values[0]
        self.wizard.results[self.result_key] = kanal.mention
        await self.save_fn(kanal.id, interaction.guild_id)
        await self.wizard.advance(interaction)

    @discord.ui.button(label="⏭️ Überspringen", style=discord.ButtonStyle.secondary, row=1)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.wizard.advance(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class SetupStreamView(discord.ui.View):
    def __init__(self, wizard: "SetupWizard"):
        super().__init__(timeout=120)
        self.wizard = wizard

        self.kanal_select = discord.ui.ChannelSelect(
            placeholder="📡 Ankündigungs-Kanal wählen …",
            channel_types=[discord.ChannelType.text],
            row=0,
        )
        self.rolle_select = discord.ui.RoleSelect(
            placeholder="📡 Ping-Rolle wählen …",
            row=1,
        )
        self.kanal_select.callback = self._noop
        self.rolle_select.callback = self._noop
        self.add_item(self.kanal_select)
        self.add_item(self.rolle_select)

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="✅ Bestätigen", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.kanal_select.values or not self.rolle_select.values:
            await interaction.response.send_message(
                embed=error_embed("❌ Pflichtfeld", "Bitte wähle Kanal und Rolle aus."),
                ephemeral=True,
            )
            return
        kanal = self.kanal_select.values[0]
        rolle = self.rolle_select.values[0]
        self.wizard.results["stream_kanal"] = kanal.mention
        self.wizard.results["stream_rolle"] = rolle.mention
        await self.wizard.save_stream(kanal.id, rolle.id, interaction.guild_id)
        await self.wizard.advance(interaction)

    @discord.ui.button(label="⏭️ Überspringen", style=discord.ButtonStyle.secondary, row=2)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.wizard.advance(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class SetupAutoRoleView(discord.ui.View):
    def __init__(self, wizard: "SetupWizard"):
        super().__init__(timeout=120)
        self.wizard = wizard

        self.rolle_select = discord.ui.RoleSelect(
            placeholder="🎭 Autorole wählen …",
            row=0,
        )
        self.rolle_select.callback = self._noop
        self.add_item(self.rolle_select)

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="✅ Bestätigen", style=discord.ButtonStyle.success, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.rolle_select.values:
            await interaction.response.send_message(
                embed=error_embed("❌ Pflichtfeld", "Bitte wähle eine Rolle aus."),
                ephemeral=True,
            )
            return
        rolle = self.rolle_select.values[0]
        self.wizard.results["autorole"] = rolle.mention
        await self.wizard.save_autorole(rolle.id, interaction.guild_id)
        await self.wizard.advance(interaction)

    @discord.ui.button(label="⏭️ Überspringen", style=discord.ButtonStyle.secondary, row=1)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.wizard.advance(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Wizard-Controller ──────────────────────────────────────────────────────────

class SetupWizard:
    """Verwaltet den Fortschritt des Setup-Wizards für einen Admin."""

    def __init__(self, cog: "Setup", interaction: discord.Interaction):
        self.cog         = cog
        self.guild       = interaction.guild
        self.results:    dict[str, str] = {}
        self.step_index: int = 0

    async def start(self, interaction: discord.Interaction) -> None:
        embed = _step_embed(0, len(STEPS))
        view  = self._view_for_step(0)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def advance(self, interaction: discord.Interaction) -> None:
        self.step_index += 1
        if self.step_index >= len(STEPS):
            # Alle Schritte durch → Zusammenfassung
            await interaction.response.edit_message(
                embed=_summary_embed(self.results),
                view=None,
            )
            # Wizard aus der aktiven Liste entfernen
            self.cog._active_wizards.pop(interaction.user.id, None)
            return

        embed = _step_embed(self.step_index, len(STEPS))
        view  = self._view_for_step(self.step_index)
        await interaction.response.edit_message(embed=embed, view=view)

    def _view_for_step(self, index: int) -> discord.ui.View:
        key = STEPS[index][0]
        if key == "welcome":
            return SetupWelcomeView(self)
        if key == "verify":
            return SetupVerifyView(self)
        if key == "clanlog":
            return SetupSingleChannelView(
                self, "clanlog_kanal",
                self.save_clanlog,
                "📋 Clan-Log-Kanal wählen …",
            )
        if key == "adminlog":
            return SetupSingleChannelView(
                self, "adminlog_kanal",
                self.save_adminlog,
                "📜 Admin-Log-Kanal wählen …",
            )
        if key == "stream":
            return SetupStreamView(self)
        if key == "autorole":
            return SetupAutoRoleView(self)
        if key == "perms":
            return SetupPermsView(self, perm_group_index=0)
        # Fallback
        return discord.ui.View()

    # ── Speicher-Hilfsmethoden — schreiben direkt in die JSON-Stores ──────────

    async def save_welcome(self, welcome_id: int, leave_id: int, guild_id: int) -> None:
        gid = str(guild_id)
        def mutate(data):
            data[gid] = {"channel_id": welcome_id, "leave_channel_id": leave_id}
            return data
        await self.cog.welcome_store.update(mutate)

    async def save_verify(
        self,
        kanal: discord.abc.GuildChannel,
        rolle: discord.Role,
        interaction: discord.Interaction,
    ) -> None:
        gid = str(interaction.guild_id)
        # Verifikations-Panel senden
        verif_cog = self.cog.bot.get_cog("Verification")
        if verif_cog:
            try:
                from cogs.verification import VerifyButton
                embed = discord.Embed(
                    title="🔐 Verifizierung",
                    description="Klicke auf den Button, um dich zu verifizieren und Zugriff auf den Server zu erhalten.",
                    color=discord.Color.from_rgb(155, 89, 182),
                )
                embed.add_field(name="Vergebe Rolle", value=rolle.mention, inline=False)
                embed.set_footer(text=f"{interaction.guild.name} │ System" if interaction.guild else FOOTER_TEXT)
                msg = await kanal.send(embed=embed, view=VerifyButton())
                def mutate(data):
                    data[gid] = {"role_id": rolle.id, "message_id": msg.id, "channel_id": kanal.id}
                    return data
                await verif_cog.store.update(mutate)
            except Exception:
                pass

    async def save_clanlog(self, channel_id: int, guild_id: int) -> None:
        gid = str(guild_id)
        def mutate(data):
            data[gid] = {"channel_id": channel_id}
            return data
        await self.cog.clanlog_store.update(mutate)

    async def save_adminlog(self, channel_id: int, guild_id: int) -> None:
        gid = str(guild_id)
        def mutate(data):
            data.setdefault(gid, {})["log_channel_id"] = channel_id
            return data
        await self.cog.logging_store.update(mutate)
        # Cache des LoggingCog invalidieren
        logging_cog = self.cog.bot.get_cog("LoggingCog")
        if logging_cog:
            logging_cog._invalidate(guild_id)

    async def save_stream(self, channel_id: int, role_id: int, guild_id: int) -> None:
        gid = str(guild_id)
        def mutate(data):
            existing = data.get(gid, {})
            data[gid] = {
                "channel_id":  channel_id,
                "role_id":     role_id,
                "all_members": existing.get("all_members", False),
                "whitelist":   existing.get("whitelist", []),
            }
            return data
        await self.cog.stream_store.update(mutate)

    async def save_autorole(self, role_id: int, guild_id: int) -> None:
        gid = str(guild_id)
        def mutate(data):
            data.setdefault(gid, {})["autorole_id"] = role_id
            return data
        await self.cog.roles_store.update(mutate)
        # Cache des Roles-Cog invalidieren
        roles_cog = self.cog.bot.get_cog("Roles")
        if roles_cog:
            roles_cog._invalidate(guild_id)


    async def save_perms(self, gruppe: str, role_ids: list[int], guild_id: int) -> None:
        await save_group_roles(guild_id, gruppe, role_ids)


# ── Berechtigungs-Step-View ────────────────────────────────────────────────────

class SetupPermsView(discord.ui.View):
    """
    Schritt 7: Rollen-Berechtigungen.
    Für jede Command-Gruppe (Moderation, Utility, Promotion, Clan) wählt der Admin
    welche Rollen diese Gruppe nutzen dürfen. Gruppe für Gruppe, je ein View.
    """

    def __init__(self, wizard: "SetupWizard", perm_group_index: int):
        super().__init__(timeout=120)
        self.wizard            = wizard
        self.perm_group_index  = perm_group_index

        gkey, glabel, gcmds = PERM_GROUPS[perm_group_index]
        self.gkey   = gkey
        self.glabel = glabel

        self.rolle_select = discord.ui.RoleSelect(
            placeholder=f"{glabel} — Erlaubte Rollen wählen …",
            min_values=0,
            max_values=25,
            row=0,
        )
        self.rolle_select.callback = self._noop
        self.add_item(self.rolle_select)

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="✅ Speichern & Weiter", style=discord.ButtonStyle.success, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_ids = [r.id for r in self.rolle_select.values]
        if role_ids:
            await self.wizard.save_perms(self.gkey, role_ids, interaction.guild_id)
            role_mentions = ", ".join(r.mention for r in self.rolle_select.values)
            self.wizard.results[f"perm_{self.gkey}"] = role_mentions

        next_idx = self.perm_group_index + 1
        if next_idx < len(PERM_GROUPS):
            # Nächste Gruppe zeigen
            _, label, emoji, color, desc = STEPS[STEPS.index(next(s for s in STEPS if s[0] == "perms"))]
            gkey2, glabel2, gcmds2 = PERM_GROUPS[next_idx]
            embed = discord.Embed(
                title=f"🔒  Setup — Berechtigungen ({next_idx + 1}/{len(PERM_GROUPS)})",
                description=f"**{glabel2}** — Welche Rollen dürfen folgende Commands nutzen?\n`{gcmds2}`\n\n*(0 Rollen = alle dürfen)*",
                color=discord.Color.from_rgb(52, 73, 94),
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            step_total = len(STEPS)
            step_nr    = next(i for i, s in enumerate(STEPS) if s[0] == "perms") + 1
            embed.set_footer(text=f"Setup  ·  Schritt {step_nr} / {step_total}")
            await interaction.response.edit_message(
                embed=embed,
                view=SetupPermsView(self.wizard, next_idx),
            )
        else:
            # Alle Gruppen durch → Wizard-Advance
            await self.wizard.advance(interaction)

    @discord.ui.button(label="⏭️ Überspringen", style=discord.ButtonStyle.secondary, row=1)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        next_idx = self.perm_group_index + 1
        if next_idx < len(PERM_GROUPS):
            gkey2, glabel2, gcmds2 = PERM_GROUPS[next_idx]
            embed = discord.Embed(
                title=f"🔒  Setup — Berechtigungen ({next_idx + 1}/{len(PERM_GROUPS)})",
                description=f"**{glabel2}** — Welche Rollen dürfen folgende Commands nutzen?\n`{gcmds2}`\n\n*(0 Rollen = alle dürfen)*",
                color=discord.Color.from_rgb(52, 73, 94),
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            step_total = len(STEPS)
            step_nr    = next(i for i, s in enumerate(STEPS) if s[0] == "perms") + 1
            embed.set_footer(text=f"Setup  ·  Schritt {step_nr} / {step_total}")
            await interaction.response.edit_message(
                embed=embed,
                view=SetupPermsView(self.wizard, next_idx),
            )
        else:
            await self.wizard.advance(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Cog ───────────────────────────────────────────────────────────────────────

class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        # Stores für alle konfigurierbaren Module
        self.welcome_store = JSONStore(WELCOME_CONFIG_PATH,  {})
        self.verif_store   = JSONStore(VERIF_CONFIG_PATH,    {})
        self.clanlog_store = JSONStore(CLANLOG_CONFIG_PATH,  {})
        self.logging_store = JSONStore(LOGGING_CONFIG_PATH,  {})
        self.stream_store  = JSONStore(STREAMER_CONFIG_PATH, {})
        self.roles_store   = JSONStore(ROLES_CONFIG_PATH,    {})
        # Berechtigungs-Store wird von utils/permissions.py verwaltet
        # Aktive Wizards pro User-ID (verhindert doppeltes Öffnen)
        self._active_wizards: dict[int, SetupWizard] = {}

    # ── /setup ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="setup",
        description="Geführtes Server-Setup — richtet alle wichtigen Module in einem Schritt ein.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_cmd(self, interaction: discord.Interaction):
        if interaction.user.id in self._active_wizards:
            await interaction.response.send_message(
                embed=warning_embed(
                    "⚠️ Setup läuft bereits",
                    "Du hast bereits ein aktives Setup. Schließe es erst ab oder warte bis es abläuft.",
                ),
                ephemeral=True,
            )
            return

        wizard = SetupWizard(self, interaction)
        self._active_wizards[interaction.user.id] = wizard
        await wizard.start(interaction)

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
    await bot.add_cog(Setup(bot))
