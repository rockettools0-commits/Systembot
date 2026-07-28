"""
Ticket-SLA, Supporter-Analytics und Auto-Close System.

Funktionen:
  • SLA-Überwachung: Tickets über dem Zeitlimit werden im Log-Kanal markiert
  • Supporter-Analytics: Antwortzeiten, geschlossene Tickets, durchschn. Bewertung
  • Auto-Close: Inaktive Tickets (konfigurierbar) werden automatisch geschlossen
  • Tägliche SLA-Reports (optional)
  • /ticket stats — Supporter-Bestenliste mit durchschn. Antwortzeit

Die SLA-Daten werden in data/ticket_sla.json gespeichert,
Analytics in data/ticket_analytics.json.
"""
from __future__ import annotations

import asyncio
import datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.tasks import loop

from utils.storage import JSONStore
from utils.theme import error_embed, info_embed, success_embed, warning_embed

SLA_CONFIG_PATH    = "data/ticket_sla.json"
ANALYTICS_PATH     = "data/ticket_analytics.json"
OPEN_TICKETS_PATH  = "data/tickets_open.json"


def _default_sla() -> dict:
    return {}


def _default_analytics() -> dict:
    return {}


class TicketSLA(commands.Cog):
    """
    SLA-Monitoring, Auto-Close und Supporter-Analytics für das Ticket-System.
    """

    sla = app_commands.Group(name="ticketsla", description="Ticket-SLA und Supporter-Analytics.")

    def __init__(self, bot: commands.Bot):
        self.bot             = bot
        self.sla_store       = JSONStore(SLA_CONFIG_PATH,   _default_sla())
        self.analytics_store = JSONStore(ANALYTICS_PATH,    _default_analytics())
        self.open_store      = JSONStore(OPEN_TICKETS_PATH, {})

    async def cog_load(self) -> None:
        self.sla_check_loop.start()

    async def cog_unload(self) -> None:
        self.sla_check_loop.cancel()

    # ─────────────────────────────────────────────────────────────────────────
    # Konfiguration
    # ─────────────────────────────────────────────────────────────────────────

    def _guild_config(self, data: dict, guild_id: int) -> dict:
        """Liefert SLA-Konfiguration mit sicheren Defaults."""
        stored = data.get(str(guild_id), {})
        return {
            "enabled":           stored.get("enabled", False),
            "sla_hours":         stored.get("sla_hours", 24),          # SLA-Frist in Stunden
            "auto_close_hours":  stored.get("auto_close_hours", 48),   # Auto-Close nach Stunden
            "warn_hours":        stored.get("warn_hours", 20),          # Warnung vor SLA-Ablauf
            "log_channel_id":    stored.get("log_channel_id"),
            "auto_close_enabled": stored.get("auto_close_enabled", False),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API — wird von tickets.py aufgerufen
    # ─────────────────────────────────────────────────────────────────────────

    async def record_open(self, guild_id: int, channel_id: int, user_id: int, panel_name: str) -> None:
        """Registriert Ticket-Öffnung für SLA-Tracking."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def mutate(data: dict) -> dict:
            data.setdefault(str(guild_id), {})[str(channel_id)] = {
                "opened_at":        now,
                "first_response_at": None,   # Wann hat das erste Teammitglied geantwortet?
                "user_id":           user_id,
                "panel_name":        panel_name,
                "sla_warned":        False,
                "sla_breached":      False,
            }
            return data

        await self.analytics_store.update(mutate)

    async def record_support_response(self, guild_id: int, channel_id: int, supporter_id: int) -> None:
        """Speichert die erste Support-Antwort (für Antwortzeit-Messung)."""
        analytics = await self.analytics_store.read()
        ticket = analytics.get(str(guild_id), {}).get(str(channel_id), {})
        if ticket and not ticket.get("first_response_at"):
            def mutate(data: dict) -> dict:
                t = data.get(str(guild_id), {}).get(str(channel_id))
                if t and not t.get("first_response_at"):
                    t["first_response_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    t["first_responder_id"] = supporter_id
                return data
            await self.analytics_store.update(mutate)

    async def record_close(
        self,
        guild_id:    int,
        channel_id:  int,
        closed_by:   int,
        rating:      float | None = None,
    ) -> None:
        """Speichert Ticket-Schließung und berechnet Antwortzeit für Analytics."""
        analytics = await self.analytics_store.read()
        ticket = analytics.get(str(guild_id), {}).get(str(channel_id), {})
        if not ticket:
            return

        now     = datetime.datetime.now(datetime.timezone.utc)
        opened  = datetime.datetime.fromisoformat(ticket["opened_at"])
        total_s = (now - opened).total_seconds()

        first_response_s = None
        if ticket.get("first_response_at"):
            first_resp = datetime.datetime.fromisoformat(ticket["first_response_at"])
            first_response_s = (first_resp - opened).total_seconds()

        # Analytics für den Schließenden/Support-Mitarbeiter
        def mutate(data: dict) -> dict:
            sup_key = str(closed_by)
            guild_stats = data.setdefault(f"stats_{guild_id}", {})
            sup_stats   = guild_stats.setdefault(sup_key, {
                "closed":    0,
                "total_response_s":  0,
                "response_count":    0,
                "total_duration_s":  0,
                "ratings_sum":       0.0,
                "ratings_count":     0,
            })
            sup_stats["closed"]          += 1
            sup_stats["total_duration_s"] += total_s
            if first_response_s is not None:
                sup_stats["total_response_s"] += first_response_s
                sup_stats["response_count"]    += 1
            if rating is not None:
                sup_stats["ratings_sum"]   += rating
                sup_stats["ratings_count"] += 1
            # Ticket-Eintrag aus laufenden Tickets entfernen
            data.get(str(guild_id), {}).pop(str(channel_id), None)
            return data

        await self.analytics_store.update(mutate)

    # ─────────────────────────────────────────────────────────────────────────
    # SLA-Hintergrund-Loop
    # ─────────────────────────────────────────────────────────────────────────

    @loop(minutes=10)
    async def sla_check_loop(self) -> None:
        """Prüft alle 10 Minuten SLA-Fristen und Auto-Close-Bedingungen."""
        try:
            await self._run_sla_check()
        except Exception:
            pass  # Loop darf nie abstürzen

    async def _run_sla_check(self) -> None:
        open_tickets = await self.open_store.read()
        analytics    = await self.analytics_store.read()
        sla_config   = await self.sla_store.read()
        now          = datetime.datetime.now(datetime.timezone.utc)

        for ch_id_str, ticket_info in list(open_tickets.items()):
            guild_id = None
            # Guild-ID aus dem Ticket-Info extrahieren (per Channel)
            for guild in self.bot.guilds:
                if guild.get_channel(int(ch_id_str)):
                    guild_id = guild.id
                    break
            if not guild_id:
                continue

            guild  = self.bot.get_guild(guild_id)
            config = self._guild_config(sla_config, guild_id)
            if not config["enabled"]:
                continue

            opened_iso = ticket_info.get("created_at")
            if not opened_iso:
                continue

            opened = datetime.datetime.fromisoformat(opened_iso)
            age_h  = (now - opened).total_seconds() / 3600

            # SLA-Warnung ausgeben
            a_entry = analytics.get(str(guild_id), {}).get(ch_id_str, {})
            if (
                not a_entry.get("sla_warned")
                and age_h >= config["warn_hours"]
                and age_h < config["sla_hours"]
            ):
                await self._send_sla_warning(guild, int(ch_id_str), ticket_info, age_h, config)
                # sla_warned setzen
                def warn_mutate(data: dict) -> dict:
                    t = data.get(str(guild_id), {}).get(ch_id_str)
                    if t:
                        t["sla_warned"] = True
                    return data
                await self.analytics_store.update(warn_mutate)

            # SLA-Breach melden
            if (
                not a_entry.get("sla_breached")
                and age_h >= config["sla_hours"]
            ):
                await self._send_sla_breach(guild, int(ch_id_str), ticket_info, age_h, config)
                def breach_mutate(data: dict) -> dict:
                    t = data.get(str(guild_id), {}).get(ch_id_str)
                    if t:
                        t["sla_breached"] = True
                    return data
                await self.analytics_store.update(breach_mutate)

            # Auto-Close
            if config["auto_close_enabled"] and age_h >= config["auto_close_hours"]:
                channel = guild.get_channel(int(ch_id_str))
                if isinstance(channel, discord.TextChannel):
                    await self._auto_close(guild, channel, ticket_info)

    async def _send_sla_warning(
        self,
        guild:       discord.Guild,
        channel_id:  int,
        ticket_info: dict,
        age_h:       float,
        config:      dict,
    ) -> None:
        """Schreibt eine SLA-Warnung in den Log-Kanal und das Ticket selbst."""
        log_channel = guild.get_channel(config["log_channel_id"]) if config["log_channel_id"] else None
        ticket_ch   = guild.get_channel(channel_id)

        embed = warning_embed(
            "⏰ SLA-Warnung",
            f"Ticket {f'<#{channel_id}>' if ticket_ch else f'#{channel_id}'} läuft bald ab!\n"
            f"Alter: **{age_h:.1f}h** / SLA: **{config['sla_hours']}h**\n"
            f"Ersteller: <@{ticket_info.get('user_id', '?')}>",
        )

        if log_channel:
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

        if isinstance(ticket_ch, discord.TextChannel):
            try:
                await ticket_ch.send(
                    embed=warning_embed(
                        "⏰ SLA-Erinnerung",
                        f"Dieses Ticket wartet seit **{age_h:.1f} Stunden** auf Antwort. "
                        f"Bitte melde dich so schnell wie möglich!",
                    ),
                )
            except discord.HTTPException:
                pass

    async def _send_sla_breach(
        self,
        guild:       discord.Guild,
        channel_id:  int,
        ticket_info: dict,
        age_h:       float,
        config:      dict,
    ) -> None:
        """Schreibt eine SLA-Verletzung in den Log-Kanal."""
        log_channel = guild.get_channel(config["log_channel_id"]) if config["log_channel_id"] else None
        if not log_channel:
            return
        embed = error_embed(
            "🚨 SLA verletzt!",
            f"Ticket <#{channel_id}> hat die SLA-Frist überschritten!\n"
            f"Alter: **{age_h:.1f}h** / SLA: **{config['sla_hours']}h**\n"
            f"Ersteller: <@{ticket_info.get('user_id', '?')}>\n"
            f"Panel: **{ticket_info.get('anzeige_name', '?')}**",
        )
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    async def _auto_close(
        self,
        guild:       discord.Guild,
        channel:     discord.TextChannel,
        ticket_info: dict,
    ) -> None:
        """Schließt ein Ticket automatisch wegen Inaktivität."""
        tickets_cog = self.bot.get_cog("Tickets")
        if tickets_cog is None:
            return

        # Letzte Nachricht prüfen
        try:
            messages = [m async for m in channel.history(limit=1)]
            if messages:
                last_msg = messages[0]
                inactive = (datetime.datetime.now(datetime.timezone.utc) - last_msg.created_at).total_seconds() / 3600
                sla_cfg  = self._guild_config(await self.sla_store.read(), guild.id)
                if inactive < sla_cfg["auto_close_hours"]:
                    return  # Noch nicht lang genug inaktiv
        except discord.HTTPException:
            return

        try:
            await channel.send(
                embed=info_embed(
                    "🔒 Auto-Close",
                    "Dieses Ticket wird wegen Inaktivität automatisch geschlossen.",
                )
            )
        except discord.HTTPException:
            pass

        # Ticket über den normalen Schließ-Flow schließen
        # (erfordert ein Mock-Interaction – direkt per Store + Channel-Delete)
        def mutate(data: dict) -> dict:
            data.pop(str(channel.id), None)
            return data

        open_store = JSONStore(OPEN_TICKETS_PATH, {})
        await open_store.update(mutate)
        await asyncio.sleep(3)
        try:
            await channel.delete(reason="Auto-Close: Inaktivität")
        except discord.HTTPException:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Slash-Commands
    # ─────────────────────────────────────────────────────────────────────────

    @sla.command(name="configure", description="Konfiguriert SLA-Einstellungen.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        sla_stunden="SLA-Frist in Stunden (Standard: 24)",
        warn_stunden="Wann eine Warnung gesendet wird (vor Ablauf)",
        autoclose_stunden="Ticket schließen nach X Stunden Inaktivität (0 = deaktiviert)",
        log_kanal="Kanal für SLA-Benachrichtigungen",
        aktiviert="SLA-System aktivieren",
    )
    async def sla_configure(
        self,
        interaction:        discord.Interaction,
        sla_stunden:        app_commands.Range[int, 1, 720]    = None,
        warn_stunden:       app_commands.Range[int, 1, 720]    = None,
        autoclose_stunden:  app_commands.Range[int, 0, 720]    = None,
        log_kanal:          discord.TextChannel                 = None,
        aktiviert:          bool                                = None,
    ) -> None:
        def mutate(data: dict) -> dict:
            cfg = data.setdefault(str(interaction.guild.id), {})
            if sla_stunden       is not None: cfg["sla_hours"]          = sla_stunden
            if warn_stunden      is not None: cfg["warn_hours"]         = warn_stunden
            if autoclose_stunden is not None:
                cfg["auto_close_hours"]   = autoclose_stunden
                cfg["auto_close_enabled"] = autoclose_stunden > 0
            if log_kanal         is not None: cfg["log_channel_id"]     = log_kanal.id
            if aktiviert         is not None: cfg["enabled"]            = aktiviert
            return data

        await self.sla_store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("✅ SLA konfiguriert", "Die SLA-Einstellungen wurden gespeichert."),
            ephemeral=True,
        )

    @sla.command(name="status", description="Zeigt die aktuelle SLA-Konfiguration.")
    @app_commands.checks.has_permissions(administrator=True)
    async def sla_status(self, interaction: discord.Interaction) -> None:
        data   = await self.sla_store.read()
        config = self._guild_config(data, interaction.guild.id)
        embed  = info_embed(
            "⏱️ SLA-Konfiguration",
            f"**Status:** {'✅ Aktiv' if config['enabled'] else '❌ Inaktiv'}\n"
            f"**SLA-Frist:** {config['sla_hours']}h\n"
            f"**Warnung bei:** {config['warn_hours']}h\n"
            f"**Auto-Close:** {'✅ ' + str(config['auto_close_hours']) + 'h Inaktivität' if config['auto_close_enabled'] else '❌ Deaktiviert'}\n"
            "**Log-Kanal:** " + (f"<#{config['log_channel_id']}>" if config['log_channel_id'] else "Nicht gesetzt"),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @sla.command(name="stats", description="Zeigt Supporter-Analytics und Ticket-Statistiken.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def sla_stats(self, interaction: discord.Interaction) -> None:
        data        = await self.analytics_store.read()
        guild_stats = data.get(f"stats_{interaction.guild.id}", {})

        if not guild_stats:
            await interaction.response.send_message(
                embed=info_embed("📊 Supporter-Analytics", "Noch keine Daten vorhanden."),
                ephemeral=True,
            )
            return

        lines = []
        # Sortiert nach geschlossenen Tickets
        sorted_supporters = sorted(
            guild_stats.items(),
            key=lambda x: x[1].get("closed", 0),
            reverse=True,
        )

        for i, (uid, stats) in enumerate(sorted_supporters[:10], 1):
            closed   = stats.get("closed", 0)
            avg_resp = (
                stats["total_response_s"] / stats["response_count"] / 60
                if stats.get("response_count", 0) > 0 else None
            )
            avg_rating = (
                stats["ratings_sum"] / stats["ratings_count"]
                if stats.get("ratings_count", 0) > 0 else None
            )
            resp_str   = f"{avg_resp:.0f}min" if avg_resp is not None else "—"
            rating_str = f"{avg_rating:.1f}⭐" if avg_rating is not None else "—"
            lines.append(
                f"**{i}.** <@{uid}> — **{closed}** geschlossen · ⏱️ {resp_str} · {rating_str}"
            )

        embed = info_embed("📊 Supporter-Analytics", "\n".join(lines))
        embed.set_footer(text="Ø Antwortzeit (Öffnung→1. Support-Antwort) | AVOKE SLA")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @sla.command(name="leaderboard", description="Supporter-Rangliste nach geschlossenen Tickets.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def sla_leaderboard(self, interaction: discord.Interaction) -> None:
        await self.sla_stats(interaction)  # Gleiche Ausgabe — Alias

    @sla.command(name="reset-stats", description="Setzt alle Supporter-Analytics für diesen Server zurück.")
    @app_commands.checks.has_permissions(administrator=True)
    async def sla_reset_stats(self, interaction: discord.Interaction) -> None:
        """Entfernt alle aggregierten Supporter-Statistiken für diese Guild."""
        stats_key = f"stats_{interaction.guild.id}"

        def mutate(data: dict) -> dict:
            data.pop(stats_key, None)
            data.pop(str(interaction.guild.id), None)  # Auch offene Ticket-Daten
            return data

        await self.analytics_store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed(
                "🗑️ Analytics zurückgesetzt",
                "Alle Supporter-Statistiken für diesen Server wurden gelöscht.",
            ),
            ephemeral=True,
        )

    @sla.command(name="report", description="Erstellt einen vollständigen SLA-Report für diesen Server.")
    @app_commands.checks.has_permissions(administrator=True)
    async def sla_report(self, interaction: discord.Interaction) -> None:
        """Erstellt einen kombinierten Report aus SLA-Config, Stats und offenen Tickets."""
        import datetime as _dt

        await interaction.response.defer(ephemeral=True)

        sla_data    = await self.sla_store.read()
        analytics   = await self.analytics_store.read()
        open_data   = await self.open_store.read()
        config      = self._guild_config(sla_data, interaction.guild.id)
        guild_stats = analytics.get(f"stats_{interaction.guild.id}", {})

        # Offene Tickets zählen (alle Tickets auf diesem Server)
        open_count  = sum(1 for ch_id in open_data)
        # Gesamtstatistiken aggregieren
        total_closed    = sum(s.get("closed", 0)         for s in guild_stats.values())
        total_responses = sum(s.get("response_count", 0) for s in guild_stats.values())
        total_resp_s    = sum(s.get("total_response_s", 0) for s in guild_stats.values())
        avg_resp_min    = (
            round(total_resp_s / total_responses / 60, 1)
            if total_responses > 0 else None
        )
        total_ratings   = sum(s.get("ratings_count", 0) for s in guild_stats.values())
        total_rating_s  = sum(s.get("ratings_sum", 0)   for s in guild_stats.values())
        avg_rating      = round(total_rating_s / total_ratings, 2) if total_ratings > 0 else None

        embed = info_embed(
            "📋 SLA-Report",
            (
                f"**SLA-System:** {'✅ Aktiv' if config['enabled'] else '❌ Inaktiv'}\n"
                f"**SLA-Frist:** {config['sla_hours']}h\n"
                f"**Warnung bei:** {config['warn_hours']}h\n"
                f"**Auto-Close:** "
                + (f"✅ nach {config['auto_close_hours']}h Inaktivität" if config['auto_close_enabled'] else "❌")
            ),
        )
        embed.add_field(
            name  = "📊 Gesamt-Statistiken",
            value = (
                f"Offene Tickets: **{open_count}**\n"
                f"Geschlossene: **{total_closed}**\n"
                f"Ø Antwortzeit: **{str(avg_resp_min) + 'min' if avg_resp_min else '—'}**\n"
                f"Ø Bewertung: **{str(avg_rating) + ' ⭐' if avg_rating else '—'}**"
            ),
            inline=True,
        )
        embed.add_field(
            name  = "👥 Supporter aktiv",
            value = str(len(guild_stats)),
            inline=True,
        )
        embed.set_footer(text=f"Report erstellt von {interaction.user} | AVOKE SLA")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Erfasst erste Support-Antwort für Antwortzeit-Messung."""
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        open_store = JSONStore(OPEN_TICKETS_PATH, {})
        open_tickets = await open_store.read()
        ticket_info = open_tickets.get(str(message.channel.id))
        if not ticket_info:
            return

        # Prüfen ob Absender Teil des Support-Teams ist
        support_role_ids = ticket_info.get("support_role_ids", [])
        is_support = (
            message.author.guild_permissions.administrator
            or any(r.id in support_role_ids for r in message.author.roles)
        )
        # Eigene Nachrichten des Ticket-Erstellers ignorieren
        if message.author.id == ticket_info.get("user_id"):
            return

        if is_support:
            await self.record_support_response(
                message.guild.id,
                message.channel.id,
                message.author.id,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketSLA(bot))
