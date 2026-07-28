"""Community-Werkzeuge: Vorschläge, Meldungen, Events, Geburtstage, Reputation und Sticky-Nachrichten."""
from __future__ import annotations

import datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.storage import JSONStore
from utils.theme import error_embed, info_embed, success_embed, warning_embed

CONFIG = "data/community_config.json"
SUGGESTIONS = "data/suggestions.json"
EVENTS = "data/events.json"
BIRTHDAYS = "data/birthdays.json"
REPUTATION = "data/reputation.json"


class ReactionRoleView(discord.ui.View):
    def __init__(self, role_id: int):
        super().__init__(timeout=None)
        self.role_id = role_id
        self.button.custom_id = f"community:role:{role_id}"

    @discord.ui.button(label="Rolle erhalten", emoji="✨", style=discord.ButtonStyle.primary)
    async def button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        role = interaction.guild.get_role(self.role_id) if interaction.guild else None
        if not role or role >= interaction.guild.me.top_role:
            await interaction.response.send_message(embed=error_embed("❌ Rolle nicht verfügbar"), ephemeral=True)
            return
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Reaction Role")
                text = "Rolle entfernt."
            else:
                await interaction.user.add_roles(role, reason="Reaction Role")
                text = "Rolle vergeben."
            await interaction.response.send_message(embed=success_embed("✨ Rollen-Auswahl", text), ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(embed=error_embed("❌ Fehlende Bot-Berechtigung"), ephemeral=True)


class Community(commands.Cog):
    community = app_commands.Group(name="community", description="Vorschläge, Meldungen und Mitglieder-Features.")
    event = app_commands.Group(name="event", description="Events planen und verwalten.")
    sticky = app_commands.Group(name="sticky", description="Sticky-Nachrichten verwalten.")
    reputation = app_commands.Group(name="reputation", description="Community-Reputation.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = JSONStore(CONFIG, {})
        self.suggestions = JSONStore(SUGGESTIONS, {})
        self.events = JSONStore(EVENTS, {})
        self.birthdays = JSONStore(BIRTHDAYS, {})
        self.reputation_store = JSONStore(REPUTATION, {})
        self._sticky_busy: set[int] = set()

    async def cog_load(self) -> None:
        config = await self.config.read()
        for guild in config.values():
            for entry in guild.get("reaction_roles", []):
                self.bot.add_view(ReactionRoleView(entry["role_id"]))
        self.event_reminders.start()
        self.birthday_greetings.start()

    def cog_unload(self) -> None:
        self.event_reminders.cancel()
        self.birthday_greetings.cancel()

    @community.command(name="reactionrole", description="Erstellt einen Button, über den Mitglieder eine Rolle wählen.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def reactionrole_setup(self, interaction: discord.Interaction, kanal: discord.TextChannel, rolle: discord.Role, titel: app_commands.Range[str, 1, 100] = "Rollen-Auswahl") -> None:
        if rolle >= interaction.guild.me.top_role:
            await interaction.response.send_message(embed=error_embed("❌ Rollen-Hierarchie", "Die Rolle muss unter meiner höchsten Rolle liegen."), ephemeral=True)
            return
        view = ReactionRoleView(rolle.id)
        message = await kanal.send(embed=info_embed(f"✨ {titel}", f"Klicke auf den Button, um {rolle.mention} ein- oder auszuschalten."), view=view)
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            entries = data.setdefault(str(interaction.guild.id), {}).setdefault("reaction_roles", [])
            entries.append({"message_id": message.id, "channel_id": kanal.id, "role_id": rolle.id})
            return data
        await self.config.update(mutate)
        self.bot.add_view(view)
        await interaction.response.send_message(embed=success_embed("✅ Rollen-Button erstellt", kanal.mention), ephemeral=True)

    @community.command(name="suggestion", description="Reiche einen Vorschlag für den Server ein.")
    async def suggestion(self, interaction: discord.Interaction, text: app_commands.Range[str, 5, 1000]) -> None:
        guild_id = str(interaction.guild.id)
        config = await self.config.read()
        channel_id = config.get(guild_id, {}).get("suggestion_channel_id")
        channel = interaction.guild.get_channel(channel_id) if channel_id else interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(embed=error_embed("❌ Vorschlagskanal nicht verfügbar"), ephemeral=True)
            return
        entry: dict[str, Any] = {"user_id": interaction.user.id, "text": text, "status": "offen", "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        embed = info_embed("💡 Neuer Vorschlag", text)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        message = await channel.send(embed=embed)
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault(guild_id, {})[str(message.id)] = entry
            return data
        await self.suggestions.update(mutate)
        await interaction.response.send_message(embed=success_embed("✅ Vorschlag eingereicht", f"Dein Vorschlag wurde in {channel.mention} veröffentlicht."), ephemeral=True)

    @community.command(name="suggestion-setup", description="Setzt den Kanal für Community-Vorschläge.")
    @app_commands.checks.has_permissions(administrator=True)
    async def suggestion_setup(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        await self._set_config(interaction.guild.id, "suggestion_channel_id", kanal.id)
        await interaction.response.send_message(embed=success_embed("✅ Vorschlagskanal gesetzt", kanal.mention), ephemeral=True)

    @community.command(name="report", description="Melde einen Nutzer vertraulich an das Team.")
    async def report(self, interaction: discord.Interaction, nutzer: discord.Member, grund: app_commands.Range[str, 3, 1000]) -> None:
        config = await self.config.read()
        channel_id = config.get(str(interaction.guild.id), {}).get("report_channel_id")
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(embed=error_embed("❌ Meldesystem nicht eingerichtet", "Ein Admin muss zuerst `/report-setup` ausführen."), ephemeral=True)
            return
        embed = warning_embed("🚩 Vertrauliche Meldung", f"**Gemeldete Person:** {nutzer.mention}\n**Von:** {interaction.user.mention}\n**Grund:** {grund}\n**Kanal:** {interaction.channel.mention}")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message(embed=success_embed("✅ Meldung gesendet", "Das Team wurde vertraulich informiert."), ephemeral=True)

    @community.command(name="report-setup", description="Setzt den privaten Kanal für Meldungen.")
    @app_commands.checks.has_permissions(administrator=True)
    async def report_setup(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        await self._set_config(interaction.guild.id, "report_channel_id", kanal.id)
        await interaction.response.send_message(embed=success_embed("✅ Meldesystem eingerichtet", kanal.mention), ephemeral=True)

    @event.command(name="create", description="Erstellt ein Event mit automatischer Erinnerung.")
    @app_commands.checks.has_permissions(manage_events=True)
    async def event_create(self, interaction: discord.Interaction, titel: app_commands.Range[str, 3, 100], zeit_utc: str, kanal: discord.TextChannel, beschreibung: app_commands.Range[str, 1, 1000]) -> None:
        try:
            when = datetime.datetime.fromisoformat(zeit_utc.replace("Z", "+00:00"))
            when = when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("❌ Ungültige Zeit", "Nutze ISO-Format, z. B. `2026-08-01T18:00+02:00`."), ephemeral=True)
            return
        if when <= datetime.datetime.now(datetime.timezone.utc):
            await interaction.response.send_message(embed=error_embed("❌ Zeitpunkt liegt in der Vergangenheit"), ephemeral=True)
            return
        embed = info_embed(f"📅 {titel}", f"{beschreibung}\n\n**Start:** <t:{int(when.timestamp())}:F>\n**In:** <t:{int(when.timestamp())}:R>")
        message = await kanal.send(embed=embed)
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault(str(interaction.guild.id), {})[str(message.id)] = {"title": titel, "when": when.isoformat(), "channel_id": kanal.id, "reminded": False}
            return data
        await self.events.update(mutate)
        await interaction.response.send_message(embed=success_embed("✅ Event erstellt", f"{titel} wurde in {kanal.mention} angekündigt."), ephemeral=True)

    @tasks.loop(minutes=1)
    async def event_reminders(self) -> None:
        data = await self.events.read()
        now = datetime.datetime.now(datetime.timezone.utc)
        changed = False
        for gid, items in data.items():
            guild = self.bot.get_guild(int(gid))
            if not guild: continue
            for event in items.values():
                when = datetime.datetime.fromisoformat(event["when"])
                if not event.get("reminded") and datetime.timedelta() <= when - now <= datetime.timedelta(minutes=15):
                    channel = guild.get_channel(event["channel_id"])
                    if isinstance(channel, discord.TextChannel):
                        await channel.send(f"⏰ **{event['title']}** startet in weniger als 15 Minuten!")
                    event["reminded"], changed = True, True
        if changed: await self.events.write(data)

    @event_reminders.before_loop
    async def before_event_reminders(self) -> None:
        await self.bot.wait_until_ready()

    @community.command(name="birthday", description="Speichere deinen Geburtstag für automatische Glückwünsche.")
    async def birthday(self, interaction: discord.Interaction, tag: app_commands.Range[int, 1, 31], monat: app_commands.Range[int, 1, 12]) -> None:
        try: datetime.date(2024, monat, tag)
        except ValueError:
            await interaction.response.send_message(embed=error_embed("❌ Ungültiges Datum"), ephemeral=True); return
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault(str(interaction.guild.id), {})[str(interaction.user.id)] = {"day": tag, "month": monat}
            return data
        await self.birthdays.update(mutate)
        await interaction.response.send_message(embed=success_embed("🎂 Geburtstag gespeichert", f"{tag:02d}.{monat:02d}."), ephemeral=True)

    @community.command(name="birthday-setup", description="Setzt den Kanal für automatische Geburtstagsgrüße.")
    @app_commands.checks.has_permissions(administrator=True)
    async def birthday_setup(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        await self._set_config(interaction.guild.id, "birthday_channel_id", kanal.id)
        await interaction.response.send_message(embed=success_embed("🎂 Geburtstagskanal gesetzt", kanal.mention), ephemeral=True)

    @tasks.loop(hours=1)
    async def birthday_greetings(self) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        data, config = await self.birthdays.read(), await self.config.read()
        for guild_id, members in data.items():
            guild = self.bot.get_guild(int(guild_id))
            channel = guild.get_channel(config.get(guild_id, {}).get("birthday_channel_id")) if guild else None
            if not isinstance(channel, discord.TextChannel):
                continue
            for user_id, birthday in members.items():
                if birthday.get("day") != now.day or birthday.get("month") != now.month:
                    continue
                key = f"last_birthday_{user_id}"
                if config.get(guild_id, {}).get(key) == now.date().isoformat():
                    continue
                await channel.send(f"🎉 Alles Gute zum Geburtstag, <@{user_id}>!", allowed_mentions=discord.AllowedMentions(users=True))
                def mutate(current: dict[str, Any], gid=guild_id, field=key, date=now.date().isoformat()) -> dict[str, Any]:
                    current.setdefault(gid, {})[field] = date
                    return current
                await self.config.update(mutate)
                config = await self.config.read()

    @birthday_greetings.before_loop
    async def before_birthday_greetings(self) -> None:
        await self.bot.wait_until_ready()

    @reputation.command(name="give", description="Gib einem hilfreichen Mitglied Reputation.")
    @app_commands.checks.cooldown(1, 86400.0, key=lambda i: (i.guild_id, i.user.id))
    async def reputation_give(self, interaction: discord.Interaction, nutzer: discord.Member, grund: app_commands.Range[str, 3, 250]) -> None:
        if nutzer.id == interaction.user.id or nutzer.bot:
            await interaction.response.send_message(embed=error_embed("❌ Nicht möglich"), ephemeral=True); return
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            entry = data.setdefault(str(interaction.guild.id), {}).setdefault(str(nutzer.id), {"points": 0, "reasons": []})
            entry["points"] += 1; entry["reasons"] = (entry["reasons"] + [grund])[-10:]
            return data
        await self.reputation_store.update(mutate)
        await interaction.response.send_message(embed=success_embed("🌟 Reputation vergeben", f"{nutzer.mention} erhielt einen Punkt für: {grund}"))

    @reputation.command(name="top", description="Zeigt die beliebtesten Community-Mitglieder.")
    async def reputation_top(self, interaction: discord.Interaction) -> None:
        data = (await self.reputation_store.read()).get(str(interaction.guild.id), {})
        top = sorted(data.items(), key=lambda item: item[1].get("points", 0), reverse=True)[:10]
        text = "\n".join(f"{index}. <@{uid}> — **{entry['points']}** 🌟" for index, (uid, entry) in enumerate(top, 1)) or "Noch keine Reputation vergeben."
        await interaction.response.send_message(embed=info_embed("🌟 Reputation-Rangliste", text))

    @community.command(name="membercard", description="Zeigt eine kompakte Member-Card.")
    async def membercard(self, interaction: discord.Interaction, nutzer: discord.Member | None = None) -> None:
        member = nutzer or interaction.user
        reputation = (await self.reputation_store.read()).get(str(interaction.guild.id), {}).get(str(member.id), {}).get("points", 0)
        embed = info_embed(f"👤 {member.display_name}", f"**Mitglied seit:** <t:{int(member.joined_at.timestamp())}:D>\n**Account erstellt:** <t:{int(member.created_at.timestamp())}:D>")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Rollen", value=str(max(0, len(member.roles) - 1)), inline=True)
        embed.add_field(name="Reputation", value=f"{reputation} 🌟", inline=True)
        await interaction.response.send_message(embed=embed)

    @sticky.command(name="set", description="Hält eine Nachricht am unteren Ende eines Kanals sichtbar.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def sticky_set(self, interaction: discord.Interaction, kanal: discord.TextChannel, nachricht: app_commands.Range[str, 1, 1500]) -> None:
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault(str(interaction.guild.id), {}).setdefault("sticky", {})[str(kanal.id)] = nachricht
            return data
        await self.config.update(mutate)
        await interaction.response.send_message(embed=success_embed("📌 Sticky gesetzt", kanal.mention), ephemeral=True)

    @sticky.command(name="remove", description="Entfernt eine Sticky-Nachricht aus einem Kanal.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def sticky_remove(self, interaction: discord.Interaction, kanal: discord.TextChannel) -> None:
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault(str(interaction.guild.id), {}).setdefault("sticky", {}).pop(str(kanal.id), None)
            return data
        await self.config.update(mutate)
        await interaction.response.send_message(embed=success_embed("✅ Sticky entfernt"), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or message.channel.id in self._sticky_busy: return
        text = (await self.config.read()).get(str(message.guild.id), {}).get("sticky", {}).get(str(message.channel.id))
        if not text or not isinstance(message.channel, discord.TextChannel): return
        self._sticky_busy.add(message.channel.id)
        try: await message.channel.send(f"📌 **Wichtige Info:** {text}")
        finally: self._sticky_busy.discard(message.channel.id)

    async def _set_config(self, guild_id: int, key: str, value: Any) -> None:
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault(str(guild_id), {})[key] = value
            return data
        await self.config.update(mutate)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Community(bot))
