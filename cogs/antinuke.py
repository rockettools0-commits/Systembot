"""
Anti-Nuke System — Audit-Log-basierte Erkennung und automatische Gegenmaßnahmen.

Erkennt:
  • Massen-Channel-Löschungen / -Erstellungen
  • Massen-Bans und Kicks
  • Rollen-Löschungen / Berechtigungs-Änderungen
  • Webhook-Erstellungen (häufig bei Nuke-Bots)
  • Server-Einstellungsänderungen in Folge

Gegenmaßnahmen:
  • Sofortige Entziehung der gefährlichen Rolle
  • Optional: Server-Lockdown (Mitglieder können nicht mehr schreiben)
  • Ban des auslösenden Accounts
  • DM an alle Owner/Admins

Konfiguration: /antinuke configure
"""
from __future__ import annotations

import asyncio
import datetime
from collections import defaultdict, deque
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import error_embed, info_embed, success_embed, warning_embed

CONFIG_PATH  = "data/antinuke_config.json"
LOG_PATH     = "data/antinuke_log.json"

# Zeitfenster (Sekunden) in dem Aktionen gezählt werden
WINDOW = 10


def _default_config() -> dict:
    return {}


def _guild_config(data: dict, guild_id: int) -> dict:
    """Gibt Anti-Nuke-Konfiguration mit sicheren Defaults zurück."""
    stored = data.get(str(guild_id), {})
    return {
        "enabled":           stored.get("enabled", False),
        "log_channel_id":    stored.get("log_channel_id"),
        "action":            stored.get("action", "derank"),   # derank | ban | lockdown
        "alert_owner":       stored.get("alert_owner", True),  # DM an Server-Owner?

        # Schwellwerte — Anzahl Aktionen im WINDOW-Zeitfenster
        "threshold_ban":     stored.get("threshold_ban",     3),
        "threshold_kick":    stored.get("threshold_kick",    5),
        "threshold_channel": stored.get("threshold_channel", 3),
        "threshold_role":    stored.get("threshold_role",    3),
        "threshold_webhook": stored.get("threshold_webhook", 2),

        # Whitelist: Diese Nutzer-IDs werden nie von Anti-Nuke erfasst
        "whitelist":         set(stored.get("whitelist", [])),
    }


class AntiNuke(commands.Cog):
    """
    Audit-Log-basiertes Anti-Nuke System mit konfigurierbaren Schwellwerten.
    """

    antinuke = app_commands.Group(name="antinuke", description="Anti-Nuke Schutz und Audit-Auswertung.")

    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self.store        = JSONStore(CONFIG_PATH, _default_config())
        self.log_store    = JSONStore(LOG_PATH, {})

        # Sliding-Window Counter: (guild_id, user_id, action) → deque[timestamp]
        self._counters: dict[tuple, deque] = defaultdict(deque)

        # Bereits behandelte Nuke-Versuche (verhindert doppelte Reaktionen)
        self._handled: set[tuple[int, int]] = set()   # (guild_id, user_id)
        self._lockdown_active: set[int] = set()       # guild_ids mit aktivem Lockdown

    # ─────────────────────────────────────────────────────────────────────────
    # Sliding-Window Hilfsmethode
    # ─────────────────────────────────────────────────────────────────────────

    def _tick(self, guild_id: int, user_id: int, action: str) -> int:
        """
        Zählt eine Aktion im Zeitfenster und gibt die aktuelle Anzahl zurück.
        Ältere Einträge werden automatisch bereinigt.
        """
        key  = (guild_id, user_id, action)
        dq   = self._counters[key]
        now  = datetime.datetime.now(datetime.timezone.utc).timestamp()
        dq.append(now)
        # Einträge außerhalb des Fensters entfernen
        while dq and now - dq[0] > WINDOW:
            dq.popleft()
        return len(dq)

    # ─────────────────────────────────────────────────────────────────────────
    # Reaktions-Engine
    # ─────────────────────────────────────────────────────────────────────────

    async def _react(
        self,
        guild:    discord.Guild,
        user_id:  int,
        reason:   str,
        config:   dict,
    ) -> None:
        """
        Führt die konfigurierte Gegenmaßnahme aus:
          derank   → höchste Rolle entziehen
          ban      → User bannen
          lockdown → Alle Text-Kanäle für @everyone sperren
        """
        # Doppelt-Handling verhindern
        handle_key = (guild.id, user_id)
        if handle_key in self._handled:
            return
        self._handled.add(handle_key)

        # Eintrag loggen
        await self._log_incident(guild, user_id, reason)

        member = guild.get_member(user_id)
        action = config["action"]

        # ── Audit-Log-Embed ──────────────────────────────────────────────────
        embed = error_embed(
            "🚨 Nuke-Versuch erkannt!",
            f"**Täter:** <@{user_id}>\n"
            f"**Grund:** {reason}\n"
            f"**Aktion:** `{action}`\n"
            f"**Server:** {guild.name}",
        )
        await self._send_log(guild, config, embed)

        # ── Gegenmaßnahmen ───────────────────────────────────────────────────
        if action in ("derank", "ban") and member:
            # Höchste Rolle unterhalb des Bots entziehen
            try:
                dangerous_roles = [
                    r for r in member.roles
                    if r != guild.default_role
                    and r.position < guild.me.top_role.position
                    and r.permissions.administrator or r.permissions.manage_channels
                    or r.permissions.ban_members or r.permissions.kick_members
                    or r.permissions.manage_roles
                ]
                if dangerous_roles:
                    await member.remove_roles(
                        *dangerous_roles,
                        reason=f"Anti-Nuke: {reason}",
                        atomic=False,
                    )
            except (discord.Forbidden, discord.HTTPException):
                pass

        if action == "ban" and member:
            try:
                await guild.ban(member, reason=f"Anti-Nuke: {reason}", delete_message_days=1)
            except (discord.Forbidden, discord.HTTPException):
                pass

        if action == "lockdown" and guild.id not in self._lockdown_active:
            await self._lockdown(guild, reason, config)

        # ── DM an Server-Owner ───────────────────────────────────────────────
        if config["alert_owner"] and guild.owner:
            try:
                await guild.owner.send(
                    embed=discord.Embed(
                        title="🚨 Anti-Nuke — Sofortwarnung",
                        description=(
                            f"**Server:** {guild.name}\n"
                            f"**Täter:** <@{user_id}>\n"
                            f"**Erkannter Angriff:** {reason}\n"
                            f"**Durchgeführte Aktion:** `{action}`\n\n"
                            f"Bitte überprüfe sofort den Server!"
                        ),
                        color=discord.Color.red(),
                        timestamp=datetime.datetime.now(datetime.timezone.utc),
                    )
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        # Handle-Key nach 30 Sekunden wieder freigeben (ermöglicht erneute Erkennung)
        async def _clear_handle():
            await asyncio.sleep(30)
            self._handled.discard(handle_key)
        asyncio.create_task(_clear_handle())

    async def _lockdown(self, guild: discord.Guild, reason: str, config: dict) -> None:
        """Sperrt alle Text-Kanäle für @everyone."""
        self._lockdown_active.add(guild.id)
        try:
            for channel in guild.text_channels:
                try:
                    overwrites = channel.overwrites_for(guild.default_role)
                    overwrites.send_messages = False
                    await channel.set_permissions(
                        guild.default_role,
                        overwrite=overwrites,
                        reason=f"Anti-Nuke Lockdown: {reason}",
                    )
                except (discord.Forbidden, discord.HTTPException):
                    continue
            await self._send_log(
                guild, config,
                warning_embed("🔒 Server-Lockdown aktiviert", f"Alle Kanäle wurden gesperrt.\nGrund: {reason}"),
            )
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Log / Persistenz
    # ─────────────────────────────────────────────────────────────────────────

    async def _send_log(self, guild: discord.Guild, config: dict, embed: discord.Embed) -> None:
        log_ch = guild.get_channel(config["log_channel_id"]) if config["log_channel_id"] else None
        if isinstance(log_ch, discord.TextChannel):
            try:
                await log_ch.send(embed=embed)
            except discord.HTTPException:
                pass

    async def _log_incident(self, guild: discord.Guild, user_id: int, reason: str) -> None:
        """Speichert Vorfall in der History-Datei."""
        entry = {
            "user_id":   user_id,
            "reason":    reason,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        def mutate(data: dict) -> dict:
            entries = data.setdefault(str(guild.id), [])
            entries.append(entry)
            data[str(guild.id)] = entries[-200:]   # Max 200 Einträge
            return data
        await self.log_store.update(mutate)

    # ─────────────────────────────────────────────────────────────────────────
    # Audit-Log-Auswertung (Haupt-Listener)
    # ─────────────────────────────────────────────────────────────────────────

    async def _process_audit(
        self,
        guild:     discord.Guild,
        action:    discord.AuditLogAction,
        user_id:   int,
        threshold: int,
        label:     str,
        config:    dict,
    ) -> None:
        """
        Zentraler Handler: Zählt die Aktion und löst Reaktion aus wenn
        der Schwellwert im Zeitfenster überschritten wird.
        """
        if not config["enabled"]:
            return
        if user_id in config["whitelist"]:
            return
        # Bot-Aktionen ignorieren (eigene IDs)
        if user_id == self.bot.user.id:
            return

        count = self._tick(guild.id, user_id, label)
        if count >= threshold:
            await self._react(guild, user_id, f"{label} ({count}× in {WINDOW}s)", config)

    async def _latest_audit(
        self,
        guild:  discord.Guild,
        action: discord.AuditLogAction,
        limit:  int = 1,
    ) -> discord.AuditLogEntry | None:
        """Liest den neuesten Audit-Log-Eintrag für eine bestimmte Aktion."""
        try:
            entries = [e async for e in guild.audit_logs(limit=limit, action=action)]
            return entries[0] if entries else None
        except (discord.Forbidden, discord.HTTPException):
            return None

    # ── Listener: Channel-Löschung ────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        guild  = channel.guild
        data   = await self.store.read()
        config = _guild_config(data, guild.id)
        if not config["enabled"]:
            return
        entry = await self._latest_audit(guild, discord.AuditLogAction.channel_delete)
        if entry and entry.user:
            await self._process_audit(
                guild, discord.AuditLogAction.channel_delete,
                entry.user.id, config["threshold_channel"],
                "Massen-Channel-Löschung", config,
            )

    # ── Listener: Channel-Erstellung ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        guild  = channel.guild
        data   = await self.store.read()
        config = _guild_config(data, guild.id)
        if not config["enabled"]:
            return
        entry = await self._latest_audit(guild, discord.AuditLogAction.channel_create)
        if entry and entry.user:
            await self._process_audit(
                guild, discord.AuditLogAction.channel_create,
                entry.user.id, config["threshold_channel"],
                "Massen-Channel-Erstellung", config,
            )

    # ── Listener: Mitglied-Ban ────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        data   = await self.store.read()
        config = _guild_config(data, guild.id)
        if not config["enabled"]:
            return
        entry = await self._latest_audit(guild, discord.AuditLogAction.ban)
        if entry and entry.user:
            await self._process_audit(
                guild, discord.AuditLogAction.ban,
                entry.user.id, config["threshold_ban"],
                "Massen-Ban", config,
            )

    # ── Listener: Mitglied-Kick ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild  = member.guild
        data   = await self.store.read()
        config = _guild_config(data, guild.id)
        if not config["enabled"]:
            return
        entry = await self._latest_audit(guild, discord.AuditLogAction.kick)
        if entry and entry.user and entry.target and entry.target.id == member.id:
            await self._process_audit(
                guild, discord.AuditLogAction.kick,
                entry.user.id, config["threshold_kick"],
                "Massen-Kick", config,
            )

    # ── Listener: Rollen-Löschung ─────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        guild  = role.guild
        data   = await self.store.read()
        config = _guild_config(data, guild.id)
        if not config["enabled"]:
            return
        entry = await self._latest_audit(guild, discord.AuditLogAction.role_delete)
        if entry and entry.user:
            await self._process_audit(
                guild, discord.AuditLogAction.role_delete,
                entry.user.id, config["threshold_role"],
                "Massen-Rollen-Löschung", config,
            )

    # ── Listener: Webhook-Erstellung ──────────────────────────────────────────

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.TextChannel) -> None:
        guild  = channel.guild
        data   = await self.store.read()
        config = _guild_config(data, guild.id)
        if not config["enabled"]:
            return
        entry = await self._latest_audit(guild, discord.AuditLogAction.webhook_create)
        if entry and entry.user:
            await self._process_audit(
                guild, discord.AuditLogAction.webhook_create,
                entry.user.id, config["threshold_webhook"],
                "Massen-Webhook-Erstellung", config,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Slash-Commands
    # ─────────────────────────────────────────────────────────────────────────

    @antinuke.command(name="configure", description="Konfiguriert das Anti-Nuke System.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        aktiviert="System aktivieren/deaktivieren",
        aktion="Reaktion auf erkannten Nuke-Versuch",
        log_kanal="Kanal für Anti-Nuke Meldungen",
        threshold_ban="Max. Bans bevor Reaktion (Standard: 3)",
        threshold_channel="Max. Channel-Löschungen bevor Reaktion (Standard: 3)",
        threshold_rolle="Max. Rollen-Löschungen bevor Reaktion (Standard: 3)",
        owner_alert="DM an Server-Owner senden?",
    )
    @app_commands.choices(aktion=[
        app_commands.Choice(name="🎭 Gefährliche Rollen entziehen (empfohlen)", value="derank"),
        app_commands.Choice(name="🔨 Täter bannen",                              value="ban"),
        app_commands.Choice(name="🔒 Server-Lockdown aktivieren",                value="lockdown"),
    ])
    async def configure(
        self,
        interaction:       discord.Interaction,
        aktiviert:         bool                = None,
        aktion:            str                 = None,
        log_kanal:         discord.TextChannel = None,
        threshold_ban:     app_commands.Range[int, 1, 20] = None,
        threshold_channel: app_commands.Range[int, 1, 20] = None,
        threshold_rolle:   app_commands.Range[int, 1, 20] = None,
        owner_alert:       bool                = None,
    ) -> None:
        def mutate(data: dict) -> dict:
            cfg = data.setdefault(str(interaction.guild.id), {})
            if aktiviert         is not None: cfg["enabled"]           = aktiviert
            if aktion            is not None: cfg["action"]            = aktion
            if log_kanal         is not None: cfg["log_channel_id"]    = log_kanal.id
            if threshold_ban     is not None: cfg["threshold_ban"]     = threshold_ban
            if threshold_channel is not None: cfg["threshold_channel"] = threshold_channel
            if threshold_rolle   is not None: cfg["threshold_role"]    = threshold_rolle
            if owner_alert       is not None: cfg["alert_owner"]       = owner_alert
            return data

        await self.store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("✅ Anti-Nuke konfiguriert", "Einstellungen wurden gespeichert."),
            ephemeral=True,
        )

    @antinuke.command(name="status", description="Zeigt die aktuelle Anti-Nuke-Konfiguration.")
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction) -> None:
        data   = await self.store.read()
        config = _guild_config(data, interaction.guild.id)
        embed  = info_embed(
            "🛡️ Anti-Nuke Status",
            f"**Status:** {'✅ Aktiv' if config['enabled'] else '❌ Inaktiv'}\n"
            f"**Aktion:** `{config['action']}`\n"
            f"**Owner-Alert:** {'✅' if config['alert_owner'] else '❌'}\n"
            "**Log-Kanal:** " + (f"<#{config['log_channel_id']}>" if config['log_channel_id'] else "Nicht gesetzt"),
        )
        embed.add_field(
            name="Schwellwerte",
            value=(
                f"Ban: **{config['threshold_ban']}** · "
                f"Kick: **{config['threshold_kick']}** · "
                f"Channel: **{config['threshold_channel']}** · "
                f"Rollen: **{config['threshold_role']}** · "
                f"Webhooks: **{config['threshold_webhook']}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Whitelist",
            value=f"{len(config['whitelist'])} Nutzer",
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @antinuke.command(name="whitelist", description="Fügt einen Nutzer zur Anti-Nuke-Whitelist hinzu.")
    @app_commands.checks.has_permissions(administrator=True)
    async def whitelist_add(self, interaction: discord.Interaction, nutzer: discord.Member) -> None:
        def mutate(data: dict) -> dict:
            wl = data.setdefault(str(interaction.guild.id), {}).setdefault("whitelist", [])
            if nutzer.id not in wl:
                wl.append(nutzer.id)
            return data

        await self.store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("✅ Whitelist", f"{nutzer.mention} wird vom Anti-Nuke ausgenommen."),
            ephemeral=True,
        )

    @antinuke.command(name="whitelist-remove", description="Entfernt einen Nutzer von der Anti-Nuke-Whitelist.")
    @app_commands.checks.has_permissions(administrator=True)
    async def whitelist_remove(self, interaction: discord.Interaction, nutzer: discord.Member) -> None:
        def mutate(data: dict) -> dict:
            wl = data.setdefault(str(interaction.guild.id), {}).setdefault("whitelist", [])
            if nutzer.id in wl:
                wl.remove(nutzer.id)
            return data

        await self.store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("✅ Whitelist aktualisiert", f"{nutzer.mention} aus der Whitelist entfernt."),
            ephemeral=True,
        )

    @antinuke.command(name="lockdown-lift", description="Hebt den Server-Lockdown wieder auf.")
    @app_commands.checks.has_permissions(administrator=True)
    async def lockdown_lift(self, interaction: discord.Interaction) -> None:
        guild  = interaction.guild
        data   = await self.store.read()
        config = _guild_config(data, guild.id)

        await interaction.response.defer(ephemeral=True)

        for channel in guild.text_channels:
            try:
                overwrites = channel.overwrites_for(guild.default_role)
                if overwrites.send_messages is False:
                    overwrites.send_messages = None  # Auf Standard zurücksetzen
                    await channel.set_permissions(
                        guild.default_role,
                        overwrite=overwrites if any(v is not None for v in overwrites) else None,
                        reason=f"Lockdown aufgehoben von {interaction.user}",
                    )
            except (discord.Forbidden, discord.HTTPException):
                continue

        self._lockdown_active.discard(guild.id)
        await interaction.followup.send(
            embed=success_embed("🔓 Lockdown aufgehoben", "Alle Kanäle wurden entsperrt."),
            ephemeral=True,
        )

    @antinuke.command(name="incidents", description="Zeigt die letzten Anti-Nuke-Vorfälle.")
    @app_commands.checks.has_permissions(administrator=True)
    async def incidents(self, interaction: discord.Interaction) -> None:
        data  = await self.log_store.read()
        items = data.get(str(interaction.guild.id), [])[-10:]
        if not items:
            await interaction.response.send_message(
                embed=info_embed("🛡️ Anti-Nuke Log", "Bisher keine Vorfälle protokolliert."),
                ephemeral=True,
            )
            return
        lines = [
            f"• <@{item['user_id']}> — {item['reason']} — {item['timestamp'][:16]}"
            for item in reversed(items)
        ]
        await interaction.response.send_message(
            embed=warning_embed("🛡️ Letzte Anti-Nuke Vorfälle", "\n".join(lines)),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiNuke(bot))
