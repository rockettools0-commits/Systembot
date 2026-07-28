"""Enterprise-Sicherheitskern: konfigurierbare Erkennung, Audit-Logs und sichere Reaktionen.

Das Modul arbeitet vollständig lokal und benötigt weder externe APIs noch Nachrichtendaten
außerhalb von Discord. Riskante Sanktionen sind bewusst *opt-in*; der Standard löscht nur
eindeutig schädliche Inhalte und schreibt einen nachvollziehbaren Audit-Log.
"""

from __future__ import annotations

import datetime
import re
import time
import unicodedata
from collections import defaultdict, deque
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import error_embed, info_embed, success_embed, warning_embed

CONFIG_PATH = "data/security_config.json"
HISTORY_PATH = "data/security_history.json"

RULES = {
    "invite": "Discord-Einladungen",
    "phishing": "Phishing- & Malware-Domains",
    "token": "Discord-Token-Muster",
    "shortener": "URL-Shortener",
    "spam": "Nachrichten-Flood",
    "duplicate": "Duplizierte Nachrichten",
    "mentions": "Massen-Erwähnungen",
    "unicode": "Zalgo- & Unicode-Spam",
    "attachments": "Gefährliche Anhänge",
}

INVITE_RE = re.compile(r"(?:discord(?:app)?\.com/invite|discord\.gg)/[\w-]+", re.I)
URL_RE = re.compile(r"https?://([^/\s?#]+)", re.I)
TOKEN_RE = re.compile(r"(?:mfa\.[\w-]{80,}|[\w-]{24}\.[\w-]{6}\.[\w-]{25,})")
ZALGO_RE = re.compile(r"[\u0300-\u036f]{5,}")
BLOCKED_EXTENSIONS = {".exe", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".msi", ".jar"}
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly", "rb.gy"}
MALICIOUS_DOMAINS = {"discord-nitro.com", "discord-gift.com", "discord-login.com", "steamcomminuty.com"}


def default_config() -> dict[str, Any]:
    return {}


def _guild_config(data: dict[str, Any], guild_id: int) -> dict[str, Any]:
    """Liefert sichere Defaults, ohne bestehende Server-Konfigurationen umzuschreiben."""
    stored = data.get(str(guild_id), {})
    return {
        "enabled": stored.get("enabled", True),
        "rules": {rule: stored.get("rules", {}).get(rule, True) for rule in RULES},
        "action": stored.get("action", "delete"),  # delete | timeout | warn
        "timeout_minutes": max(1, min(int(stored.get("timeout_minutes", 10)), 40320)),
        "max_mentions": max(1, min(int(stored.get("max_mentions", 5)), 50)),
        "spam_limit": max(2, min(int(stored.get("spam_limit", 6)), 20)),
        "spam_window": max(2, min(int(stored.get("spam_window", 8)), 60)),
        "log_channel_id": stored.get("log_channel_id"),
        "emergency": stored.get("emergency", False),
        "trusted_users": set(stored.get("trusted_users", [])),
        "trusted_roles": set(stored.get("trusted_roles", [])),
    }


class Security(commands.Cog):
    """Modulares Sicherheitsmodul mit pro Server speicherbarer Konfiguration."""

    security = app_commands.Group(name="security", description="Enterprise-Sicherheitszentrale.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = JSONStore(CONFIG_PATH, default_config())
        self.history_store = JSONStore(HISTORY_PATH, {})
        self._cache: dict[int, dict[str, Any]] = {}
        self._message_times: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._duplicates: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)

    async def _config(self, guild_id: int) -> dict[str, Any]:
        if guild_id not in self._cache:
            self._cache[guild_id] = _guild_config(await self.store.read(), guild_id)
        return self._cache[guild_id]

    def _invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)

    @staticmethod
    def _is_trusted(member: discord.Member, config: dict[str, Any]) -> bool:
        return member.id in config["trusted_users"] or any(r.id in config["trusted_roles"] for r in member.roles)

    @staticmethod
    def _normalise(text: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", text).casefold().split())

    def _detect(self, message: discord.Message, config: dict[str, Any]) -> tuple[str, int] | None:
        text, rules = message.content, config["rules"]
        if rules["token"] and TOKEN_RE.search(text):
            return "Mögliches Discord-Token erkannt", 100
        domains = {match.lower().split(":")[0] for match in URL_RE.findall(text)}
        if rules["phishing"] and any(domain in MALICIOUS_DOMAINS for domain in domains):
            return "Bekannte Phishing- oder Malware-Domain", 95
        if rules["invite"] and INVITE_RE.search(text):
            return "Nicht erlaubte Discord-Einladung", 70
        if rules["shortener"] and any(domain in SHORTENERS for domain in domains):
            return "URL-Shortener erkannt", 45
        if rules["mentions"] and (message.mention_everyone or len(message.mentions) > config["max_mentions"]):
            return "Massen-Erwähnung", 70
        if rules["unicode"] and ZALGO_RE.search(text):
            return "Zalgo-/Unicode-Spam", 45
        if rules["attachments"] and any(
            attachment.filename.lower().endswith(ext) for attachment in message.attachments for ext in BLOCKED_EXTENSIONS
        ):
            return "Potentiell gefährlicher Anhang", 85

        now, key = time.monotonic(), (message.guild.id, message.author.id)
        times = self._message_times[key]
        times.append(now)
        while times and now - times[0] > config["spam_window"]:
            times.popleft()
        if rules["spam"] and len(times) >= config["spam_limit"]:
            return "Nachrichten-Flood", 60

        normalised = self._normalise(text)
        if normalised:
            duplicates = self._duplicates[key]
            duplicates.append((now, normalised))
            while duplicates and now - duplicates[0][0] > 30:
                duplicates.popleft()
            if rules["duplicate"] and sum(value == normalised for _, value in duplicates) >= 3:
                return "Wiederholte Nachricht", 55
        return None

    async def _audit(self, guild: discord.Guild, title: str, description: str, color: discord.Color) -> None:
        config = await self._config(guild.id)
        channel_id = config.get("log_channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_footer(text=f"{guild.name} • Security Audit")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def _record_incident(self, guild_id: int, member_id: int, reason: str, risk: int, action: str) -> None:
        entry = {"user_id": member_id, "reason": reason, "risk": risk, "action": action,
                 "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            items = data.setdefault(str(guild_id), [])
            items.append(entry)
            data[str(guild_id)] = items[-100:]  # begrenzte, datensparsame Historie
            return data
        await self.history_store.update(mutate)

    async def _respond(self, message: discord.Message, reason: str, risk: int, config: dict[str, Any]) -> None:
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        action = config["action"]
        if action == "timeout":
            try:
                until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=config["timeout_minutes"])
                await message.author.timeout(until, reason=f"Security: {reason}")
            except (discord.Forbidden, discord.HTTPException):
                action = "delete"
        await self._record_incident(message.guild.id, message.author.id, reason, risk, action)
        await self._audit(message.guild, "🛡️ Bedrohung abgewehrt", f"**Mitglied:** {message.author.mention}\n**Regel:** {reason}\n**Risiko:** {risk}/100\n**Aktion:** {action}", discord.Color.red())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        config = await self._config(message.guild.id)
        if not config["enabled"] or self._is_trusted(message.author, config):
            return
        finding = self._detect(message, config)
        if finding:
            reason, risk = finding
            await self._respond(message, reason, risk, config)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Notfallmodus meldet neue, sehr junge Accounts sichtbar im Security-Audit."""
        config = await self._config(member.guild.id)
        if not config["emergency"]:
            return
        age = datetime.datetime.now(datetime.timezone.utc) - member.created_at
        if age < datetime.timedelta(days=7):
            await self._record_incident(member.guild.id, member.id, "Neuer Account während Notfallmodus", 65, "audit")
            await self._audit(member.guild, "⚠️ Neuer Account im Notfallmodus", f"**Mitglied:** {member.mention}\n**Account-Alter:** {age.days} Tage\n**Empfehlung:** vor Freigabe prüfen.", discord.Color.orange())

    @security.command(name="status", description="Zeigt den Sicherheitsstatus dieses Servers.")
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction.guild.id)
        active = [name for key, name in RULES.items() if config["rules"][key]]
        embed = info_embed("🛡️ Security Center", f"**Status:** {'✅ Aktiv' if config['enabled'] else '❌ Pausiert'}\n**Regeln:** {len(active)}/{len(RULES)} aktiv\n**Aktion:** `{config['action']}`")
        embed.add_field(name="Aktive Schutzschichten", value=" • ".join(active)[:1024] or "Keine", inline=False)
        embed.add_field(name="Trusted", value=f"{len(config['trusted_users'])} Nutzer · {len(config['trusted_roles'])} Rollen", inline=True)
        embed.add_field(name="Audit-Log", value=f"<#{config['log_channel_id']}>" if config["log_channel_id"] else "Nicht gesetzt", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @security.command(name="rule", description="Aktiviert oder deaktiviert eine einzelne Schutzregel.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(rule="Schutzregel", aktiv="Aktiv oder inaktiv")
    @app_commands.choices(rule=[app_commands.Choice(name=name, value=key) for key, name in RULES.items()])
    async def rule(self, interaction: discord.Interaction, rule: str, aktiv: bool) -> None:
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault(str(interaction.guild.id), {}).setdefault("rules", {})[rule] = aktiv
            return data
        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(embed=success_embed("🛡️ Sicherheitsregel aktualisiert", f"**{RULES[rule]}:** {'aktiviert' if aktiv else 'deaktiviert'}"), ephemeral=True)

    @security.command(name="configure", description="Setzt Sicherheitsaktion, Limits oder Audit-Kanal.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(action="Reaktion nach einem Treffer", audit_kanal="Kanal für Security-Audits", timeout_minuten="Nur für Timeout-Aktion", max_mentions="Erlaubte Erwähnungen")
    @app_commands.choices(action=[app_commands.Choice(name="Nur löschen (empfohlen)", value="delete"), app_commands.Choice(name="Löschen + Timeout", value="timeout"), app_commands.Choice(name="Löschen + Audit-Warnung", value="warn")])
    async def configure(self, interaction: discord.Interaction, action: str | None = None, audit_kanal: discord.TextChannel | None = None, timeout_minuten: app_commands.Range[int, 1, 40320] | None = None, max_mentions: app_commands.Range[int, 1, 50] | None = None) -> None:
        if all(value is None for value in (action, audit_kanal, timeout_minuten, max_mentions)):
            await interaction.response.send_message(embed=warning_embed("⚠️ Keine Änderung", "Gib mindestens eine Einstellung an."), ephemeral=True)
            return
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            cfg = data.setdefault(str(interaction.guild.id), {})
            if action is not None: cfg["action"] = action
            if audit_kanal is not None: cfg["log_channel_id"] = audit_kanal.id
            if timeout_minuten is not None: cfg["timeout_minutes"] = timeout_minuten
            if max_mentions is not None: cfg["max_mentions"] = max_mentions
            return data
        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(embed=success_embed("✅ Security-Konfiguration gespeichert"), ephemeral=True)

    @security.command(name="trust", description="Nimmt einen Nutzer oder eine Rolle von Security-Prüfungen aus.")
    @app_commands.checks.has_permissions(administrator=True)
    async def trust(self, interaction: discord.Interaction, nutzer: discord.Member | None = None, rolle: discord.Role | None = None) -> None:
        if (nutzer is None) == (rolle is None):
            await interaction.response.send_message(embed=error_embed("❌ Auswahl erforderlich", "Wähle genau einen Nutzer oder eine Rolle."), ephemeral=True)
            return
        key, value = ("trusted_users", nutzer.id) if nutzer else ("trusted_roles", rolle.id)
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            values = data.setdefault(str(interaction.guild.id), {}).setdefault(key, [])
            if value not in values: values.append(value)
            return data
        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        target = nutzer.mention if nutzer else rolle.mention
        await interaction.response.send_message(embed=success_embed("✅ Als vertrauenswürdig markiert", target), ephemeral=True)

    @security.command(name="untrust", description="Entfernt einen Nutzer oder eine Rolle aus der Trusted-Liste.")
    @app_commands.checks.has_permissions(administrator=True)
    async def untrust(self, interaction: discord.Interaction, nutzer: discord.Member | None = None, rolle: discord.Role | None = None) -> None:
        if (nutzer is None) == (rolle is None):
            await interaction.response.send_message(embed=error_embed("❌ Auswahl erforderlich", "Wähle genau einen Nutzer oder eine Rolle."), ephemeral=True)
            return
        key, value = ("trusted_users", nutzer.id) if nutzer else ("trusted_roles", rolle.id)
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            values = data.setdefault(str(interaction.guild.id), {}).setdefault(key, [])
            if value in values: values.remove(value)
            return data
        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        await interaction.response.send_message(embed=success_embed("✅ Trusted-Status entfernt"), ephemeral=True)

    @security.command(name="incidents", description="Zeigt die letzten Security-Vorfälle.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def incidents(self, interaction: discord.Interaction) -> None:
        items = (await self.history_store.read()).get(str(interaction.guild.id), [])[-10:]
        if not items:
            await interaction.response.send_message(embed=info_embed("🛡️ Security Incidents", "Bisher wurden keine Vorfälle gespeichert."), ephemeral=True)
            return
        lines = [f"• <@{item['user_id']}> — **{item['reason']}** · Risiko {item['risk']}/100 · `{item['action']}`" for item in reversed(items)]
        await interaction.response.send_message(embed=warning_embed("🛡️ Letzte Security-Vorfälle", "\n".join(lines)), ephemeral=True)

    @security.command(name="score", description="Bewertet die aktuelle Sicherheitskonfiguration.")
    @app_commands.checks.has_permissions(administrator=True)
    async def score(self, interaction: discord.Interaction) -> None:
        config = await self._config(interaction.guild.id)
        enabled = sum(config["rules"].values())
        score = enabled * 8 + (15 if config["log_channel_id"] else 0) + (8 if config["action"] == "timeout" else 0) + (5 if config["emergency"] else 0)
        score = min(score, 100)
        grade = "Sehr gut" if score >= 85 else "Gut" if score >= 65 else "Ausbaufähig"
        await interaction.response.send_message(embed=info_embed("🛡️ Security Score", f"**{score}/100 — {grade}**\nAktive Regeln: {enabled}/{len(RULES)}\nAudit-Log: {'aktiv' if config['log_channel_id'] else 'nicht gesetzt'}"), ephemeral=True)

    @security.command(name="emergency", description="Aktiviert oder deaktiviert den Security-Notfallmodus.")
    @app_commands.checks.has_permissions(administrator=True)
    async def emergency(self, interaction: discord.Interaction, aktiv: bool) -> None:
        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault(str(interaction.guild.id), {})["emergency"] = aktiv
            return data
        await self.store.update(mutate)
        self._invalidate(interaction.guild.id)
        text = "Neue sehr junge Accounts werden jetzt im Audit hervorgehoben." if aktiv else "Der normale Schutzmodus läuft weiter."
        await interaction.response.send_message(embed=(warning_embed if aktiv else success_embed)(f"🚨 Notfallmodus {'aktiviert' if aktiv else 'deaktiviert'}", text), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Security(bot))
