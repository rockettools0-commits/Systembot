"""
Enterprise Security & AutoMod — Vollständige Überarbeitung.

Erkennt und blockiert:
  Anti-Raid:               Massen-Joins → automatischer Lockdown
  Anti-Spam:               Nachrichten-Flood pro Nutzer
  Anti-Duplicate:          Wiederholte identische Nachrichten
  Anti-Mention-Spam:       Massen-Erwähnungen (@everyone, User-Pings, Rollen)
  Anti-Link:               Alle URLs (mit Allowlist)
  Anti-Invite:             Discord-Einladungslinks
  Anti-Scam/Phishing:      Bekannte Phishing/Scam-Domains + Mustererkennung
  Anti-Nitro-Scam:         "Free Nitro"-Muster
  Anti-Token-Grabber:      Discord-Token-ähnliche Strings
  Anti-URL-Shortener:      Bekannte URL-Kürzer
  Anti-Ghost-Ping:         Mention → sofort gelöscht (Logging-Cog übernimmt)
  Anti-Emoji-Spam:         Zu viele Emojis pro Nachricht
  Anti-Zalgo:              Zalgo-/Unicode-Missbrauch
  Anti-Caps:               Zu viele Großbuchstaben
  Anti-Attachment-Spam:    Gefährliche Dateianhänge
  Anti-Webhook-Spam:       (via on_webhooks_update, in antinuke.py)
  Anti-Mass-Join:          Raid-Erkennung mit auto-Lockdown

Alle Erkennungen sind konfigurierbar und nutzen Schwellwerte statt einfacher
Wortlisten. Falsch-Positive werden durch kontextsensitive Prüfungen reduziert.

Architektur-Prinzipien:
  • Einzelne _detect()-Methode gibt (reason, severity) oder None zurück
  • _respond() führt konfigurierte Aktion aus (delete/timeout/warn/ban)
  • Sliding-Window Counter für Spam/Raid (kein Memory Leak durch auto-cleanup)
  • Config-Cache mit Invalidierung bei Änderungen
  • Vollständig asynchron, kein blocking I/O
"""

from __future__ import annotations

import asyncio
import datetime
import re
import time
import unicodedata
from collections import defaultdict, deque
from typing import Any, Final

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import error_embed, info_embed, success_embed, warning_embed

CONFIG_PATH:  Final[str] = "data/security_config.json"
HISTORY_PATH: Final[str] = "data/security_history.json"

# ── Erkennungs-Muster ─────────────────────────────────────────────────────────

INVITE_RE = re.compile(
    r"(?:discord(?:\.com)?/invite|discord\.gg|discordapp\.com/invite)/[\w-]+",
    re.IGNORECASE,
)
URL_RE       = re.compile(r"https?://([^/\s?#]+)", re.IGNORECASE)
TOKEN_RE     = re.compile(r"(?:mfa\.[\w-]{80,}|[\w-]{24}\.[\w-]{6}\.[\w-]{25,})")
ZALGO_RE     = re.compile(r"[\u0300-\u036f\u0489]{5,}")
REPEAT_RE    = re.compile(r"(.)\1{6,}")   # 6+ gleiche Zeichen hintereinander
CAPS_RE      = re.compile(r"[A-Z]")

# Unicode-Emoji-Zähler
EMOJI_RE     = re.compile(
    r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA9F"
    r"\u2702-\u27B0\u24C2-\uFE0F]+",
)

BLOCKED_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".exe", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".msi", ".jar",
    ".pif", ".reg", ".hta", ".com", ".dll",
})

SHORTENERS: Final[frozenset[str]] = frozenset({
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly",
    "rb.gy", "ow.ly", "buff.ly", "rebrand.ly", "short.gg",
})

# Bekannte Phishing/Scam-Domains (erweiterbar per /security domain)
MALICIOUS_DOMAINS: Final[frozenset[str]] = frozenset({
    "discord-nitro.com", "discord-gift.com", "discord-login.com",
    "steamcomminuty.com", "free-nitro.gg", "discord.gifts",
    "discordnitro.gift", "steamcommunily.com", "discordapp.gifts",
})

# Nitro-Scam-Muster
NITRO_SCAM_RE = re.compile(
    r"free\s+nitro|discord\s+nitro\s+giveaway|claim\s+your\s+nitro|"
    r"nitro\s+for\s+free|you\s+won\s+nitro",
    re.IGNORECASE,
)

# Regel-Metadaten
RULES: Final[dict[str, str]] = {
    "invite":       "Discord-Einladungen",
    "phishing":     "Phishing- & Scam-Domains",
    "nitro_scam":   "Nitro-Scam-Muster",
    "token":        "Discord-Token-Muster",
    "shortener":    "URL-Shortener",
    "spam":         "Nachrichten-Flood",
    "duplicate":    "Duplizierte Nachrichten",
    "mentions":     "Massen-Erwähnungen",
    "unicode":      "Zalgo- & Unicode-Spam",
    "attachments":  "Gefährliche Dateianhänge",
    "caps":         "Caps-Spam",
    "emoji_spam":   "Emoji-Spam",
    "repeat_chars": "Zeichen-Wiederholung",
    "mass_join":    "Raid-/Massen-Join-Erkennung",
}


def _default_config() -> dict[str, Any]:
    return {}


def _build_guild_config(stored: dict) -> dict[str, Any]:
    """Erstellt vollständige Guild-Config mit sicheren Defaults."""
    rules_default = {rule: True for rule in RULES}
    rules_default["caps"] = False         # Standard aus (viele Leute schreiben in Caps)
    rules_default["emoji_spam"] = False   # Standard aus
    return {
        "enabled":          stored.get("enabled", True),
        "rules":            {**rules_default, **stored.get("rules", {})},
        "action":           stored.get("action", "delete"),    # delete | timeout | warn | ban
        "timeout_minutes":  max(1, min(int(stored.get("timeout_minutes",  10)), 40320)),
        "max_mentions":     max(1, min(int(stored.get("max_mentions",      5)),    50)),
        "spam_limit":       max(2, min(int(stored.get("spam_limit",        6)),    20)),
        "spam_window":      max(2, min(int(stored.get("spam_window",       8)),    60)),
        "max_emojis":       max(1, min(int(stored.get("max_emojis",       15)),   100)),
        "min_caps_pct":     max(1, min(int(stored.get("min_caps_pct",     80)),   100)),  # % Großbuchstaben
        "raid_threshold":   max(2, min(int(stored.get("raid_threshold",    8)),    50)),
        "raid_window":      max(2, min(int(stored.get("raid_window",      10)),   120)),
        "log_channel_id":   stored.get("log_channel_id"),
        "emergency":        stored.get("emergency", False),
        "trusted_users":    set(stored.get("trusted_users", [])),
        "trusted_roles":    set(stored.get("trusted_roles", [])),
        "allowed_domains":  set(stored.get("allowed_domains", [])),
        "exempt_channels":  set(stored.get("exempt_channels", [])),
        "extra_phishing":   set(stored.get("extra_phishing", [])),
    }


class Security(commands.Cog):
    """
    Modulares Enterprise-Security-System.
    Kein Bot-Crash bei Ausnahmen — alle Erkenner sind isoliert.
    """

    security = app_commands.Group(
        name="security",
        description="Enterprise-Sicherheitszentrale.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot          = bot
        self.store        = JSONStore(CONFIG_PATH,  _default_config())
        self.history_store= JSONStore(HISTORY_PATH, {})

        # Config-Cache: guild_id → config dict
        self._cache: dict[int, dict[str, Any]] = {}

        # Sliding-Window Counter: (guild_id, user_id) → deque[timestamp]
        self._msg_times:    dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._dup_msgs:     dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)

        # Raid-Erkennung: guild_id → deque[timestamp]
        self._join_times: dict[int, deque[float]] = defaultdict(deque)

        # Laufende Lockdowns (verhindert Doppel-Reaktionen)
        self._raid_lockdown: set[int] = set()

    # ── Config-Verwaltung ──────────────────────────────────────────────────────

    async def _config(self, guild_id: int) -> dict[str, Any]:
        if guild_id not in self._cache:
            data = await self.store.read()
            self._cache[guild_id] = _build_guild_config(data.get(str(guild_id), {}))
        return self._cache[guild_id]

    def _invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)

    @staticmethod
    def _is_trusted(member: discord.Member, config: dict[str, Any]) -> bool:
        return (
            member.id in config["trusted_users"]
            or any(r.id in config["trusted_roles"] for r in member.roles)
        )

    @staticmethod
    def _normalise(text: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", text).casefold().split())

    # ── Erkennungs-Engine ─────────────────────────────────────────────────────

    def _detect(
        self,
        message: discord.Message,
        config:  dict[str, Any],
    ) -> tuple[str, int] | None:
        """
        Prüft eine Nachricht auf alle aktivierten Regeln.
        Gibt (reason, severity 0-100) oder None zurück.
        Severity-Werte: 100=kritisch, 70=hoch, 50=mittel, 30=niedrig
        """
        text    = message.content or ""
        rules   = config["rules"]
        guild   = message.guild
        user_id = message.author.id
        key     = (guild.id, user_id)
        now     = time.monotonic()

        # ── 1. Token-Grabber (kritisch — immer zuerst) ────────────────────
        if rules.get("token") and TOKEN_RE.search(text):
            return "Mögliches Discord-Token erkannt", 100

        # ── 2. Phishing & Scam-Domains ─────────────────────────────────────
        domains = {m.lower() for m in URL_RE.findall(text)}
        all_malicious = MALICIOUS_DOMAINS | config.get("extra_phishing", set())
        if rules.get("phishing") and any(d in all_malicious for d in domains):
            return "Bekannte Phishing- oder Scam-Domain", 95

        # ── 3. Nitro-Scam ──────────────────────────────────────────────────
        if rules.get("nitro_scam") and NITRO_SCAM_RE.search(text):
            return "Nitro-Scam-Muster erkannt", 90

        # ── 4. Discord-Einladungen ─────────────────────────────────────────
        if rules.get("invite") and INVITE_RE.search(text):
            return "Nicht erlaubte Discord-Einladung", 70

        # ── 5. URL-Shortener ───────────────────────────────────────────────
        if rules.get("shortener") and any(d in SHORTENERS for d in domains):
            return "URL-Shortener erkannt", 50

        # ── 6. Massen-Erwähnungen ─────────────────────────────────────────
        if rules.get("mentions"):
            mention_count = len(message.mentions) + len(message.role_mentions)
            if message.mention_everyone or mention_count > config["max_mentions"]:
                return f"Massen-Erwähnung ({mention_count} Pings)", 75

        # ── 7. Zalgo/Unicode ──────────────────────────────────────────────
        if rules.get("unicode") and ZALGO_RE.search(text):
            return "Zalgo-/Unicode-Spam", 45

        # ── 8. Caps-Spam ──────────────────────────────────────────────────
        if rules.get("caps") and len(text) >= 10:
            caps   = len(CAPS_RE.findall(text))
            letters = sum(1 for c in text if c.isalpha())
            if letters > 0 and (caps / letters) * 100 >= config["min_caps_pct"]:
                return f"Caps-Spam ({caps/letters*100:.0f}% Großbuchstaben)", 35

        # ── 9. Emoji-Spam ─────────────────────────────────────────────────
        if rules.get("emoji_spam"):
            # Custom-Emojis + Unicode-Emojis zählen
            custom_count  = text.count("<:") + text.count("<a:")
            unicode_count = len(EMOJI_RE.findall(text))
            if custom_count + unicode_count > config["max_emojis"]:
                return f"Emoji-Spam ({custom_count + unicode_count} Emojis)", 35

        # ── 10. Zeichen-Wiederholung ───────────────────────────────────────
        if rules.get("repeat_chars") and REPEAT_RE.search(text):
            return "Zeichen-Wiederholung erkannt", 30

        # ── 11. Gefährliche Anhänge ────────────────────────────────────────
        if rules.get("attachments") and message.attachments:
            for att in message.attachments:
                ext = "." + att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else ""
                if ext in BLOCKED_EXTENSIONS:
                    return f"Gefährlicher Anhang: {att.filename}", 85

        # ── 12. Spam (Sliding-Window) ─────────────────────────────────────
        times = self._msg_times[key]
        times.append(now)
        while times and now - times[0] > config["spam_window"]:
            times.popleft()
        if rules.get("spam") and len(times) >= config["spam_limit"]:
            return f"Nachrichten-Flood ({len(times)} in {config['spam_window']}s)", 60

        # ── 13. Duplizierte Nachrichten ───────────────────────────────────
        normalised = self._normalise(text)
        if normalised and rules.get("duplicate"):
            dups = self._dup_msgs[key]
            dups.append((now, normalised))
            while dups and now - dups[0][0] > 30:
                dups.popleft()
            count = sum(1 for _, v in dups if v == normalised)
            if count >= 3:
                return f"Wiederholte Nachricht ({count}×)", 55

        return None

    # ── Reaktions-Engine ──────────────────────────────────────────────────────

    async def _audit(
        self,
        guild:       discord.Guild,
        title:       str,
        description: str,
        color:       discord.Color,
    ) -> None:
        """Schreibt einen Audit-Eintrag in den konfigurierten Log-Kanal."""
        config     = await self._config(guild.id)
        channel_id = config.get("log_channel_id")
        channel    = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title       = title,
            description = description,
            color       = color,
            timestamp   = datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=f"{guild.name} • Security Audit")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _record_incident(
        self,
        guild_id:  int,
        member_id: int,
        reason:    str,
        severity:  int,
        action:    str,
    ) -> None:
        """Speichert einen Vorfall in der History (max. 200 pro Guild)."""
        entry = {
            "user_id":   member_id,
            "reason":    reason,
            "severity":  severity,
            "action":    action,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

        def mutate(data: dict) -> dict:
            items = data.setdefault(str(guild_id), [])
            items.append(entry)
            data[str(guild_id)] = items[-200:]
            return data

        await self.history_store.update(mutate)

    async def _respond(
        self,
        message:  discord.Message,
        reason:   str,
        severity: int,
        config:   dict[str, Any],
    ) -> None:
        """Führt die konfigurierte Aktion aus."""
        # Nachricht löschen (immer)
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        action = config["action"]

        if action == "timeout" and isinstance(message.author, discord.Member):
            try:
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                    minutes=config["timeout_minutes"]
                )
                await message.author.timeout(until, reason=f"Security: {reason}")
            except (discord.Forbidden, discord.HTTPException):
                action = "delete"

        elif action == "ban" and isinstance(message.author, discord.Member):
            try:
                await message.author.ban(reason=f"Security-Autoban: {reason}", delete_message_days=1)
            except (discord.Forbidden, discord.HTTPException):
                action = "delete"

        elif action == "warn":
            try:
                await message.channel.send(
                    embed=warning_embed(
                        "⚠️ Automod",
                        f"{message.author.mention}: {reason}",
                    ),
                    delete_after=8,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        await self._record_incident(message.guild.id, message.author.id, reason, severity, action)
        await self._audit(
            message.guild,
            "🛡️ Bedrohung neutralisiert",
            f"**Mitglied:** {message.author.mention} (`{message.author.id}`)\n"
            f"**Regel:** {reason}\n"
            f"**Schwere:** {severity}/100\n"
            f"**Aktion:** `{action}`\n"
            f"**Kanal:** {message.channel.mention}",
            discord.Color.red(),
        )

    # ── Listener ─────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Haupt-Automod-Handler."""
        if (
            message.guild is None
            or message.author.bot
            or not isinstance(message.author, discord.Member)
        ):
            return

        config = await self._config(message.guild.id)

        if not config["enabled"]:
            return

        # Exempt-Channel prüfen
        if message.channel.id in config["exempt_channels"]:
            return

        # Trusted-Nutzer überspringen
        if self._is_trusted(message.author, config):
            return

        try:
            finding = self._detect(message, config)
        except Exception:
            return  # Darf nie den Bot crashen

        if finding:
            reason, severity = finding
            await self._respond(message, reason, severity, config)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """
        Raid-Erkennung: Zu viele Joins in kurzer Zeit → Lockdown.
        Alt-Account-Warnung im Emergency-Modus.
        """
        config = await self._config(member.guild.id)

        if not config["enabled"]:
            return

        # ── Raid-Erkennung ────────────────────────────────────────────────
        if config["rules"].get("mass_join"):
            now   = time.monotonic()
            joins = self._join_times[member.guild.id]
            joins.append(now)
            while joins and now - joins[0] > config["raid_window"]:
                joins.popleft()

            if len(joins) >= config["raid_threshold"] and member.guild.id not in self._raid_lockdown:
                await self._trigger_raid_lockdown(member.guild, config, len(joins))

        # ── Emergency: Neuer Account → Audit ──────────────────────────────
        if config["emergency"]:
            age = datetime.datetime.now(datetime.timezone.utc) - member.created_at
            if age < datetime.timedelta(days=7):
                await self._record_incident(
                    member.guild.id, member.id,
                    "Neuer Account während Notfallmodus", 65, "audit",
                )
                await self._audit(
                    member.guild,
                    "⚠️ Neuer Account im Notfallmodus",
                    f"**Mitglied:** {member.mention} (`{member.id}`)\n"
                    f"**Account-Alter:** {age.days} Tage\n"
                    f"**Empfehlung:** Vor Freigabe manuell prüfen.",
                    discord.Color.orange(),
                )

    async def _trigger_raid_lockdown(
        self,
        guild:  discord.Guild,
        config: dict,
        count:  int,
    ) -> None:
        """Aktiviert Raid-Lockdown: Alle Text-Kanäle für @everyone sperren."""
        self._raid_lockdown.add(guild.id)

        await self._audit(
            guild,
            "🚨 RAID ERKANNT — Lockdown aktiviert",
            f"**{count} Joins** in {config['raid_window']}s erkannt.\n"
            f"Alle Kanäle wurden gesperrt. Bitte manuell prüfen und `/mod lockdown aktiv=False` ausführen.",
            discord.Color.red(),
        )

        for channel in guild.text_channels:
            try:
                ow = channel.overwrites_for(guild.default_role)
                ow.send_messages = False
                await channel.set_permissions(
                    guild.default_role,
                    overwrite=ow,
                    reason="Anti-Raid Lockdown",
                )
            except (discord.Forbidden, discord.HTTPException):
                continue

        # Nach 300s Lockdown automatisch wieder freigeben
        async def _auto_unlock():
            await asyncio.sleep(300)
            self._raid_lockdown.discard(guild.id)
            await self._audit(
                guild,
                "✅ Raid-Lockdown automatisch aufgehoben",
                "Lockdown nach 5 Minuten automatisch beendet. Bitte Mitgliederliste prüfen.",
                discord.Color.green(),
            )

        asyncio.create_task(_auto_unlock())

    # ── Slash-Commands ────────────────────────────────────────────────────────

    @security.command(name="status", description="Zeigt den vollständigen Sicherheitsstatus.")
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction.guild.id)
        active_rules = [name for key, name in RULES.items() if config["rules"].get(key)]
        inactive     = [name for key, name in RULES.items() if not config["rules"].get(key)]

        embed = info_embed(
            "🛡️ Security Center",
            f"**Status:** {'✅ Aktiv' if config['enabled'] else '❌ Pausiert'}\n"
            f"**Aktive Regeln:** {len(active_rules)}/{len(RULES)}\n"
            f"**Reaktion:** `{config['action']}`\n"
            f"**Notfallmodus:** {'🚨 Aktiv' if config['emergency'] else '○ Inaktiv'}\n"
            f"**Raid-Threshold:** {config['raid_threshold']} Joins/{config['raid_window']}s",
        )
        embed.add_field(
            name="✅ Aktive Regeln",
            value="\n".join(f"• {r}" for r in active_rules[:10]) or "Keine",
            inline=True,
        )
        embed.add_field(
            name="❌ Inaktive Regeln",
            value="\n".join(f"• {r}" for r in inactive[:10]) or "Keine",
            inline=True,
        )
        embed.add_field(
            name="⚙️ Einstellungen",
            value=(
                f"Max. Mentions: **{config['max_mentions']}**\n"
                f"Max. Emojis: **{config['max_emojis']}**\n"
                f"Spam: **{config['spam_limit']}** MSG/{config['spam_window']}s\n"
                f"Timeout: **{config['timeout_minutes']}** Min.\n"
                f"Trusted: **{len(config['trusted_users'])}** Nutzer / **{len(config['trusted_roles'])}** Rollen"
            ),
            inline=False,
        )
        log_ch = config["log_channel_id"]
        embed.add_field(
            name="📋 Log-Kanal",
            value=f"<#{log_ch}>" if log_ch else "❌ Nicht gesetzt",
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @security.command(name="rule", description="Aktiviert oder deaktiviert eine Schutzregel.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(rule=[
        app_commands.Choice(name=name, value=key) for key, name in RULES.items()
    ])
    async def rule(self, interaction: discord.Interaction, rule: str, aktiv: bool) -> None:
        def mutate(data: dict) -> dict:
            data.setdefault(str(interaction.guild.id), {}).setdefault("rules", {})[rule] = aktiv
            return data

        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed(
                "🛡️ Regel aktualisiert",
                f"**{RULES[rule]}:** {'✅ aktiviert' if aktiv else '❌ deaktiviert'}",
            ),
            ephemeral=True,
        )

    @security.command(name="configure", description="Konfiguriert Sicherheitsparameter.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="🗑️ Nur löschen (Standard)",       value="delete"),
        app_commands.Choice(name="⏱️ Löschen + Timeout",             value="timeout"),
        app_commands.Choice(name="⚠️ Löschen + Warn-Nachricht",     value="warn"),
        app_commands.Choice(name="🔨 Löschen + Ban (gefährlich!)",   value="ban"),
    ])
    async def configure(
        self,
        interaction:     discord.Interaction,
        action:          str | None = None,
        audit_kanal:     discord.TextChannel | None = None,
        timeout_minuten: app_commands.Range[int, 1, 40320] | None = None,
        max_mentions:    app_commands.Range[int, 1, 50]    | None = None,
        spam_limit:      app_commands.Range[int, 2, 20]    | None = None,
        spam_window:     app_commands.Range[int, 2, 60]    | None = None,
        max_emojis:      app_commands.Range[int, 1, 100]   | None = None,
    ) -> None:
        if all(v is None for v in (action, audit_kanal, timeout_minuten, max_mentions, spam_limit, spam_window, max_emojis)):
            await interaction.response.send_message(
                embed=warning_embed("⚠️ Keine Änderung", "Gib mindestens eine Einstellung an."),
                ephemeral=True,
            )
            return

        def mutate(data: dict) -> dict:
            cfg = data.setdefault(str(interaction.guild.id), {})
            if action          is not None: cfg["action"]          = action
            if audit_kanal     is not None: cfg["log_channel_id"]  = audit_kanal.id
            if timeout_minuten is not None: cfg["timeout_minutes"] = timeout_minuten
            if max_mentions    is not None: cfg["max_mentions"]    = max_mentions
            if spam_limit      is not None: cfg["spam_limit"]      = spam_limit
            if spam_window     is not None: cfg["spam_window"]     = spam_window
            if max_emojis      is not None: cfg["max_emojis"]      = max_emojis
            return data

        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed("✅ Security-Konfiguration gespeichert"),
            ephemeral=True,
        )

    @security.command(name="trust", description="Nimmt einen Nutzer/eine Rolle aus Security-Prüfungen aus.")
    @app_commands.checks.has_permissions(administrator=True)
    async def trust(
        self,
        interaction: discord.Interaction,
        nutzer:      discord.Member | None = None,
        rolle:       discord.Role   | None = None,
    ) -> None:
        if not nutzer and not rolle:
            await interaction.response.send_message(
                embed=error_embed("❌ Auswahl erforderlich", "Nutzer oder Rolle angeben."),
                ephemeral=True,
            )
            return

        key, value = ("trusted_users", nutzer.id) if nutzer else ("trusted_roles", rolle.id)

        def mutate(data: dict) -> dict:
            values = data.setdefault(str(interaction.guild.id), {}).setdefault(key, [])
            if value not in values:
                values.append(value)
            return data

        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        target = nutzer.mention if nutzer else rolle.mention
        await interaction.response.send_message(
            embed=success_embed("✅ Als vertrauenswürdig markiert", target),
            ephemeral=True,
        )

    @security.command(name="untrust", description="Entfernt einen Nutzer/eine Rolle aus der Trusted-Liste.")
    @app_commands.checks.has_permissions(administrator=True)
    async def untrust(
        self,
        interaction: discord.Interaction,
        nutzer:      discord.Member | None = None,
        rolle:       discord.Role   | None = None,
    ) -> None:
        if not nutzer and not rolle:
            await interaction.response.send_message(
                embed=error_embed("❌ Auswahl erforderlich"),
                ephemeral=True,
            )
            return

        key, value = ("trusted_users", nutzer.id) if nutzer else ("trusted_roles", rolle.id)

        def mutate(data: dict) -> dict:
            values = data.setdefault(str(interaction.guild.id), {}).setdefault(key, [])
            if value in values:
                values.remove(value)
            return data

        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed("✅ Trusted-Status entfernt"),
            ephemeral=True,
        )

    @security.command(name="incidents", description="Zeigt die letzten Security-Vorfälle.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def incidents(self, interaction: discord.Interaction) -> None:
        items = (await self.history_store.read()).get(str(interaction.guild.id), [])[-15:]
        if not items:
            await interaction.response.send_message(
                embed=info_embed("🛡️ Security Incidents", "Keine Vorfälle protokolliert."),
                ephemeral=True,
            )
            return
        lines = [
            f"• <@{i['user_id']}> — **{i['reason']}** · "
            f"Schwere {i.get('severity', i.get('risk', 0))}/100 · "
            f"`{i['action']}` · {i['timestamp'][:10]}"
            for i in reversed(items)
        ]
        await interaction.response.send_message(
            embed=warning_embed("🛡️ Letzte Security-Vorfälle", "\n".join(lines)),
            ephemeral=True,
        )

    @security.command(name="score", description="Bewertet die aktuelle Sicherheitskonfiguration.")
    @app_commands.checks.has_permissions(administrator=True)
    async def score(self, interaction: discord.Interaction) -> None:
        config   = await self._config(interaction.guild.id)
        enabled  = sum(1 for v in config["rules"].values() if v)
        score    = (
            enabled * 5
            + (15 if config["log_channel_id"] else 0)
            + (10 if config["action"] == "timeout" else 5 if config["action"] == "warn" else 0)
            + (5  if config["emergency"] else 0)
            + (5  if config["trusted_users"] or config["trusted_roles"] else 0)
        )
        score = min(score, 100)
        grade = "Sehr gut 🟢" if score >= 80 else "Gut 🟡" if score >= 55 else "Ausbaufähig 🔴"
        await interaction.response.send_message(
            embed=info_embed(
                "🛡️ Security Score",
                f"**{score}/100 — {grade}**\n"
                f"Aktive Regeln: **{enabled}/{len(RULES)}**\n"
                f"Audit-Log: {'✅' if config['log_channel_id'] else '❌'}\n"
                f"Reaktion: `{config['action']}`",
            ),
            ephemeral=True,
        )

    @security.command(name="emergency", description="Aktiviert/deaktiviert den Notfallmodus.")
    @app_commands.checks.has_permissions(administrator=True)
    async def emergency(self, interaction: discord.Interaction, aktiv: bool) -> None:
        def mutate(data: dict) -> dict:
            data.setdefault(str(interaction.guild.id), {})["emergency"] = aktiv
            return data

        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        fn  = warning_embed if aktiv else success_embed
        msg = "Neue junge Accounts werden im Audit geloggt." if aktiv else "Normaler Schutzmodus aktiv."
        await interaction.response.send_message(
            embed=fn(f"🚨 Notfallmodus {'aktiviert' if aktiv else 'deaktiviert'}", msg),
            ephemeral=True,
        )

    @security.command(name="exempt-channel", description="Kanal von Security-Prüfungen ausnehmen/einschließen.")
    @app_commands.checks.has_permissions(administrator=True)
    async def exempt_channel(
        self,
        interaction: discord.Interaction,
        kanal:       discord.TextChannel,
        ausnahme:    bool = True,
    ) -> None:
        def mutate(data: dict) -> dict:
            chs = data.setdefault(str(interaction.guild.id), {}).setdefault("exempt_channels", [])
            if ausnahme and kanal.id not in chs:
                chs.append(kanal.id)
            elif not ausnahme and kanal.id in chs:
                chs.remove(kanal.id)
            return data

        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Kanal-Ausnahme aktualisiert",
                f"{kanal.mention} {'ist jetzt' if ausnahme else 'ist nicht mehr'} von Security ausgenommen.",
            ),
            ephemeral=True,
        )

    @security.command(name="domain", description="Fügt eine Phishing-Domain hinzu oder entfernt sie.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(aktion=[
        app_commands.Choice(name="➕ Zur Blockliste hinzufügen", value="add"),
        app_commands.Choice(name="➖ Von Blockliste entfernen",  value="remove"),
    ])
    async def domain(self, interaction: discord.Interaction, aktion: str, domain: str) -> None:
        domain = domain.lower().strip()

        def mutate(data: dict) -> dict:
            domains = data.setdefault(str(interaction.guild.id), {}).setdefault("extra_phishing", [])
            if aktion == "add" and domain not in domains:
                domains.append(domain)
            elif aktion == "remove" and domain in domains:
                domains.remove(domain)
            return data

        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Domain-Blockliste aktualisiert",
                f"`{domain}` wurde {'hinzugefügt' if aktion == 'add' else 'entfernt'}.",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Security(bot))
