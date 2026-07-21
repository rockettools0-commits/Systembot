"""Administrator-GUI fuer Ticketpanels; erweitert das bestehende JSON-Format."""

import discord
from discord import app_commands
from discord.ext import commands

from cogs.tickets import TicketOpenView


def panel_embed(name: str, text: str, image_url: str) -> discord.Embed:
    embed = discord.Embed(title=f"Ticket: {name}", description=text or f"Oeffne hier ein {name}-Ticket.", color=discord.Color.blurple())
    if image_url.startswith("http"):
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text="Ticket-System")
    return embed


class PanelModal(discord.ui.Modal, title="Ticketpanel konfigurieren"):
    channel_id = discord.ui.TextInput(label="Panel-Kanal-ID")
    category_id = discord.ui.TextInput(label="Ticket-Kategorie-ID")
    name = discord.ui.TextInput(label="Panelname")
    log_channel_id = discord.ui.TextInput(label="Ticket-Log-Kanal-ID")
    options = discord.ui.TextInput(label="Sperrrolle-ID | Bild-URL (optional)", required=False)

    def __init__(self, cog: "TicketGUI", panel_id: str | None = None, existing: dict | None = None):
        super().__init__()
        self.cog, self.panel_id = cog, panel_id
        if panel_id:
            self.title = "Ticketpanel bearbeiten"
        if existing:
            self.channel_id.default = str(existing.get("channel_id", ""))
            self.category_id.default = str(existing.get("kategorie_id", ""))
            self.name.default = existing.get("anzeige_name", "")
            self.log_channel_id.default = str(existing.get("log_kanal_id", ""))
            self.options.default = " | ".join(filter(None, [
                str(existing.get("gesperrte_rolle_id", "") or ""), existing.get("bild_url", "")
            ]))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id, category_id, log_id = int(self.channel_id.value), int(self.category_id.value), int(self.log_channel_id.value)
        except ValueError:
            await interaction.response.send_message("Kanal- und Kategorie-IDs muessen Zahlen sein.", ephemeral=True)
            return
        guild = interaction.guild
        channel, category, log_channel = guild.get_channel(channel_id), guild.get_channel(category_id), guild.get_channel(log_id)
        if not isinstance(channel, discord.TextChannel) or not isinstance(category, discord.CategoryChannel) or not isinstance(log_channel, discord.TextChannel):
            await interaction.response.send_message("Mindestens eine Kanal-ID ist ungueltig.", ephemeral=True)
            return
        raw = self.options.value.strip()
        blocked_role, image = 0, ""
        if raw:
            for part in raw.split("|"):
                part = part.strip()
                if part.isdigit():
                    blocked_role = int(part)
                elif part.startswith("http"):
                    image = part
        config = await self.cog.tickets.config_store.read()
        if self.panel_id:
            panel = config["panels"].get(self.panel_id)
            if panel is None:
                await interaction.response.send_message("Panel wurde nicht gefunden.", ephemeral=True)
                return
            panel.update({"channel_id": channel_id, "kategorie_id": category_id, "anzeige_name": self.name.value,
                          "log_kanal_id": log_id, "rating_log_kanal_id": log_id, "gesperrte_rolle_id": blocked_role,
                          "bild_url": image})
            await self.cog.tickets.config_store.write(config)
            await interaction.response.send_message("Panel-Konfiguration gespeichert.", ephemeral=True)
            return
        message = await channel.send(embed=panel_embed(self.name.value, "", image))
        new_id = str(message.id)
        config.setdefault("panels", {})[new_id] = {"channel_id": channel_id, "message_id": message.id, "kategorie_id": category_id,
            "anzeige_name": self.name.value, "bild_url": image, "gesperrte_rolle_id": blocked_role,
            "log_kanal_id": log_id, "rating_log_kanal_id": log_id}
        await self.cog.tickets.config_store.write(config)
        view = TicketOpenView(new_id)
        self.cog.bot.add_view(view)
        await message.edit(view=view)
        await interaction.response.send_message(f"Panel **{self.name.value}** erstellt.", ephemeral=True)


class PanelSelect(discord.ui.Select):
    def __init__(self, cog: "TicketGUI", mode: str):
        self.cog, self.mode = cog, mode
        super().__init__(placeholder="Ticketpanel waehlen", options=[])

    async def refresh(self):
        panels = (await self.cog.tickets.config_store.read()).get("panels", {})
        self.options = [discord.SelectOption(label=data.get("anzeige_name", panel_id)[:100], value=panel_id)
                        for panel_id, data in list(panels.items())[:25]] or [discord.SelectOption(label="Keine Panels", value="none")]

    async def callback(self, interaction: discord.Interaction):
        panel_id = self.values[0]
        if panel_id == "none":
            await interaction.response.send_message("Keine Panels vorhanden.", ephemeral=True)
            return
        if self.mode == "edit":
            config = await self.cog.tickets.config_store.read()
            await interaction.response.send_modal(PanelModal(self.cog, panel_id, config["panels"].get(panel_id)))
            return
        if self.mode == "message":
            await interaction.response.send_modal(PanelTextModal(self.cog, panel_id))
            return
        if self.mode == "logs":
            await interaction.response.send_modal(PanelLogModal(self.cog, panel_id))
            return
        config = await self.cog.tickets.config_store.read()
        panel = config["panels"].pop(panel_id, None)
        await self.cog.tickets.config_store.write(config)
        if panel:
            channel = interaction.guild.get_channel(panel["channel_id"])
            if channel:
                try:
                    message = await channel.fetch_message(panel["message_id"])
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
        await interaction.response.send_message("Panel geloescht.", ephemeral=True)


class PanelSelectView(discord.ui.View):
    def __init__(self, cog: "TicketGUI", mode: str):
        super().__init__(timeout=120)
        self.select = PanelSelect(cog, mode)
        self.add_item(self.select)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.administrator

    async def on_timeout(self):
        self.stop()


class SupportRoleModal(discord.ui.Modal, title="Supportrollen verwalten"):
    role_ids = discord.ui.TextInput(label="Rollen-IDs (mit Komma getrennt)", required=False)

    def __init__(self, cog: "TicketGUI"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            roles = [int(value.strip()) for value in self.role_ids.value.split(",") if value.strip()]
        except ValueError:
            await interaction.response.send_message("Bitte nur Rollen-IDs durch Kommas getrennt angeben.", ephemeral=True)
            return
        config = await self.cog.tickets.config_store.read()
        config["support_role_ids"] = roles
        await self.cog.tickets.config_store.write(config)
        await interaction.response.send_message(f"{len(roles)} Supportrolle(n) gespeichert.", ephemeral=True)


class PanelTextModal(discord.ui.Modal, title="Ticketnachricht bearbeiten"):
    text = discord.ui.TextInput(label="Nachricht im neuen Ticket", style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, cog: "TicketGUI", panel_id: str):
        super().__init__()
        self.cog, self.panel_id = cog, panel_id

    async def on_submit(self, interaction: discord.Interaction):
        config = await self.cog.tickets.config_store.read()
        config["panels"][self.panel_id]["panel_text"] = self.text.value
        await self.cog.tickets.config_store.write(config)
        await interaction.response.send_message("Ticketnachricht gespeichert.", ephemeral=True)


class PanelLogModal(discord.ui.Modal, title="Log-Kanaele bearbeiten"):
    ticket_log = discord.ui.TextInput(label="Ticket-Log-Kanal-ID")
    rating_log = discord.ui.TextInput(label="Bewertungs-Log-Kanal-ID")

    def __init__(self, cog: "TicketGUI", panel_id: str):
        super().__init__()
        self.cog, self.panel_id = cog, panel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            ticket_log, rating_log = int(self.ticket_log.value), int(self.rating_log.value)
        except ValueError:
            await interaction.response.send_message("Bitte zwei gueltige Kanal-IDs angeben.", ephemeral=True)
            return
        config = await self.cog.tickets.config_store.read()
        panel = config["panels"][self.panel_id]
        panel["log_kanal_id"], panel["rating_log_kanal_id"] = ticket_log, rating_log
        await self.cog.tickets.config_store.write(config)
        await interaction.response.send_message("Log-Kanaele gespeichert.", ephemeral=True)


class TicketAdminView(discord.ui.View):
    def __init__(self, cog: "TicketGUI"):
        super().__init__(timeout=600)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("Keine Berechtigung.", ephemeral=True)
        return False

    @discord.ui.button(label="Panel erstellen", style=discord.ButtonStyle.green)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PanelModal(self.cog))

    @discord.ui.button(label="Panel bearbeiten", style=discord.ButtonStyle.blurple)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = PanelSelectView(self.cog, "edit")
        await view.select.refresh()
        await interaction.response.send_message("Panel auswaehlen:", view=view, ephemeral=True)

    @discord.ui.button(label="Panel loeschen", style=discord.ButtonStyle.red)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = PanelSelectView(self.cog, "delete")
        await view.select.refresh()
        await interaction.response.send_message("Zu loeschendes Panel auswaehlen:", view=view, ephemeral=True)

    @discord.ui.button(label="Supportrollen", style=discord.ButtonStyle.secondary)
    async def support_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SupportRoleModal(self.cog))

    @discord.ui.button(label="Ticketnachricht", style=discord.ButtonStyle.secondary)
    async def ticket_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = PanelSelectView(self.cog, "message")
        await view.select.refresh()
        await interaction.response.send_message("Panel auswaehlen:", view=view, ephemeral=True)

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.secondary)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = PanelSelectView(self.cog, "logs")
        await view.select.refresh()
        await interaction.response.send_message("Panel auswaehlen:", view=view, ephemeral=True)


class TicketGUI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tickets = bot.get_cog("Tickets")

    @app_commands.command(name="ticket-gui", description="Oeffnet die Ticket-Verwaltung.")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_gui(self, interaction: discord.Interaction):
        if self.tickets is None:
            await interaction.response.send_message("Ticket-System ist nicht geladen.", ephemeral=True)
            return
        embed = discord.Embed(title="Ticket-Verwaltung", description="Panels, Kategorien, Rollen und Log-Kanaele verwalten.", color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, view=TicketAdminView(self), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketGUI(bot))
