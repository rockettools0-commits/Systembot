"""
Moderation & Automod — Krasse Überarbeitung.

Automod erkennt und bestraft automatisch:
  • Schimpfwörter / Hate-Speech
  • Nicht erlaubte Links & Discord-Invite-Links
  • Spam (Nachrichten-Flood)
  • Massen-Mentions (@everyone, @here, viele User-Pings)
  • Caps-Lock-Spam (>70% Großbuchstaben)
  • Emoji-Spam (>10 Emojis pro Nachricht)
  • Zeichen-Wiederholungen (aaaaaaaaa)
  • Zalgo-Text / Unicode-Schmutz
  • Raid-Erkennung (viele Joins in kurzer Zeit → Lockdown)
  • Account-Alter-Filter (neue Accounts < 7 Tage → Auto-Kick oder Warn)
  • Attachment-Filter (konfigurierbare verbotene Datei-Endungen)

Stufensystem: Warn → Mute → Kick → Ban (konfigurierbar)
Alle Aktionen → Clan-Log via bot.dispatch
Konfiguration live per Slash-Commands (/automod-...)
"""

import re
import time
import unicodedata
import datetime
from collections import defaultdict, deque

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.storage import JSONStore
from utils.theme import error_embed, warning_embed, success_embed, info_embed, get_footer_text
from utils.permissions import check_role_permission

WARN_PATH           = "data/warnings.json"
AUTOMOD_CONFIG_PATH = "data/automod_config.json"
MUTES_PATH          = "data/mutes.json"

# ── Standard-Wortlisten ───────────────────────────────────────────────────────
DEFAULT_BAD_WORDS = [
    "kanacke", "nazi", "hurensohn", "wichser", "scheiß", "arschloch",
    "fotze", "nutte", "spast", "nigger", "faggot", "retard",
]

# Discord-Invite Pattern
INVITE_PATTERN = re.compile(
    r"(discord\.gg/|discord\.com/invite/|discordapp\.com/invite/)", re.IGNORECASE
)
# Allgemeiner Link-Pattern
LINK_PATTERN = re.compile(r"(https?://|www\.)", re.IGNORECASE)
# Zalgo-Zeichen (combining chars > 5 hintereinander)
ZALGO_PATTERN = re.compile(r"[\u0300-\u036f\u0489]{5,}")
# Zeichen-Wiederholung (selber Buchstabe 6+ mal)
REPEAT_PATTERN = re.compile(r"(.)\1{5,}")
# IP-Adressen
IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

MUTE_ROLE_NAME       = "Muted"
SPAM_MSG_LIMIT       = 5
SPAM_TIME_WINDOW     = 3      # Sekunden
RAID_JOIN_THRESHOLD  = 8      # Joins innerhalb RAID_JOIN_WINDOW → Raid
RAID_JOIN_WINDOW     = 10     # Sekunden

# Warn-Eskalation
WARN_THRESHOLD_MUTE  = 3
WARN_THRESHOLD_KICK  = 5
WARN_THRESHOLD_BAN   = 7

# Verbotene Datei-Endungen (Standard)
DEFAULT_BLOCKED_EXTENSIONS = [".exe", ".bat", ".cmd", ".msi", ".scr", ".vbs", ".ps1", ".jar", ".zip", ".rar"]


def default_warnings():
    return {}

def default_automod_config():
    return {}

def default_mutes():
    return {}


# ── Confirm/Cancel View ───────────────────────────────────────────────────────

class ConfirmActionView(discord.ui.View):
    def __init__(self, action: str, target: discord.Member, grund: str, moderator: discord.Member):
        super().__init__(timeout=30)
        self.action    = action
        self.target    = target
        self.grund     = grund
        self.moderator = moderator
        self.done      = False

    async def _disable_all(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        self.done = True
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="✅ Bestätigen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.moderator.id:
            await interaction.response.send_message("❌ Nur der Ausführende darf bestätigen.", ephemeral=True)
            return
        self.done = True
        await self._disable_all(interaction)
        try:
            if self.action == "ban":
                await self.target.ban(reason=f"{self.grund} | Von: {self.moderator}")
                label = "gebannt"
            else:
                await self.target.kick(reason=f"{self.grund} | Von: {self.moderator}")
                label = "gekickt"
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehlende Berechtigung", "Mir fehlen die Berechtigungen."), ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("❌ Fehler", str(e)), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(f"✅ {self.target} wurde {label}.", f"**Grund:** {self.grund}"),
            ephemeral=True,
        )
        interaction.client.dispatch("clan_action", interaction.guild, self.action,
                                    self.target, self.moderator, self.grund)

    @discord.ui.button(label="❌ Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.moderator.id:
            await interaction.response.send_message("❌ Nur der Ausführende darf abbrechen.", ephemeral=True)
            return
        self.done = True
        await self._disable_all(interaction)
        await interaction.response.send_message(
            embed=info_embed("ℹ️ Abgebrochen", "Die Aktion wurde abgebrochen."), ephemeral=True)


# ── Hauptcog ─────────────────────────────────────────────────────────────────

class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot           = bot
        self.warn_store    = JSONStore(WARN_PATH,           default_warnings())
        self.automod_store = JSONStore(AUTOMOD_CONFIG_PATH, default_automod_config())
        self.mutes_store   = JSONStore(MUTES_PATH,          default_mutes())

        # In-Memory Tracking
        self.message_times: dict[tuple, deque]  = defaultdict(lambda: deque(maxlen=SPAM_MSG_LIMIT))
        self.join_times:    dict[int, deque]     = defaultdict(lambda: deque(maxlen=RAID_JOIN_THRESHOLD))
        self.lockdown_guilds: set[int]           = set()

        # Config-Cache
        self._automod_cache: dict = {}

    async def cog_load(self):
        self.check_mutes.start()

    def cog_unload(self):
        self.check_mutes.cancel()

    # ── Mute-Persistenz ───────────────────────────────────────────────────────

    @tasks.loop(seconds=30)
    async def check_mutes(self):
        now  = datetime.datetime.now(datetime.timezone.utc)
        data = await self.mutes_store.read()
        to_remove: list[tuple[str, str]] = []
        for guild_id, user_mutes in data.items():
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue
            for user_id, info in list(user_mutes.items()):
                until = datetime.datetime.fromisoformat(info["until"])
                if now >= until:
                    member = guild.get_member(int(user_id))
                    if member:
                        role = guild.get_role(info["role_id"])
                        if role and role in member.roles:
                            try:
                                await member.remove_roles(role, reason="Mute-Dauer abgelaufen")
                            except discord.HTTPException:
                                pass
                    to_remove.append((guild_id, user_id))
        if to_remove:
            def mutate(d):
                for gid, uid in to_remove:
                    d.get(gid, {}).pop(uid, None)
                return d
            await self.mutes_store.update(mutate)

    @check_mutes.before_loop
    async def before_check_mutes(self):
        await self.bot.wait_until_ready()

    # ── Hilfsmethoden ────────────────────────────────────────────────────────

    async def _get_or_create_mute_role(self, guild: discord.Guild) -> discord.Role:
        role = discord.utils.get(guild.roles, name=MUTE_ROLE_NAME)
        if role is None:
            role = await guild.create_role(name=MUTE_ROLE_NAME, reason="Automod Mute-Rolle erstellt")
            for channel in guild.channels:
                try:
                    await channel.set_permissions(role, send_messages=False, speak=False, add_reactions=False)
                except discord.HTTPException:
                    pass
        return role

    async def _get_automod_config(self, guild_id: str) -> dict:
        if guild_id not in self._automod_cache:
            data = await self.automod_store.read()
            self._automod_cache[guild_id] = data.get(guild_id, {})
        return self._automod_cache[guild_id]

    def _invalidate_cache(self, guild_id: str):
        self._automod_cache.pop(guild_id, None)

    async def _auto_punish(
        self,
        guild:   discord.Guild,
        member:  discord.Member,
        reason:  str,
        channel: discord.abc.Messageable,
    ):
        """Bestraft basierend auf aktueller Warn-Anzahl automatisch."""
        guild_id = str(guild.id)

        warn_entry = {
            "moderator":    "Automod",
            "moderator_id": self.bot.user.id,
            "grund":        reason,
            "timestamp":    datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        result_holder: dict = {}

        def mutate(data):
            lst = data.setdefault(guild_id, {}).setdefault(str(member.id), [])
            lst.append(warn_entry)
            result_holder["count"] = len(lst)
            return data

        await self.warn_store.update(mutate)
        count = result_holder.get("count", 1)

        self.bot.dispatch("clan_action", guild, "warn", member, None, reason,
                          f"Automod · Verwarnungen: {count}")

        if count >= WARN_THRESHOLD_BAN:
            try:
                await member.ban(reason=f"Automod (Ban): {reason} [{count} Warns]")
                await channel.send(
                    embed=error_embed("🔨 Automod — Ban",
                                     f"{member.mention} wurde automatisch **gebannt**.\n"
                                     f"**Grund:** {reason}\n**Warns:** {count}"),
                    delete_after=10)
                self.bot.dispatch("clan_action", guild, "ban", member, None,
                                  f"Automod-Ban: {reason}", f"Warns: {count}")
            except discord.HTTPException:
                pass

        elif count >= WARN_THRESHOLD_KICK:
            try:
                await member.kick(reason=f"Automod (Kick): {reason} [{count} Warns]")
                await channel.send(
                    embed=error_embed("👢 Automod — Kick",
                                     f"{member.mention} wurde automatisch **gekickt**.\n"
                                     f"**Grund:** {reason}\n**Warns:** {count}"),
                    delete_after=10)
                self.bot.dispatch("clan_action", guild, "kick", member, None,
                                  f"Automod-Kick: {reason}", f"Warns: {count}")
            except discord.HTTPException:
                pass

        elif count >= WARN_THRESHOLD_MUTE:
            try:
                mute_role = await self._get_or_create_mute_role(guild)
                await member.add_roles(mute_role, reason=f"Automod (Mute): {reason}")
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)
                def m_mutate(d):
                    d.setdefault(guild_id, {})[str(member.id)] = {
                        "until": until.isoformat(), "role_id": mute_role.id}
                    return d
                await self.mutes_store.update(m_mutate)
                await channel.send(
                    embed=warning_embed("🔇 Automod — Mute",
                                       f"{member.mention} wurde automatisch für **10 Min.** gemutet.\n"
                                       f"**Grund:** {reason}\n**Warns:** {count}"),
                    delete_after=10)
                self.bot.dispatch("clan_action", guild, "mute", member, None,
                                  f"Automod-Mute: {reason}", "Dauer: 10 Min.")
            except discord.HTTPException:
                pass
        else:
            try:
                await channel.send(
                    embed=warning_embed("⚠️ Automod — Verwarnung",
                                       f"{member.mention} wurde automatisch verwarnt.\n"
                                       f"**Grund:** {reason}\n**Warns gesamt:** {count}"),
                    delete_after=8)
            except discord.HTTPException:
                pass

    automod = app_commands.Group(name="automod", description="Automod-Konfiguration.")

    # ── /automod config ───────────────────────────────────────────────────────

    @automod.command(name="config", description="Zeigt die aktuelle Automod-Konfiguration an.")
    async def automod_config(self, interaction: discord.Interaction):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        cfg = await self._get_automod_config(str(interaction.guild.id))
        embed = info_embed("🛡️ Automod-Konfiguration")
        embed.add_field(name="🔗 Link-Filter",           value="✅ An" if cfg.get("link_filter", True) else "❌ Aus", inline=True)
        embed.add_field(name="📨 Invite-Filter",         value="✅ An" if cfg.get("invite_filter", True) else "❌ Aus", inline=True)
        embed.add_field(name="📢 Mention-Filter",        value="✅ An" if cfg.get("mention_filter", True) else "❌ Aus", inline=True)
        embed.add_field(name="🔠 Caps-Filter",           value="✅ An" if cfg.get("caps_filter", True) else "❌ Aus", inline=True)
        embed.add_field(name="😂 Emoji-Filter",          value="✅ An" if cfg.get("emoji_filter", True) else "❌ Aus", inline=True)
        embed.add_field(name="🔁 Repeat-Filter",         value="✅ An" if cfg.get("repeat_filter", True) else "❌ Aus", inline=True)
        embed.add_field(name="👻 Zalgo-Filter",          value="✅ An" if cfg.get("zalgo_filter", True) else "❌ Aus", inline=True)
        embed.add_field(name="📁 Attachment-Filter",     value="✅ An" if cfg.get("attachment_filter", True) else "❌ Aus", inline=True)
        embed.add_field(name="🆕 Account-Alter-Filter",  value=f"✅ {cfg.get('min_account_days', 0)} Tage" if cfg.get('min_account_days', 0) > 0 else "❌ Aus", inline=True)
        embed.add_field(name="📣 Max. Mentions",         value=str(cfg.get("max_mentions", 5)), inline=True)
        embed.add_field(name="😂 Max. Emojis",           value=str(cfg.get("max_emojis", 10)), inline=True)
        embed.add_field(name="🔠 Min. Caps-Länge",       value=str(cfg.get("min_caps_length", 10)), inline=True)
        bw = cfg.get("bad_words", DEFAULT_BAD_WORDS)
        embed.add_field(name=f"🚫 Schimpfwörter ({len(bw)})",
                        value=", ".join(f"`{w}`" for w in bw[:10]) + ("…" if len(bw) > 10 else ""),
                        inline=False)
        ad = cfg.get("allowed_domains", [])
        embed.add_field(name=f"✅ Erlaubte Domains ({len(ad)})",
                        value=", ".join(f"`{d}`" for d in ad) if ad else "Keine",
                        inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @automod.command(name="toggle", description="Aktiviert oder deaktiviert einen Automod-Filter.")
    @app_commands.describe(filter_name="Welcher Filter?", aktiv="An oder Aus")
    @app_commands.choices(filter_name=[
        app_commands.Choice(name="🔗 Link-Filter",          value="link_filter"),
        app_commands.Choice(name="📨 Invite-Filter",        value="invite_filter"),
        app_commands.Choice(name="📢 Mention-Filter",       value="mention_filter"),
        app_commands.Choice(name="🔠 Caps-Filter",          value="caps_filter"),
        app_commands.Choice(name="😂 Emoji-Filter",         value="emoji_filter"),
        app_commands.Choice(name="🔁 Repeat-Filter",        value="repeat_filter"),
        app_commands.Choice(name="👻 Zalgo-Filter",         value="zalgo_filter"),
        app_commands.Choice(name="📁 Attachment-Filter",    value="attachment_filter"),
    ])
    async def automod_toggle(self, interaction: discord.Interaction,
                             filter_name: str, aktiv: bool):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        def mutate(data):
            data.setdefault(guild_id, {})[filter_name] = aktiv
            return data
        await self.automod_store.update(mutate)
        self._invalidate_cache(guild_id)
        status = "✅ aktiviert" if aktiv else "❌ deaktiviert"
        await interaction.response.send_message(
            embed=success_embed(f"🛡️ Filter {status}", f"`{filter_name}` wurde {status}."),
            ephemeral=True)

    @automod.command(name="badword", description="Fügt ein Schimpfwort hinzu oder entfernt es.")
    @app_commands.describe(aktion="Hinzufügen oder Entfernen", wort="Das Wort")
    @app_commands.choices(aktion=[
        app_commands.Choice(name="➕ Hinzufügen", value="add"),
        app_commands.Choice(name="➖ Entfernen",  value="remove"),
    ])
    async def automod_badword(self, interaction: discord.Interaction,
                              aktion: str, wort: str):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        wort     = wort.lower().strip()
        result: dict = {}
        def mutate(data):
            cfg  = data.setdefault(guild_id, {})
            bw   = list(cfg.get("bad_words", list(DEFAULT_BAD_WORDS)))
            if aktion == "add":
                if wort not in bw:
                    bw.append(wort)
                    result["msg"] = f"`{wort}` hinzugefügt."
                else:
                    result["msg"] = f"`{wort}` ist bereits in der Liste."
            else:
                if wort in bw:
                    bw.remove(wort)
                    result["msg"] = f"`{wort}` entfernt."
                else:
                    result["msg"] = f"`{wort}` war nicht in der Liste."
            cfg["bad_words"] = bw
            return data
        await self.automod_store.update(mutate)
        self._invalidate_cache(guild_id)
        await interaction.response.send_message(
            embed=success_embed("🚫 Wortliste aktualisiert", result["msg"]),
            ephemeral=True)

    @automod.command(name="domain", description="Erlaubte Domain hinzufügen oder entfernen.")
    @app_commands.describe(aktion="Hinzufügen oder Entfernen", domain="z.B. youtube.com")
    @app_commands.choices(aktion=[
        app_commands.Choice(name="➕ Erlauben",   value="add"),
        app_commands.Choice(name="➖ Entfernen",  value="remove"),
    ])
    async def automod_domain(self, interaction: discord.Interaction,
                             aktion: str, domain: str):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        domain   = domain.lower().strip()
        result: dict = {}
        def mutate(data):
            cfg = data.setdefault(guild_id, {})
            ad  = list(cfg.get("allowed_domains", []))
            if aktion == "add":
                if domain not in ad:
                    ad.append(domain)
                    result["msg"] = f"`{domain}` erlaubt."
                else:
                    result["msg"] = f"`{domain}` ist bereits erlaubt."
            else:
                if domain in ad:
                    ad.remove(domain)
                    result["msg"] = f"`{domain}` entfernt."
                else:
                    result["msg"] = f"`{domain}` war nicht in der Liste."
            cfg["allowed_domains"] = ad
            return data
        await self.automod_store.update(mutate)
        self._invalidate_cache(guild_id)
        await interaction.response.send_message(
            embed=success_embed("✅ Domain-Liste aktualisiert", result["msg"]),
            ephemeral=True)

    @automod.command(name="set", description="Setzt einen numerischen Automod-Wert.")
    @app_commands.describe(
        einstellung="Was soll geändert werden?",
        wert="Neuer Wert (Zahl)",
    )
    @app_commands.choices(einstellung=[
        app_commands.Choice(name="📣 Max. Mentions pro Nachricht", value="max_mentions"),
        app_commands.Choice(name="😂 Max. Emojis pro Nachricht",   value="max_emojis"),
        app_commands.Choice(name="🔠 Mindest-Länge für Caps-Filter", value="min_caps_length"),
        app_commands.Choice(name="🆕 Mindest-Account-Alter (Tage)", value="min_account_days"),
        app_commands.Choice(name="📋 Automod-Log-Kanal (Channel-ID)", value="log_channel_id"),
    ])
    async def automod_set(self, interaction: discord.Interaction,
                          einstellung: str, wert: int):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        def mutate(data):
            data.setdefault(guild_id, {})[einstellung] = wert
            return data
        await self.automod_store.update(mutate)
        self._invalidate_cache(guild_id)
        await interaction.response.send_message(
            embed=success_embed("🛡️ Automod aktualisiert",
                                f"`{einstellung}` wurde auf **{wert}** gesetzt."),
            ephemeral=True)

    @app_commands.command(name="lockdown",
                          description="Aktiviert oder deaktiviert den Server-Lockdown (kein Schreiben).")
    @app_commands.describe(aktiv="An = Lockdown, Aus = Normal")
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown(self, interaction: discord.Interaction, aktiv: bool):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild    = interaction.guild
        everyone = guild.default_role
        if aktiv:
            self.lockdown_guilds.add(guild.id)
        else:
            self.lockdown_guilds.discard(guild.id)

        count = 0
        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(everyone)
                overwrite.send_messages = False if aktiv else None
                await channel.set_permissions(everyone, overwrite=overwrite,
                                              reason=f"Lockdown {'aktiviert' if aktiv else 'deaktiviert'} von {interaction.user}")
                count += 1
            except discord.HTTPException:
                pass

        status = "🔒 aktiviert" if aktiv else "🔓 deaktiviert"
        await interaction.followup.send(
            embed=(error_embed if aktiv else success_embed)(
                f"⚡ Server-Lockdown {status}",
                f"**{count}** Text-Kanäle {'gesperrt' if aktiv else 'entsperrt'}.\n"
                + ("Niemand kann mehr schreiben bis der Lockdown aufgehoben wird." if aktiv else "Alle können wieder schreiben.")
            ),
            ephemeral=True)

    # ── Mod-Commands ─────────────────────────────────────────────────────────

    @app_commands.command(name="say", description="[Mod] Lässt den Bot eine Nachricht senden.")
    @app_commands.describe(kanal="Ziel-Kanal", nachricht="Die zu sendende Nachricht")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def say(self, interaction: discord.Interaction,
                  nachricht: str, kanal: discord.TextChannel = None):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        target = kanal or interaction.channel
        try:
            await target.send(nachricht)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Kein Zugriff", f"Ich kann nicht in {target.mention} schreiben."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("✅ Nachricht gesendet", f"In {target.mention}: {nachricht[:100]}"),
            ephemeral=True)

    @app_commands.command(name="announce", description="[Admin] Sendet eine Ankündigung in einen Kanal.")
    @app_commands.describe(kanal="Ziel-Kanal", titel="Titel der Ankündigung", inhalt="Text der Ankündigung")
    async def announce(self, interaction: discord.Interaction,
                       kanal: discord.TextChannel, titel: str, inhalt: str):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        embed = discord.Embed(
            title=f"📢 {titel}",
            description=inhalt,
            color=discord.Color.from_rgb(46, 204, 113),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=f"Ankündigung von {interaction.user} · {get_footer_text(interaction)}")
        try:
            await kanal.send(embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Kein Zugriff", f"Ich kann nicht in {kanal.mention} schreiben."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("✅ Ankündigung gesendet", f"Ankündigung in {kanal.mention} veröffentlicht."),
            ephemeral=True)

    @app_commands.command(name="lock", description="[Mod] Sperrt einen Kanal für @everyone.")
    @app_commands.describe(kanal="Zu sperrenden Kanal (Standard: aktueller)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        target = kanal or interaction.channel
        try:
            ow = target.overwrites_for(interaction.guild.default_role)
            ow.send_messages = False
            await target.set_permissions(interaction.guild.default_role, overwrite=ow,
                                         reason=f"Kanal gesperrt von {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Ich kann diesen Kanal nicht sperren."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=error_embed("🔒 Kanal gesperrt", f"{target.mention} wurde gesperrt."))
        self.bot.dispatch("clan_action", interaction.guild, "mute", interaction.user, interaction.user,
                          f"Kanal gesperrt: {target.name}")

    @app_commands.command(name="unlock", description="[Mod] Entsperrt einen Kanal für @everyone.")
    @app_commands.describe(kanal="Zu entsperrenden Kanal (Standard: aktueller)")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, kanal: discord.TextChannel = None):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        target = kanal or interaction.channel
        try:
            ow = target.overwrites_for(interaction.guild.default_role)
            ow.send_messages = None
            await target.set_permissions(interaction.guild.default_role, overwrite=ow,
                                         reason=f"Kanal entsperrt von {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Ich kann diesen Kanal nicht entsperren."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("🔓 Kanal entsperrt", f"{target.mention} wurde entsperrt."))

    @app_commands.command(name="tempban", description="Bannt einen User temporär vom Server.")
    @app_commands.describe(user="Der User", dauer_stunden="Dauer in Stunden", grund="Grund")
    @app_commands.checks.has_permissions(ban_members=True)
    async def tempban(self, interaction: discord.Interaction, user: discord.Member,
                      dauer_stunden: app_commands.Range[int, 1, 720],
                      grund: str = "Kein Grund angegeben"):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht möglich", "Du kannst diesen User nicht bannen (höhere/gleiche Rolle)."),
                ephemeral=True)
            return

        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=dauer_stunden)
        try:
            await user.ban(reason=f"{grund} | Tempban bis {until.strftime('%Y-%m-%d %H:%M')} UTC | Von: {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Ich kann diesen User nicht bannen."),
                ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("❌ Fehler", str(e)), ephemeral=True)
            return

        # Zeitgesteuerten Unban planen
        guild = interaction.guild
        async def _do_unban():
            import asyncio
            await asyncio.sleep(dauer_stunden * 3600)
            try:
                await guild.unban(user, reason=f"Tempban abgelaufen: {grund}")
                self.bot.dispatch("clan_action", guild, "unban", user, None,
                                  f"Tempban abgelaufen nach {dauer_stunden}h")
            except discord.HTTPException:
                pass

        import asyncio
        asyncio.create_task(_do_unban())

        await interaction.response.send_message(
            embed=success_embed(
                f"⏱️ Tempban ausgeführt",
                f"**{user}** wurde für **{dauer_stunden}h** gebannt.\n"
                f"**Grund:** {grund}\n"
                f"**Bis:** <t:{int(until.timestamp())}:F>",
            ))
        self.bot.dispatch("clan_action", interaction.guild, "ban", user, interaction.user,
                          grund, f"Tempban: {dauer_stunden}h")

    @app_commands.command(name="unban", description="Entbannt einen User vom Server.")
    @app_commands.describe(user_id="Die User-ID des gebannten Users", grund="Grund für den Unban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str,
                    grund: str = "Unban"):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültige ID", "Bitte eine gültige User-ID angeben."),
                ephemeral=True)
            return

        try:
            user = await interaction.client.fetch_user(uid)
            await interaction.guild.unban(user, reason=f"{grund} | Von: {interaction.user}")
        except discord.NotFound:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht gefunden", "Dieser User ist nicht gebannt oder existiert nicht."),
                ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Ich kann diesen User nicht entbannen."),
                ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(f"✅ {user} wurde entbannt.", f"**Grund:** {grund}"))
        self.bot.dispatch("clan_action", interaction.guild, "unban", user, interaction.user, grund)

    @app_commands.command(name="timeout", description="Setzt ein Discord-Timeout (Stummschaltung).")
    @app_commands.describe(user="Der User", minuten="Dauer in Minuten (1–40320)", grund="Grund")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout_cmd(self, interaction: discord.Interaction, user: discord.Member,
                          minuten: app_commands.Range[int, 1, 40320],
                          grund: str = "Kein Grund angegeben"):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minuten)
        try:
            await user.timeout(until, reason=f"{grund} | Von: {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Ich kann diesen User nicht timeouten."),
                ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("❌ Fehler", str(e)), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(
                f"⏱️ Timeout gesetzt",
                f"**{user}** wurde für **{minuten} Minuten** gestummt.\n"
                f"**Grund:** {grund}\n"
                f"**Bis:** <t:{int(until.timestamp())}:R>",
            ))
        self.bot.dispatch("clan_action", interaction.guild, "mute", user, interaction.user,
                          grund, f"Discord Timeout: {minuten} Min.")

    @app_commands.command(name="untimeout", description="Hebt das Timeout eines Users auf.")
    @app_commands.describe(user="Der User", grund="Grund")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout_cmd(self, interaction: discord.Interaction, user: discord.Member,
                            grund: str = "Timeout aufgehoben"):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        if not user.timed_out_until:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Kein Timeout", "Dieser User hat kein aktives Timeout."),
                ephemeral=True)
            return
        try:
            await user.timeout(None, reason=f"{grund} | Von: {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Ich kann das Timeout nicht aufheben."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed(f"✅ Timeout aufgehoben", f"**{user}** — Grund: {grund}"))
        self.bot.dispatch("clan_action", interaction.guild, "unmute", user, interaction.user, grund)

    @app_commands.command(name="resetnick", description="[Mod] Setzt den Nickname eines Users zurück.")
    @app_commands.describe(user="Das Mitglied")
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def resetnick(self, interaction: discord.Interaction, user: discord.Member):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        try:
            await user.edit(nick=None, reason=f"Nickname zurückgesetzt von {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Ich kann den Nickname dieses Users nicht ändern."),
                ephemeral=True)
            return
        await interaction.response.send_message(
            embed=success_embed("✅ Nickname zurückgesetzt", f"Nickname von {user.mention} wurde zurückgesetzt."),
            ephemeral=True)

    @app_commands.command(name="ban", description="Bannt einen User vom Server.")
    @app_commands.describe(user="Der zu bannende User", grund="Grund für den Bann")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.Member,
                  grund: str = "Kein Grund angegeben"):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht möglich", "Du kannst diesen User nicht bannen (höhere/gleiche Rolle)."),
                ephemeral=True)
            return
        embed = warning_embed("⚠️ Bann bestätigen",
                              f"Möchtest du **{user}** wirklich bannen?\n**Grund:** {grund}")
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.response.send_message(
            embed=embed,
            view=ConfirmActionView(action="ban", target=user, grund=grund, moderator=interaction.user),
            ephemeral=True)

    @app_commands.command(name="kick", description="Kickt einen User vom Server.")
    @app_commands.describe(user="Der zu kickende User", grund="Grund für den Kick")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, user: discord.Member,
                   grund: str = "Kein Grund angegeben"):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        if user.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht möglich", "Du kannst diesen User nicht kicken (höhere/gleiche Rolle)."),
                ephemeral=True)
            return
        embed = warning_embed("⚠️ Kick bestätigen",
                              f"Möchtest du **{user}** wirklich kicken?\n**Grund:** {grund}")
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.response.send_message(
            embed=embed,
            view=ConfirmActionView(action="kick", target=user, grund=grund, moderator=interaction.user),
            ephemeral=True)

    @app_commands.command(name="mute", description="Muted einen User (Text & Voice).")
    @app_commands.describe(user="Der zu mutende User", dauer_minuten="Dauer in Minuten (0 = permanent)", grund="Grund")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, user: discord.Member,
                   dauer_minuten: int = 0, grund: str = "Kein Grund angegeben"):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        await interaction.response.defer()
        try:
            mute_role = await self._get_or_create_mute_role(interaction.guild)
            await user.add_roles(mute_role, reason=f"{grund} | Von: {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send(embed=error_embed("❌ Keine Berechtigung", "Mute fehlgeschlagen."))
            return
        except discord.HTTPException as e:
            await interaction.followup.send(embed=error_embed("❌ Fehler", str(e)))
            return

        duration_text = f"{dauer_minuten} Minuten" if dauer_minuten > 0 else "permanent"
        await interaction.followup.send(
            embed=success_embed(f"🔇 {user} wurde gemuted ({duration_text}).", f"**Grund:** {grund}"))
        self.bot.dispatch("clan_action", interaction.guild, "mute", user, interaction.user,
                          grund, f"Dauer: {duration_text}")
        if dauer_minuten > 0:
            until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=dauer_minuten)
            def mutate(d):
                d.setdefault(str(interaction.guild.id), {})[str(user.id)] = {
                    "until": until.isoformat(), "role_id": mute_role.id}
                return d
            await self.mutes_store.update(mutate)

    @app_commands.command(name="unmute", description="Entfernt den Mute eines Users.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, user: discord.Member):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        mute_role = discord.utils.get(interaction.guild.roles, name=MUTE_ROLE_NAME)
        if mute_role is None or mute_role not in user.roles:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Nicht gemuted", "Dieser User ist nicht gemuted."), ephemeral=True)
            return
        try:
            await user.remove_roles(mute_role, reason=f"Entmutet von {interaction.user}")
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed("❌ Fehler", str(e)), ephemeral=True)
            return
        def mutate(d):
            d.get(str(interaction.guild.id), {}).pop(str(user.id), None)
            return d
        await self.mutes_store.update(mutate)
        await interaction.response.send_message(embed=success_embed(f"✅ {user} wurde entmutet."))
        self.bot.dispatch("clan_action", interaction.guild, "unmute", user, interaction.user, "Mute aufgehoben")

    @app_commands.command(name="warn", description="Verwarnt einen User.")
    @app_commands.describe(user="Der zu verwarnende User", grund="Grund der Verwarnung")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, user: discord.Member, grund: str):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id   = str(interaction.guild.id)
        warn_entry = {
            "moderator":    str(interaction.user),
            "moderator_id": interaction.user.id,
            "grund":        grund,
            "timestamp":    datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        def mutate(data):
            data.setdefault(guild_id, {}).setdefault(str(user.id), []).append(warn_entry)
            return data
        result     = await self.warn_store.update(mutate)
        warn_count = len(result[guild_id][str(user.id)])
        await interaction.response.send_message(
            embed=warning_embed(f"⚠️ {user} wurde verwarnt.",
                                f"**Grund:** {grund}\nGesamt-Verwarnungen: **{warn_count}**"))
        self.bot.dispatch("clan_action", interaction.guild, "warn", user, interaction.user,
                          grund, f"Verwarnungen gesamt: {warn_count}")
        try:
            await user.send(f"⚠️ Du wurdest auf **{interaction.guild.name}** verwarnt.\nGrund: {grund}")
        except discord.Forbidden:
            pass
        # Eskalation
        if warn_count >= WARN_THRESHOLD_BAN:
            try:
                await user.ban(reason=f"Automod: {warn_count} Verwarnungen")
                await interaction.channel.send(
                    embed=error_embed(f"🔨 {user} wurde automatisch gebannt.",
                                     f"Grund: {warn_count} Verwarnungen."), delete_after=10)
            except discord.HTTPException:
                pass
        elif warn_count >= WARN_THRESHOLD_KICK:
            try:
                await user.kick(reason=f"Automod: {warn_count} Verwarnungen")
                await interaction.channel.send(
                    embed=error_embed(f"👢 {user} wurde automatisch gekickt.",
                                     f"Grund: {warn_count} Verwarnungen."), delete_after=10)
            except discord.HTTPException:
                pass
        elif warn_count >= WARN_THRESHOLD_MUTE:
            try:
                mute_role = await self._get_or_create_mute_role(interaction.guild)
                await user.add_roles(mute_role, reason=f"Automod: {warn_count} Verwarnungen")
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)
                def mm(d):
                    d.setdefault(guild_id, {})[str(user.id)] = {
                        "until": until.isoformat(), "role_id": mute_role.id}
                    return d
                await self.mutes_store.update(mm)
                await interaction.channel.send(
                    embed=warning_embed(f"🔇 {user} wurde automatisch für 10 Min. gemuted.",
                                       f"Grund: {warn_count} Verwarnungen."), delete_after=10)
            except discord.HTTPException:
                pass

    @app_commands.command(name="warn-remove", description="Entfernt eine einzelne Verwarnung eines Users.")
    @app_commands.describe(user="Der User", nummer="Nummer der Verwarnung (1-basiert, siehe /warnings)")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn_remove(self, interaction: discord.Interaction, user: discord.Member, nummer: int):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        result_holder: dict = {}
        def mutate(data):
            entries = data.get(guild_id, {}).get(str(user.id), [])
            if nummer < 1 or nummer > len(entries):
                result_holder["error"] = f"Ungültige Nummer. {user.display_name} hat {len(entries)} Verwarnungen."
                return data
            removed = entries.pop(nummer - 1)
            result_holder["removed"] = removed
            data[guild_id][str(user.id)] = entries
            return data
        await self.warn_store.update(mutate)
        if "error" in result_holder:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehler", result_holder["error"]), ephemeral=True)
            return
        removed = result_holder["removed"]
        await interaction.response.send_message(
            embed=success_embed(
                f"✅ Verwarnung #{nummer} entfernt",
                f"**User:** {user.mention}\n**Entfernte Verwarnung:** {removed['grund']}\n"
                f"**Ursprünglich von:** {removed['moderator']}"),
            ephemeral=True)
        self.bot.dispatch("clan_action", interaction.guild, "warn", user, interaction.user,
                          f"Verwarnung #{nummer} entfernt: {removed['grund']}", "Aktion: Warn-Remove")

    @app_commands.command(name="warn-clear", description="Löscht ALLE Verwarnungen eines Users.")
    @app_commands.describe(user="Der User dessen Verwarnungen gelöscht werden")
    @app_commands.checks.has_permissions(administrator=True)
    async def warn_clear(self, interaction: discord.Interaction, user: discord.Member):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        count_holder: dict = {}
        def mutate(data):
            entries = data.get(guild_id, {}).get(str(user.id), [])
            count_holder["count"] = len(entries)
            data.setdefault(guild_id, {})[str(user.id)] = []
            return data
        await self.warn_store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("✅ Alle Verwarnungen gelöscht",
                                f"**{count_holder['count']}** Verwarnung(en) von {user.mention} wurden entfernt."),
            ephemeral=True)
        self.bot.dispatch("clan_action", interaction.guild, "warn", user, interaction.user,
                          f"Alle {count_holder['count']} Verwarnungen gelöscht", "Aktion: Warn-Clear")

    @app_commands.command(name="warnings", description="Zeigt alle Verwarnungen eines Users an.")
    async def warnings_cmd(self, interaction: discord.Interaction, user: discord.Member):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        data    = await self.warn_store.read()
        entries = data.get(str(interaction.guild.id), {}).get(str(user.id), [])
        if not entries:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Keine Verwarnungen", f"**{user}** hat keine Verwarnungen."),
                ephemeral=True)
            return
        embed = warning_embed(f"⚠️ Verwarnungen von {user}")
        for i, e in enumerate(entries[-10:], start=1):
            embed.add_field(
                name=f"#{i} — {e['timestamp'][:10]}",
                value=f"**Grund:** {e['grund']}\n**Von:** {e['moderator']}",
                inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            msg = error_embed("❌ Keine Berechtigung", "Du hast nicht die nötigen Berechtigungen.")
        else:
            msg = error_embed("❌ Fehler", str(error))
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)

    # ── Raid-Erkennung ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild    = member.guild
        guild_id = guild.id
        now      = time.monotonic()
        cfg      = await self._get_automod_config(str(guild_id))

        # Raid-Detection
        dq = self.join_times[guild_id]
        dq.append(now)
        if len(dq) == RAID_JOIN_THRESHOLD and (now - dq[0]) <= RAID_JOIN_WINDOW:
            if guild_id not in self.lockdown_guilds:
                self.lockdown_guilds.add(guild_id)
                # Alle Text-Kanäle sperren
                for channel in guild.text_channels:
                    try:
                        ow = channel.overwrites_for(guild.default_role)
                        ow.send_messages = False
                        await channel.set_permissions(guild.default_role, overwrite=ow,
                                                      reason="Automod: Raid erkannt — Lockdown")
                    except discord.HTTPException:
                        pass
                # Erstem Kanal Warnung schicken
                tc = guild.system_channel or next((c for c in guild.text_channels), None)
                if tc:
                    try:
                        await tc.send(embed=error_embed(
                            "🚨 Raid erkannt — Server-Lockdown aktiv!",
                            f"In den letzten **{RAID_JOIN_WINDOW}s** sind **{RAID_JOIN_THRESHOLD}** "
                            f"Accounts beigetreten.\n"
                            f"Alle Kanäle wurden gesperrt. Admins: `/lockdown aktiv:False` zum Aufheben."))
                    except discord.HTTPException:
                        pass
                dq.clear()

        # Account-Alter-Filter
        min_days = cfg.get("min_account_days", 0)
        if min_days > 0:
            account_age = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
            if account_age < min_days:
                try:
                    await member.kick(reason=f"Automod: Account zu jung ({account_age} Tage < {min_days} Tage)")
                    self.bot.dispatch("clan_action", guild, "kick", member, None,
                                      f"Account zu jung: {account_age} Tage",
                                      f"Mindest-Alter: {min_days} Tage")
                except discord.HTTPException:
                    pass

    # ── Nachrichten-Automod ───────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        # Admins & Mods (manage_messages) sind ausgenommen
        if message.author.guild_permissions.administrator:
            return
        if message.author.guild_permissions.manage_messages:
            return

        guild_id = str(message.guild.id)
        cfg      = await self._get_automod_config(guild_id)
        content  = message.content
        lower    = content.lower()
        violated = False
        reason   = ""

        # 1. Schimpfwörter
        bad_words = cfg.get("bad_words", DEFAULT_BAD_WORDS)
        for word in bad_words:
            if word.lower() in lower:
                violated = True
                reason   = "Verbotenes Wort"
                break

        # 2. Discord-Invite-Links
        if not violated and cfg.get("invite_filter", True):
            if INVITE_PATTERN.search(content):
                violated = True
                reason   = "Discord-Invite-Link"

        # 3. Allgemeiner Link-Filter
        if not violated and cfg.get("link_filter", True):
            texts = [content] + [e.url or "" for e in message.embeds] + \
                    [e.description or "" for e in message.embeds]
            allowed = cfg.get("allowed_domains", [])
            for text in texts:
                if LINK_PATTERN.search(text) and not any(d in text.lower() for d in allowed):
                    violated = True
                    reason   = "Nicht erlaubter Link"
                    break

        # 4. IP-Adressen
        if not violated and IP_PATTERN.search(content):
            violated = True
            reason   = "IP-Adresse erkannt"

        # 5. Massen-Mentions
        if not violated and cfg.get("mention_filter", True):
            max_mentions = cfg.get("max_mentions", 5)
            mention_count = len(message.mentions) + \
                            (1 if message.mention_everyone else 0) + \
                            len(message.role_mentions)
            if mention_count > max_mentions:
                violated = True
                reason   = f"Zu viele Mentions ({mention_count})"

        # 6. Caps-Lock-Spam
        if not violated and cfg.get("caps_filter", True):
            min_len = cfg.get("min_caps_length", 10)
            letters = [c for c in content if c.isalpha()]
            if len(letters) >= min_len:
                caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
                if caps_ratio > 0.70:
                    violated = True
                    reason   = f"Caps-Spam ({int(caps_ratio*100)}% Großbuchstaben)"

        # 7. Emoji-Spam
        if not violated and cfg.get("emoji_filter", True):
            max_emojis = cfg.get("max_emojis", 10)
            # Standard-Emojis zählen
            emoji_count = sum(
                1 for c in content
                if unicodedata.category(c) in ("So", "Sm") or
                (ord(c) >= 0x1F600 and ord(c) <= 0x1FAFF)
            )
            # Custom Emojis (<:name:id>)
            emoji_count += len(re.findall(r"<a?:[a-zA-Z0-9_]+:\d+>", content))
            if emoji_count > max_emojis:
                violated = True
                reason   = f"Emoji-Spam ({emoji_count} Emojis)"

        # 8. Zeichen-Wiederholung
        if not violated and cfg.get("repeat_filter", True):
            if REPEAT_PATTERN.search(content):
                violated = True
                reason   = "Zeichen-Wiederholung"

        # 9. Zalgo-Text
        if not violated and cfg.get("zalgo_filter", True):
            if ZALGO_PATTERN.search(content):
                violated = True
                reason   = "Zalgo/Unicode-Spam"

        # 10. Verbotene Anhänge
        if not violated and cfg.get("attachment_filter", True):
            blocked_ext = cfg.get("blocked_extensions", DEFAULT_BLOCKED_EXTENSIONS)
            for att in message.attachments:
                ext = "." + att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else ""
                if ext in blocked_ext:
                    violated = True
                    reason   = f"Verbotener Datei-Typ (`{ext}`)"
                    break

        # ── Aktion bei Verstoß ────────────────────────────────────────────────
        if violated:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            try:
                await message.channel.send(
                    embed=error_embed(
                        "🛡️ Automod — Nachricht entfernt",
                        f"{message.author.mention} · **{reason}**"
                    ),
                    delete_after=7)
            except discord.HTTPException:
                pass

            # Clan-Log (direkt, ohne Punish)
            log_cid = cfg.get("log_channel_id")
            if log_cid:
                lch = message.guild.get_channel(log_cid)
                if lch:
                    try:
                        e = error_embed(
                            "🛡️ Automod: Nachricht entfernt",
                            f"**User:** {message.author.mention} (`{message.author.id}`)\n"
                            f"**Kanal:** {message.channel.mention}\n"
                            f"**Grund:** {reason}\n"
                            f"**Inhalt:** {content[:400]}")
                        await lch.send(embed=e)
                    except discord.HTTPException:
                        pass

            # Auto-Punish (Warn → Mute → Kick → Ban)
            await self._auto_punish(message.guild, message.author, reason, message.channel)
            return

        # ── Spam-Schutz ───────────────────────────────────────────────────────
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        dq  = self.message_times[key]
        dq.append(now)
        if len(dq) == SPAM_MSG_LIMIT and (now - dq[0]) <= SPAM_TIME_WINDOW:
            dq.clear()
            try:
                mute_role = await self._get_or_create_mute_role(message.guild)
                await message.author.add_roles(mute_role, reason="Automod: Spam erkannt")
                await message.channel.send(
                    embed=warning_embed(
                        "🔇 Automod — Spam erkannt",
                        f"{message.author.mention} wurde wegen Nachrichten-Spam automatisch gemutet."),
                    delete_after=8)
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)
                def sm(d):
                    d.setdefault(guild_id, {})[str(message.author.id)] = {
                        "until": until.isoformat(), "role_id": mute_role.id}
                    return d
                await self.mutes_store.update(sm)
                self.bot.dispatch("clan_action", message.guild, "mute", message.author, None,
                                  "Automod: Spam", "Dauer: 5 Min.")
            except discord.HTTPException:
                pass

    # ── Nachträglich gesendete Nachrichten (Edit-Check) ───────────────────────

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Bearbeitete Nachrichten werden ebenfalls durch den Automod gejagt."""
        if after.author.bot or after.guild is None:
            return
        if after.author.guild_permissions.administrator:
            return
        if after.author.guild_permissions.manage_messages:
            return
        # Automod erneut auf bearbeitete Nachricht anwenden
        await self.on_message(after)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
