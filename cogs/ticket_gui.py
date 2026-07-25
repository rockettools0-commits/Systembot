"""
Ticket-GUI — moderne Button/Select-Oberfläche für die Verwaltung des
Ticket-Systems, als Ergänzung zum bestehenden `/ticket-setup` Command.

Aufruf:
  /ticket-gui   (Slash, nur Administratoren)

Über die GUI lassen sich verwalten:
  • Ticketpanel erstellen
  • Panel bearbeiten (Name / Bild)
  • Panel löschen
  • Kategorie ändern
  • Rolle ändern (gesperrte Rolle)
  • Ticketnachricht ändern
  • Ticket-Logs einstellen
  • Bewertungslogs einstellen
  • Supportrollen verwalten

Nutzt dieselbe Konfigurationsdatei wie cogs/tickets.py (data/tickets_config.json),
sodass beide Systeme nahtlos zusammenarbeiten — bestehende Panels bleiben
vollständig erhalten und funktionsfähig.
"""

from __future__ import annotations

import datetime
from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, blurple_embed, FOOTER_TEXT, COLOR_BLURPLE, get_footer_text

from cogs.tickets import CONFIG_PATH, TicketOpenView

# Gleiche Datei wie cogs/tickets.py — beide Cogs teilen sich dieselbe Konfiguration
_config_store = JSONStore(CONFIG_PATH, {"panels": {}})


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

async def _get_panels() -> dict:
    data = await _config_store.read()
    return data.get("panels", {})


async def _update_panel(panel_id: str, **fields) -> None:
    async def mutate(data):
        panels = data.setdefault("panels", {})
        if panel_id in panels:
            panels[panel_id].update(fields)
        return data
    await _config_store.update(mutate)


def _panel_options(panels: dict) -> list[discord.SelectOption]:
    options = []
    for panel_id, panel in panels.items():
        options.append(
            discord.SelectOption(
                label=panel.get("anzeige_name", "Unbenannt")[:100],
                description=f"Panel-ID: {panel_id}",
                value=panel_id,
            )
        )
    return options[:25]  # Discord erlaubt max. 25 Select-Optionen


# ── Basis-View mit Admin-Check ────────────────────────────────────────────────

class AdminOnlyView(discord.ui.View):
    """Stellt sicher, dass nur Administratoren mit der GUI interagieren können."""

    def __init__(self, *, timeout: float | None = 180):
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if isinstance(member, discord.Member) and member.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            embed=error_embed("❌ Keine Berechtigung", "Nur Administratoren dürfen die Ticket-GUI nutzen."),
            ephemeral=True,
        )
        return False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Generischer "Panel auswählen"-Schritt ─────────────────────────────────────

class PanelPickerView(AdminOnlyView):
    """
    Zeigt ein Dropdown mit allen vorhandenen Panels an.
    `on_pick(interaction, panel_id, panel_data)` wird nach der Auswahl aufgerufen.
    """

    def __init__(self, panels: dict, on_pick: Callable[[discord.Interaction, str, dict], Awaitable[None]]):
        super().__init__(timeout=180)
        self.panels = panels
        self.on_pick = on_pick

        self.select = discord.ui.Select(
            placeholder="🎫 Panel auswählen …",
            options=_panel_options(panels),
        )
        self.select.callback = self._callback
        self.add_item(self.select)

    async def _callback(self, interaction: discord.Interaction):
        panel_id = self.select.values[0]
        panel = self.panels.get(panel_id)
        if panel is None:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehler", "Dieses Panel existiert nicht mehr."), ephemeral=True
            )
            return
        await self.on_pick(interaction, panel_id, panel)


async def _prompt_panel_picker(
    interaction: discord.Interaction,
    title: str,
    description: str,
    on_pick: Callable[[discord.Interaction, str, dict], Awaitable[None]],
):
    panels = await _get_panels()
    if not panels:
        await interaction.response.send_message(
            embed=error_embed("❌ Keine Panels vorhanden", "Erstelle zuerst ein Panel über **➕ Panel erstellen**."),
            ephemeral=True,
        )
        return
    embed = blurple_embed(title, description)
    await interaction.response.send_message(embed=embed, view=PanelPickerView(panels, on_pick), ephemeral=True)


# ── Modals ─────────────────────────────────────────────────────────────────────

class PanelInfoModal(discord.ui.Modal):
    """Wird sowohl für 'Panel erstellen' als auch 'Panel bearbeiten' genutzt."""

    def __init__(self, title: str, *, default_name: str = "", default_image: str = "", on_submit_cb=None):
        super().__init__(title=title, timeout=300)
        self.on_submit_cb = on_submit_cb

        self.name_input = discord.ui.TextInput(
            label="Anzeigename",
            placeholder="z. B. Support, Trading, Bewerbung …",
            default=default_name or None,
            max_length=100,
        )
        self.image_input = discord.ui.TextInput(
            label="Bild-URL (optional)",
            placeholder="https://…",
            default=default_image or None,
            required=False,
            max_length=300,
        )
        self.add_item(self.name_input)
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self.on_submit_cb:
            await self.on_submit_cb(interaction, str(self.name_input.value), str(self.image_input.value))


class TicketMessageModal(discord.ui.Modal, title="💬 Ticketnachricht bearbeiten"):
    def __init__(self, panel_id: str, default_text: str = ""):
        super().__init__(timeout=300)
        self.panel_id = panel_id
        self.text_input = discord.ui.TextInput(
            label="Text im Ticket-Kanal",
            style=discord.TextStyle.paragraph,
            placeholder="Wird oben im neu erstellten Ticket-Kanal angezeigt.",
            default=default_text or None,
            required=False,
            max_length=1000,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await _update_panel(self.panel_id, ticket_message=str(self.text_input.value))
        await interaction.response.send_message(
            embed=success_embed("✅ Ticketnachricht aktualisiert", "Wird bei neuen Tickets dieses Panels verwendet."),
            ephemeral=True,
        )


# ── Panel erstellen ────────────────────────────────────────────────────────────

class CreatePanelConfigView(AdminOnlyView):
    """Zweiter Schritt beim Panel erstellen: Kanal, Kategorie, gesperrte Rolle, Log-Kanal."""

    def __init__(self, anzeige_name: str, bild_url: str):
        super().__init__(timeout=300)
        self.anzeige_name = anzeige_name
        self.bild_url = bild_url

        self.kanal_select = discord.ui.ChannelSelect(
            placeholder="📨 Kanal für das Panel wählen …",
            channel_types=[discord.ChannelType.text],
            row=0,
        )
        self.kategorie_select = discord.ui.ChannelSelect(
            placeholder="📁 Zielkategorie für neue Tickets wählen …",
            channel_types=[discord.ChannelType.category],
            row=1,
        )
        self.rolle_select = discord.ui.RoleSelect(
            placeholder="🚫 Gesperrte Rolle wählen (optional) …",
            min_values=0,
            row=2,
        )
        self.log_select = discord.ui.ChannelSelect(
            placeholder="📜 Ticket-Log-Kanal wählen …",
            channel_types=[discord.ChannelType.text],
            row=3,
        )
        for sel in (self.kanal_select, self.kategorie_select, self.rolle_select, self.log_select):
            sel.callback = self._noop
            self.add_item(sel)

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="✅ Panel erstellen", style=discord.ButtonStyle.success, row=4)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.kanal_select.values or not self.kategorie_select.values or not self.log_select.values:
            await interaction.response.send_message(
                embed=error_embed("❌ Pflichtfelder fehlen", "Bitte wähle Kanal, Kategorie und Log-Kanal aus."),
                ephemeral=True,
            )
            return

        kanal: discord.TextChannel = self.kanal_select.values[0]
        kategorie: discord.CategoryChannel = self.kategorie_select.values[0]
        log_kanal: discord.TextChannel = self.log_select.values[0]
        gesperrte_rolle = self.rolle_select.values[0] if self.rolle_select.values else None

        embed = discord.Embed(
            title=f"🎫  {self.anzeige_name}",
            description=(
                f"Klicke auf den Button unten, um ein **{self.anzeige_name}**-Ticket zu eröffnen.\n"
                f"Ein Teammitglied wird sich schnellstmöglich um dich kümmern.\n\n"
                f"⚡ Tickets werden automatisch als Transkript gespeichert."
            ),
            color=COLOR_BLURPLE,
        )
        if self.bild_url and self.bild_url.startswith("http"):
            embed.set_thumbnail(url=self.bild_url)
        embed.set_footer(text=get_footer_text(interaction))

        try:
            sent_message = await kanal.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehler", f"Ich habe keine Berechtigung, in {kanal.mention} zu senden."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("❌ Fehler", str(e)), ephemeral=True)
            return

        panel_id = str(sent_message.id)
        panel_data = {
            "channel_id": kanal.id,
            "message_id": sent_message.id,
            "kategorie_id": kategorie.id,
            "anzeige_name": self.anzeige_name,
            "bild_url": self.bild_url,
            "gesperrte_rolle_id": gesperrte_rolle.id if gesperrte_rolle else None,
            "log_kanal_id": log_kanal.id,
            "rating_log_kanal_id": log_kanal.id,
            "ticket_message": "",
            "support_role_ids": [],
        }

        async def mutate(data):
            data.setdefault("panels", {})[panel_id] = panel_data
            return data

        await _config_store.update(mutate)

        view = TicketOpenView(panel_id)
        interaction.client.add_view(view)
        await sent_message.edit(view=view)

        await interaction.response.send_message(
            embed=success_embed("✅ Panel erstellt", f"**{self.anzeige_name}** wurde in {kanal.mention} erstellt."),
            ephemeral=True,
        )
        self.stop()


# ── Hauptmenü ──────────────────────────────────────────────────────────────────

class TicketGuiMainView(AdminOnlyView):
    def __init__(self):
        super().__init__(timeout=180)

    # Reihe 0 — Panel-Verwaltung
    @discord.ui.button(label="➕ Panel erstellen", style=discord.ButtonStyle.success, row=0)
    async def create_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_submit(inter: discord.Interaction, name: str, image: str):
            if not name.strip():
                await inter.response.send_message(
                    embed=error_embed("❌ Fehler", "Der Anzeigename darf nicht leer sein."), ephemeral=True
                )
                return
            await inter.response.send_message(
                embed=blurple_embed("📁 Panel-Konfiguration", "Wähle nun Kanal, Kategorie, gesperrte Rolle und Log-Kanal."),
                view=CreatePanelConfigView(name.strip(), image.strip()),
                ephemeral=True,
            )

        await interaction.response.send_modal(PanelInfoModal("➕ Neues Ticketpanel", on_submit_cb=on_submit))

    @discord.ui.button(label="✏️ Panel bearbeiten", style=discord.ButtonStyle.primary, row=0)
    async def edit_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_pick(inter: discord.Interaction, panel_id: str, panel: dict):
            async def on_submit(inter2: discord.Interaction, name: str, image: str):
                await _update_panel(panel_id, anzeige_name=name.strip(), bild_url=image.strip())
                # Versuchen, die bereits geposteten Panel-Nachricht mit zu aktualisieren
                try:
                    channel = inter2.guild.get_channel(panel["channel_id"])
                    if channel is not None:
                        msg = await channel.fetch_message(panel["message_id"])
                        embed = msg.embeds[0] if msg.embeds else discord.Embed()
                        embed.title = f"🎫  {name.strip()}"
                        if image.strip().startswith("http"):
                            embed.set_thumbnail(url=image.strip())
                        await msg.edit(embed=embed)
                except (discord.NotFound, discord.HTTPException, IndexError):
                    pass
                await inter2.response.send_message(
                    embed=success_embed("✅ Panel aktualisiert", f"**{name.strip()}** wurde gespeichert."),
                    ephemeral=True,
                )

            await inter.response.send_modal(
                PanelInfoModal(
                    "✏️ Panel bearbeiten",
                    default_name=panel.get("anzeige_name", ""),
                    default_image=panel.get("bild_url", ""),
                    on_submit_cb=on_submit,
                )
            )

        await _prompt_panel_picker(interaction, "✏️ Panel bearbeiten", "Wähle das Panel, das du bearbeiten möchtest.", on_pick)

    @discord.ui.button(label="🗑️ Panel löschen", style=discord.ButtonStyle.danger, row=0)
    async def delete_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_pick(inter: discord.Interaction, panel_id: str, panel: dict):
            async def mutate(data):
                data.get("panels", {}).pop(panel_id, None)
                return data
            await _config_store.update(mutate)

            # Panel-Nachricht ebenfalls entfernen, wenn möglich
            try:
                channel = inter.guild.get_channel(panel["channel_id"])
                if channel is not None:
                    msg = await channel.fetch_message(panel["message_id"])
                    await msg.delete()
            except (discord.NotFound, discord.HTTPException):
                pass

            await inter.response.send_message(
                embed=success_embed("✅ Panel gelöscht", f"**{panel.get('anzeige_name')}** wurde entfernt."),
                ephemeral=True,
            )

        await _prompt_panel_picker(interaction, "🗑️ Panel löschen", "Wähle das Panel, das du löschen möchtest.\n⚠️ Dies kann nicht rückgängig gemacht werden.", on_pick)

    # Reihe 1 — Einstellungen pro Panel
    @discord.ui.button(label="📁 Kategorie ändern", style=discord.ButtonStyle.secondary, row=1)
    async def change_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_pick(inter: discord.Interaction, panel_id: str, panel: dict):
            view = AdminOnlyView(timeout=180)
            select = discord.ui.ChannelSelect(
                placeholder="📁 Neue Kategorie wählen …", channel_types=[discord.ChannelType.category]
            )

            async def cb(inter2: discord.Interaction):
                await _update_panel(panel_id, kategorie_id=select.values[0].id)
                await inter2.response.edit_message(
                    embed=success_embed("✅ Kategorie geändert", f"Neue Kategorie: {select.values[0].mention}"),
                    view=None,
                )

            select.callback = cb
            view.add_item(select)
            await inter.response.send_message(embed=info_embed("📁 Kategorie ändern", f"Panel: **{panel.get('anzeige_name')}**"), view=view, ephemeral=True)

        await _prompt_panel_picker(interaction, "📁 Kategorie ändern", "Wähle zuerst das Panel aus.", on_pick)

    @discord.ui.button(label="🔒 Rolle ändern", style=discord.ButtonStyle.secondary, row=1)
    async def change_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_pick(inter: discord.Interaction, panel_id: str, panel: dict):
            view = AdminOnlyView(timeout=180)
            select = discord.ui.RoleSelect(placeholder="🚫 Neue gesperrte Rolle wählen …", min_values=0)

            async def cb(inter2: discord.Interaction):
                role_id = select.values[0].id if select.values else None
                await _update_panel(panel_id, gesperrte_rolle_id=role_id)
                label = select.values[0].mention if select.values else "Keine"
                await inter2.response.edit_message(
                    embed=success_embed("✅ Rolle geändert", f"Gesperrte Rolle: {label}"), view=None
                )

            select.callback = cb
            view.add_item(select)
            await inter.response.send_message(
                embed=info_embed("🔒 Gesperrte Rolle ändern", f"Panel: **{panel.get('anzeige_name')}**\n\nMitglieder mit dieser Rolle können in diesem Panel **kein** Ticket öffnen."),
                view=view, ephemeral=True,
            )

        await _prompt_panel_picker(interaction, "🔒 Rolle ändern", "Wähle zuerst das Panel aus.", on_pick)

    @discord.ui.button(label="💬 Ticketnachricht", style=discord.ButtonStyle.secondary, row=1)
    async def change_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_pick(inter: discord.Interaction, panel_id: str, panel: dict):
            await inter.response.send_modal(
                TicketMessageModal(panel_id, default_text=panel.get("ticket_message", ""))
            )

        await _prompt_panel_picker(interaction, "💬 Ticketnachricht ändern", "Wähle zuerst das Panel aus.", on_pick)

    # Reihe 2 — Logs & Supportrollen
    @discord.ui.button(label="📜 Ticket-Logs", style=discord.ButtonStyle.secondary, row=2)
    async def change_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_pick(inter: discord.Interaction, panel_id: str, panel: dict):
            view = AdminOnlyView(timeout=180)
            select = discord.ui.ChannelSelect(
                placeholder="📜 Neuen Ticket-Log-Kanal wählen …", channel_types=[discord.ChannelType.text]
            )

            async def cb(inter2: discord.Interaction):
                await _update_panel(panel_id, log_kanal_id=select.values[0].id)
                await inter2.response.edit_message(
                    embed=success_embed("✅ Ticket-Log-Kanal geändert", f"Neuer Kanal: {select.values[0].mention}"),
                    view=None,
                )

            select.callback = cb
            view.add_item(select)
            await inter.response.send_message(embed=info_embed("📜 Ticket-Logs einstellen", f"Panel: **{panel.get('anzeige_name')}**"), view=view, ephemeral=True)

        await _prompt_panel_picker(interaction, "📜 Ticket-Logs einstellen", "Wähle zuerst das Panel aus.", on_pick)

    @discord.ui.button(label="⭐ Bewertungslogs", style=discord.ButtonStyle.secondary, row=2)
    async def change_rating_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_pick(inter: discord.Interaction, panel_id: str, panel: dict):
            view = AdminOnlyView(timeout=180)
            select = discord.ui.ChannelSelect(
                placeholder="⭐ Neuen Bewertungs-Log-Kanal wählen …", channel_types=[discord.ChannelType.text]
            )

            async def cb(inter2: discord.Interaction):
                await _update_panel(panel_id, rating_log_kanal_id=select.values[0].id)
                await inter2.response.edit_message(
                    embed=success_embed("✅ Bewertungs-Log-Kanal geändert", f"Neuer Kanal: {select.values[0].mention}"),
                    view=None,
                )

            select.callback = cb
            view.add_item(select)
            await inter.response.send_message(
                embed=info_embed("⭐ Bewertungslogs einstellen", f"Panel: **{panel.get('anzeige_name')}**\n\nHier werden abgegebene Ticketbewertungen automatisch gepostet."),
                view=view, ephemeral=True,
            )

        await _prompt_panel_picker(interaction, "⭐ Bewertungslogs einstellen", "Wähle zuerst das Panel aus.", on_pick)

    @discord.ui.button(label="🛠️ Supportrollen", style=discord.ButtonStyle.secondary, row=2)
    async def change_support_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def on_pick(inter: discord.Interaction, panel_id: str, panel: dict):
            view = AdminOnlyView(timeout=180)
            select = discord.ui.RoleSelect(
                placeholder="🛠️ Supportrollen wählen (mehrere möglich) …", min_values=0, max_values=10
            )

            async def cb(inter2: discord.Interaction):
                role_ids = [r.id for r in select.values]
                await _update_panel(panel_id, support_role_ids=role_ids)
                labels = ", ".join(r.mention for r in select.values) if select.values else "Keine"
                await inter2.response.edit_message(
                    embed=success_embed("✅ Supportrollen aktualisiert", f"Supportrollen: {labels}"), view=None
                )

            select.callback = cb
            view.add_item(select)
            await inter.response.send_message(
                embed=info_embed("🛠️ Supportrollen verwalten", f"Panel: **{panel.get('anzeige_name')}**\n\nDiese Rollen erhalten automatisch Zugriff auf neue Ticket-Kanäle dieses Panels."),
                view=view, ephemeral=True,
            )

        await _prompt_panel_picker(interaction, "🛠️ Supportrollen verwalten", "Wähle zuerst das Panel aus.", on_pick)


def _main_menu_embed(panel_count: int, guild=None) -> discord.Embed:
    embed = discord.Embed(
        title="🎫  Ticket-GUI — Verwaltung",
        description=(
            "Verwalte das komplette Ticket-System bequem über Buttons & Menüs — "
            "ganz ohne komplizierte Commands.\n\n"
            f"📊 Aktuell konfigurierte Panels: **{panel_count}**"
        ),
        color=COLOR_BLURPLE,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.set_footer(text=get_footer_text(guild))
    return embed


# TicketGui Cog wurde entfernt — /ticket gui läuft jetzt als Subcommand
# im /ticket-Gruppe in cogs/tickets.py, damit Discord keine zwei
# konkurrierende "ticket"-Einträge im Command-Tree hat.

async def setup(bot: commands.Bot):
    pass  # Views + Helpers werden von cogs/tickets.py importiert
