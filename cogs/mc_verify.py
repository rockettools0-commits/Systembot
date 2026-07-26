"""
Minecraft ↔ Discord Bridge-Cog für HugoSMP.

Empfängt Events von der Mineflayer-Bridge (mc_bridge/bot.js) via lokalem HTTP.

Events:
  POST /mc-verify  — Verifizierungscode einlösen
  POST /mc-pay     — /pay-Transaktion protokollieren (Clan-Kasse)

Slash-Commands:
  /mc verify      — Startet die Verifizierung
  /mc status      — Zeigt eigene Verknüpfung
  /mc unlink      — Hebt eigene Verknüpfung auf
  /mc whois       — [Mod] Discord-User → MC-Name oder MC-Name → Discord-User
  /mc list        — [Mod] Alle verknüpften Accounts auflisten
  /kasse stand    — Aktueller Clan-Kassenstand
  /kasse history  — Letzte Transaktionen
  /kasse log      — [Admin] Log-Kanal für automatische Pay-Meldungen setzen

Umgebungsvariablen (.env):
  MC_VERIFY_PORT        — Port des internen HTTP-Servers (Standard: 8766)
  MC_BRIDGE_SECRET      — Muss mit mc_bridge/.env BRIDGE_SECRET übereinstimmen
  MC_BOT_NAME           — Ingame-Name des Mineflayer-Bots (für die DM-Anweisung)
  MC_VERIFIED_ROLE_ID   — Optionale Discord-Rollen-ID die nach Verifizierung vergeben wird
  MC_PAY_LOG_CHANNEL_ID — Kanal-ID für automatische Pay-Logs (0 = deaktiviert)
  MC_KASSE_NAME         — MC-Username der als "Clan-Kasse" gilt (empfängt Zahlungen)
"""

from __future__ import annotations

import asyncio
import os
import random
import string
import datetime
from aiohttp import web

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, warning_embed, get_footer_text

MC_LINKS_PATH   = "data/mc_links.json"
MC_PENDING_PATH = "data/mc_pending.json"
MC_KASSE_PATH   = "data/mc_kasse.json"

VERIFY_PORT   = int(os.getenv("MC_VERIFY_PORT",       "8766"))
BRIDGE_SECRET = os.getenv("MC_BRIDGE_SECRET",          "changeme")
MC_BOT_NAME   = os.getenv("MC_BOT_NAME",               "HugoSMPBot")
VERIFIED_ROLE = int(os.getenv("MC_VERIFIED_ROLE_ID",   "0"))
PAY_LOG_CH    = int(os.getenv("MC_PAY_LOG_CHANNEL_ID", "0"))
KASSE_NAME    = os.getenv("MC_KASSE_NAME",              "").lower()   # MC-Name der Clan-Kasse

CODE_TTL_SECONDS = 300   # 5 Minuten


def _gen_code() -> str:
    """Generiert einen einmaligen 8-stelligen Code: DC-XXXXXXXX"""
    chars = string.ascii_uppercase + string.digits
    return "DC-" + "".join(random.choices(chars, k=8))


# ── Discord-Cog ───────────────────────────────────────────────────────────────

class McVerify(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot           = bot
        self.links_store   = JSONStore(MC_LINKS_PATH,   {})
        self.pending_store = JSONStore(MC_PENDING_PATH, {})
        self.kasse_store   = JSONStore(MC_KASSE_PATH,   {"balance": 0.0, "transactions": [], "log_channel_id": PAY_LOG_CH})
        self._runner: web.AppRunner | None = None
        self._pending_dms: dict[str, discord.Message] = {}

    async def cog_load(self):
        await self._start_http_server()

    async def cog_unload(self):
        if self._runner:
            await self._runner.cleanup()

    # ── Interner HTTP-Server ──────────────────────────────────────────────────

    async def _start_http_server(self):
        """Startet einen kleinen aiohttp-Server auf localhost:VERIFY_PORT."""
        app = web.Application()
        app.router.add_post("/mc-verify", self._handle_bridge_post)
        app.router.add_post("/mc-pay",    self._handle_pay_post)
        app.router.add_get("/health",     self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", VERIFY_PORT)
        await site.start()

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "cog": "McVerify"})

    async def _handle_bridge_post(self, request: web.Request) -> web.Response:
        """
        Empfängt POST von der Mineflayer-Bridge:
        { "secret": "...", "mc_username": "Steve", "code": "DC-XXXXXXXX" }
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        if data.get("secret") != BRIDGE_SECRET:
            return web.json_response({"error": "Unauthorized"}, status=403)

        mc_username = str(data.get("mc_username", "UNKNOWN")).strip()
        code        = str(data.get("code", "")).strip().upper()

        if not code.startswith("DC-") or len(code) != 11:
            return web.json_response({"error": "Invalid code format"}, status=400)

        # Pending-Einträge prüfen
        pending = await self.pending_store.read()
        match_discord_id: str | None = None

        for discord_id, entry in list(pending.items()):
            if entry.get("code") == code:
                # TTL prüfen
                created = datetime.datetime.fromisoformat(entry["created_at"])
                age     = (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds()
                if age > CODE_TTL_SECONDS:
                    # Code abgelaufen
                    def remove_expired(d):
                        d.pop(discord_id, None)
                        return d
                    await self.pending_store.update(remove_expired)
                    continue
                match_discord_id = discord_id
                break

        if match_discord_id is None:
            return web.json_response({"error": "Code not found or expired"}, status=404)

        # Verknüpfung speichern
        def save_link(d):
            d[match_discord_id] = {
                "mc_username": mc_username,
                "linked_at":  datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            return d

        def remove_pending(d):
            d.pop(match_discord_id, None)
            return d

        await self.links_store.update(save_link)
        await self.pending_store.update(remove_pending)

        # Discord-User benachrichtigen
        asyncio.create_task(
            self._on_verified(int(match_discord_id), mc_username)
        )

        return web.json_response({"ok": True, "discord_id": match_discord_id, "mc_username": mc_username})

    async def _on_verified(self, discord_id: int, mc_username: str):
        """Wird aufgerufen sobald ein Code erfolgreich eingelöst wurde."""
        user = self.bot.get_user(discord_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(discord_id)
            except discord.HTTPException:
                return

        # Pending-DM löschen
        old_dm = self._pending_dms.pop(str(discord_id), None)
        if old_dm:
            try:
                await old_dm.delete()
            except discord.HTTPException:
                pass

        # Bestätigungs-DM
        embed = success_embed(
            "✅ Minecraft-Account verknüpft!",
            f"Dein Discord-Account ist jetzt mit **{mc_username}** verbunden.\n\n"
            f"Du kannst deine Verknüpfung jederzeit mit `/mc status` prüfen\n"
            f"oder mit `/mc unlink` aufheben.",
        )
        embed.add_field(name="🎮 Minecraft", value=f"`{mc_username}`",         inline=True)
        embed.add_field(name="🏷️ Discord",  value=f"{user.mention}",          inline=True)
        embed.add_field(name="📅 Datum",     value=f"<t:{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}:F>", inline=False)
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            pass

        # Verified-Rolle vergeben (falls konfiguriert)
        if VERIFIED_ROLE:
            for guild in self.bot.guilds:
                member = guild.get_member(discord_id)
                if member is None:
                    continue
                role = guild.get_role(VERIFIED_ROLE)
                if role:
                    try:
                        await member.add_roles(role, reason="MC-Verifizierung abgeschlossen")
                    except discord.HTTPException:
                        pass

    # ── /mc Gruppe ────────────────────────────────────────────────────────────
    mc = app_commands.Group(name="mc", description="Minecraft-Account Verknüpfung.")

    # /mc verify ──────────────────────────────────────────────────────────────

    @mc.command(name="verify", description="Verknüpft deinen Discord-Account mit deinem Minecraft-Account.")
    async def mc_verify(self, interaction: discord.Interaction):
        discord_id = str(interaction.user.id)

        # Bereits verknüpft?
        links = await self.links_store.read()
        if discord_id in links:
            mc_name = links[discord_id]["mc_username"]
            await interaction.response.send_message(
                embed=info_embed(
                    "ℹ️ Bereits verknüpft",
                    f"Dein Account ist bereits mit **{mc_name}** verbunden.\n"
                    f"Nutze `/mc unlink` um die Verknüpfung aufzuheben.",
                ),
                ephemeral=True,
            )
            return

        # Bereits ein offener Code?
        pending = await self.pending_store.read()
        if discord_id in pending:
            entry   = pending[discord_id]
            created = datetime.datetime.fromisoformat(entry["created_at"])
            age     = (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds()
            remaining = int(CODE_TTL_SECONDS - age)
            if remaining > 0:
                await interaction.response.send_message(
                    embed=warning_embed(
                        "⚠️ Verifizierung läuft bereits",
                        f"Du hast bereits einen aktiven Code.\n"
                        f"Schau in deine DMs! Der Code läuft in **{remaining}s** ab.\n\n"
                        f"Befehl: `/msg {MC_BOT_NAME} {entry['code']}`",
                    ),
                    ephemeral=True,
                )
                return

        # Neuen Code generieren
        code    = _gen_code()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def save_pending(d):
            d[discord_id] = {"code": code, "created_at": now_iso}
            return d

        await self.pending_store.update(save_pending)

        # DM an User
        embed = discord.Embed(
            title="🔗 Discord ↔ Minecraft Verifizierung",
            description=(
                "Hier kannst du deinen Discord-Account sicher mit deinem Minecraft-Account verknüpfen.\n\n"
                f"Bitte sende den folgenden Befehl innerhalb von **5 Minuten** exakt so im Spiel:"
            ),
            color=discord.Color.from_rgb(88, 214, 141),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(
            name="📋 Befehl (kopieren!)",
            value=f"```\n/msg {MC_BOT_NAME} {code}\n```",
            inline=False,
        )
        embed.add_field(
            name="⏱️ Gültig bis",
            value=f"<t:{int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + CODE_TTL_SECONDS}:R>",
            inline=True,
        )
        embed.add_field(name="🌐 Server", value=f"`hugosmp.net`", inline=True)
        embed.set_footer(text="Diese Nachricht wird nach erfolgreicher Verifizierung automatisch gelöscht.")

        try:
            dm_msg = await interaction.user.send(embed=embed)
            self._pending_dms[discord_id] = dm_msg
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ DMs deaktiviert",
                    "Ich kann dir keine DM senden. Bitte aktiviere DMs von Server-Mitgliedern und versuche es erneut.",
                ),
                ephemeral=True,
            )
            # Pending-Eintrag wieder entfernen
            def remove_pending(d):
                d.pop(discord_id, None)
                return d
            await self.pending_store.update(remove_pending)
            return

        await interaction.response.send_message(
            embed=success_embed(
                "📬 Code gesendet!",
                f"Ich habe dir eine DM mit dem Verifizierungsbefehl geschickt.\n"
                f"Sende ihn innerhalb von **5 Minuten** im Minecraft-Chat auf `hugosmp.net`.",
            ),
            ephemeral=True,
        )

        # Auto-Cleanup nach TTL
        asyncio.create_task(self._expire_code(discord_id, code))

    async def _expire_code(self, discord_id: str, code: str):
        """Entfernt den Code nach TTL falls er nicht eingelöst wurde."""
        await asyncio.sleep(CODE_TTL_SECONDS + 5)
        pending = await self.pending_store.read()
        if pending.get(discord_id, {}).get("code") == code:
            def remove(d):
                d.pop(discord_id, None)
                return d
            await self.pending_store.update(remove)
            # DM-Nachricht editieren
            old_dm = self._pending_dms.pop(discord_id, None)
            if old_dm:
                try:
                    await old_dm.edit(
                        embed=error_embed(
                            "⏱️ Code abgelaufen",
                            "Dein Verifizierungscode ist abgelaufen.\n"
                            "Starte die Verifizierung erneut mit `/mc verify`.",
                        )
                    )
                except discord.HTTPException:
                    pass

    # /mc status ──────────────────────────────────────────────────────────────

    @mc.command(name="status", description="Zeigt deinen verknüpften Minecraft-Account.")
    async def mc_status(self, interaction: discord.Interaction):
        links = await self.links_store.read()
        entry = links.get(str(interaction.user.id))
        if not entry:
            await interaction.response.send_message(
                embed=info_embed(
                    "ℹ️ Nicht verknüpft",
                    "Du hast noch keinen Minecraft-Account verknüpft.\n"
                    "Nutze `/mc verify` um loszulegen.",
                ),
                ephemeral=True,
            )
            return
        linked_ts = datetime.datetime.fromisoformat(entry["linked_at"])
        embed = success_embed("✅ Minecraft-Verknüpfung")
        embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{entry['mc_username']}/64")
        embed.add_field(name="🎮 Minecraft-Name", value=f"`{entry['mc_username']}`",                       inline=True)
        embed.add_field(name="🏷️ Discord",        value=interaction.user.mention,                         inline=True)
        embed.add_field(name="📅 Verknüpft seit", value=f"<t:{int(linked_ts.timestamp())}:D>",             inline=True)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # /mc unlink ──────────────────────────────────────────────────────────────

    @mc.command(name="unlink", description="Hebt die Verknüpfung mit deinem Minecraft-Account auf.")
    async def mc_unlink(self, interaction: discord.Interaction):
        discord_id = str(interaction.user.id)
        links      = await self.links_store.read()
        if discord_id not in links:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht verknüpft", "Du hast keinen verknüpften Minecraft-Account."),
                ephemeral=True,
            )
            return
        mc_name = links[discord_id]["mc_username"]

        def remove(d):
            d.pop(discord_id, None)
            return d

        await self.links_store.update(remove)

        # Verified-Rolle entfernen
        if VERIFIED_ROLE:
            for guild in self.bot.guilds:
                member = guild.get_member(interaction.user.id)
                if member:
                    role = guild.get_role(VERIFIED_ROLE)
                    if role and role in member.roles:
                        try:
                            await member.remove_roles(role, reason="MC-Verknüpfung aufgehoben")
                        except discord.HTTPException:
                            pass

        await interaction.response.send_message(
            embed=success_embed(
                "✅ Verknüpfung aufgehoben",
                f"Dein Account ist nicht mehr mit **{mc_name}** verbunden.",
            ),
            ephemeral=True,
        )

    # /mc whois ───────────────────────────────────────────────────────────────

    @mc.command(name="whois", description="[Mod] Sucht die Verknüpfung eines Users oder MC-Namens.")
    @app_commands.describe(
        discord_user="Discord-User nachschlagen (optional)",
        mc_name="Minecraft-Name nachschlagen (optional)",
    )
    async def mc_whois(
        self,
        interaction: discord.Interaction,
        discord_user: discord.Member = None,
        mc_name: str = None,
    ):
        from utils.permissions import check_role_permission
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        if not discord_user and not mc_name:
            await interaction.response.send_message(
                embed=error_embed("❌ Kein Parameter", "Bitte gib entweder einen Discord-User oder einen MC-Namen an."),
                ephemeral=True,
            )
            return

        links = await self.links_store.read()

        if discord_user:
            entry = links.get(str(discord_user.id))
            if not entry:
                await interaction.response.send_message(
                    embed=info_embed("ℹ️ Nicht gefunden", f"{discord_user.mention} hat keinen verknüpften MC-Account."),
                    ephemeral=True,
                )
                return
            embed = info_embed(f"🔍 Verknüpfung — {discord_user.display_name}")
            embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{entry['mc_username']}/64")
            embed.add_field(name="🎮 Minecraft",    value=f"`{entry['mc_username']}`",    inline=True)
            embed.add_field(name="🏷️ Discord",      value=discord_user.mention,          inline=True)
            embed.add_field(name="📅 Verknüpft",    value=entry["linked_at"][:10],        inline=True)
            embed.set_footer(text=get_footer_text(interaction))
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # MC-Name suche
        mc_name_lower = mc_name.lower()
        match = next(
            ((did, e) for did, e in links.items() if e["mc_username"].lower() == mc_name_lower),
            None,
        )
        if not match:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Nicht gefunden", f"Kein Discord-Account mit MC-Name **{mc_name}** verknüpft."),
                ephemeral=True,
            )
            return

        discord_id, entry = match
        member = interaction.guild.get_member(int(discord_id)) if interaction.guild else None
        mention = member.mention if member else f"<@{discord_id}>"
        embed = info_embed(f"🔍 Verknüpfung — {entry['mc_username']}")
        embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{entry['mc_username']}/64")
        embed.add_field(name="🎮 Minecraft",  value=f"`{entry['mc_username']}`", inline=True)
        embed.add_field(name="🏷️ Discord",    value=mention,                     inline=True)
        embed.add_field(name="📅 Verknüpft",  value=entry["linked_at"][:10],     inline=True)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # /mc list ────────────────────────────────────────────────────────────────

    @mc.command(name="list", description="[Mod] Zeigt alle verknüpften Minecraft-Accounts.")
    async def mc_list(self, interaction: discord.Interaction):
        from utils.permissions import check_role_permission
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        links = await self.links_store.read()
        if not links:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Keine Verknüpfungen", "Noch kein Account verknüpft."),
                ephemeral=True,
            )
            return

        lines = []
        for discord_id, entry in list(links.items())[:30]:
            member = interaction.guild.get_member(int(discord_id)) if interaction.guild else None
            name   = member.mention if member else f"<@{discord_id}>"
            lines.append(f"🎮 `{entry['mc_username']}` → {name}")

        embed = info_embed(
            f"🔗 Verknüpfte Accounts ({len(links)})",
            "\n".join(lines),
        )
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /mc-pay HTTP-Handler ──────────────────────────────────────────────────

    async def _handle_pay_post(self, request: web.Request) -> web.Response:
        """
        Empfängt POST von der Mineflayer-Bridge:
        { "secret": "...", "sender": "Steve", "receiver": "Alex",
          "amount": 500.0, "currency": "Coins", "raw": "..." }
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        if data.get("secret") != BRIDGE_SECRET:
            return web.json_response({"error": "Unauthorized"}, status=403)

        sender   = str(data.get("sender",   "?"))
        receiver = str(data.get("receiver", "?"))
        try:
            amount = float(data.get("amount", 0))
        except (ValueError, TypeError):
            return web.json_response({"error": "Invalid amount"}, status=400)

        currency = str(data.get("currency", "Coins"))
        raw      = str(data.get("raw",      ""))
        now_iso  = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Nur buchen wenn Clan-Kasse beteiligt ist (falls KASSE_NAME gesetzt)
        kasse_involved = (
            not KASSE_NAME
            or sender.lower()   == KASSE_NAME
            or receiver.lower() == KASSE_NAME
        )

        tx = {
            "ts":       now_iso,
            "sender":   sender,
            "receiver": receiver,
            "amount":   amount,
            "currency": currency,
            "raw":      raw,
        }

        new_balance: float = 0.0

        if kasse_involved:
            # Kassenstand aktualisieren
            def update_kasse(d):
                nonlocal new_balance
                bal = d.get("balance", 0.0)
                if KASSE_NAME:
                    if receiver.lower() == KASSE_NAME:
                        bal += amount   # Einzahlung in die Kasse
                    elif sender.lower() == KASSE_NAME:
                        bal -= amount   # Auszahlung aus der Kasse
                else:
                    bal += amount       # Kein Filter → alles addieren
                d["balance"] = round(bal, 2)
                new_balance  = d["balance"]
                txs = d.setdefault("transactions", [])
                txs.append(tx)
                d["transactions"] = txs[-500:]  # max 500 Einträge
                return d

            await self.kasse_store.update(update_kasse)

        # Log-Kanal benachrichtigen
        asyncio.create_task(self._post_pay_log(tx, new_balance, kasse_involved))

        return web.json_response({"ok": True, "kasse_involved": kasse_involved})

    async def _post_pay_log(self, tx: dict, new_balance: float, kasse_involved: bool):
        """Postet eine Pay-Transaktion in den konfigurierten Log-Kanal."""
        kasse_data = await self.kasse_store.read()
        ch_id      = kasse_data.get("log_channel_id") or PAY_LOG_CH
        if not ch_id:
            return

        channel = self.bot.get_channel(int(ch_id))
        if channel is None:
            return

        # Richtung & Farbe bestimmen
        if KASSE_NAME and tx["receiver"].lower() == KASSE_NAME:
            title  = "📥 Einzahlung — Clan-Kasse"
            color  = discord.Color.from_rgb(88, 214, 141)   # grün
            sign   = "+"
        elif KASSE_NAME and tx["sender"].lower() == KASSE_NAME:
            title  = "📤 Auszahlung — Clan-Kasse"
            color  = discord.Color.from_rgb(235, 77, 75)    # rot
            sign   = "−"
        else:
            title  = "💸 /pay erkannt"
            color  = discord.Color.from_rgb(84, 153, 199)
            sign   = "+"

        embed = discord.Embed(title=title, color=color,
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="👤 Von",       value=f"`{tx['sender']}`",              inline=True)
        embed.add_field(name="👤 An",        value=f"`{tx['receiver']}`",            inline=True)
        embed.add_field(name="💰 Betrag",    value=f"**{sign}{tx['amount']:,.2f} {tx['currency']}**", inline=True)
        if kasse_involved and KASSE_NAME:
            embed.add_field(name="🏦 Kassenstand", value=f"`{new_balance:,.2f} {tx['currency']}`", inline=True)
        if tx["raw"]:
            embed.add_field(name="📋 Original-Nachricht", value=f"```{tx['raw'][:200]}```", inline=False)

        # MC-Username → Discord-User Verknüpfung anzeigen
        links = await self.links_store.read()
        for mc_name in (tx["sender"], tx["receiver"]):
            match = next((did for did, e in links.items() if e["mc_username"].lower() == mc_name.lower()), None)
            if match:
                embed.add_field(name=f"🔗 {mc_name} auf Discord", value=f"<@{match}>", inline=True)

        guild = channel.guild if hasattr(channel, "guild") else None
        embed.set_footer(text=f"{guild.name} │ Clan-Kasse" if guild else "Clan-Kasse")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ── /kasse Gruppe ─────────────────────────────────────────────────────────
    kasse = app_commands.Group(name="kasse", description="Clan-Kasse — Transaktionen & Stand.")

    @kasse.command(name="stand", description="Zeigt den aktuellen Clan-Kassenstand.")
    async def kasse_stand(self, interaction: discord.Interaction):
        data    = await self.kasse_store.read()
        balance = data.get("balance", 0.0)
        txs     = data.get("transactions", [])

        # Letzte Einzahlung & Auszahlung
        last_in  = next((t for t in reversed(txs) if KASSE_NAME and t["receiver"].lower() == KASSE_NAME), None)
        last_out = next((t for t in reversed(txs) if KASSE_NAME and t["sender"].lower()   == KASSE_NAME), None)

        currency = txs[-1]["currency"] if txs else "Coins"
        embed    = discord.Embed(
            title="🏦 Clan-Kasse",
            color=discord.Color.from_rgb(212, 172, 13),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="💰 Kassenstand",       value=f"**{balance:,.2f} {currency}**",   inline=False)
        embed.add_field(name="📊 Transaktionen",     value=str(len(txs)),                       inline=True)
        if last_in:
            embed.add_field(name="📥 Letzte Einzahlung",
                            value=f"`{last_in['sender']}` → **+{last_in['amount']:,.2f}** ({last_in['ts'][:10]})",
                            inline=False)
        if last_out:
            embed.add_field(name="📤 Letzte Auszahlung",
                            value=f"**−{last_out['amount']:,.2f}** → `{last_out['receiver']}` ({last_out['ts'][:10]})",
                            inline=False)
        if KASSE_NAME:
            embed.set_thumbnail(url=f"https://mc-heads.net/avatar/{KASSE_NAME}/64")
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    @kasse.command(name="history", description="Zeigt die letzten Transaktionen der Clan-Kasse.")
    @app_commands.describe(anzahl="Wie viele Einträge? (Standard: 10, max: 25)")
    async def kasse_history(self, interaction: discord.Interaction,
                            anzahl: app_commands.Range[int, 1, 25] = 10):
        data = await self.kasse_store.read()
        txs  = data.get("transactions", [])
        if not txs:
            await interaction.response.send_message(
                embed=info_embed("ℹ️ Keine Transaktionen", "Noch keine /pay-Transaktionen erkannt."),
                ephemeral=True,
            )
            return

        recent   = list(reversed(txs))[:anzahl]
        currency = txs[-1]["currency"]
        lines    = []
        for t in recent:
            direction = "📥" if (KASSE_NAME and t["receiver"].lower() == KASSE_NAME) else "📤"
            lines.append(
                f"{direction} `{t['ts'][:10]}` **{t['sender']}** → **{t['receiver']}**: "
                f"`{t['amount']:,.2f} {t['currency']}`"
            )

        embed = discord.Embed(
            title=f"📋 Clan-Kasse — Letzte {len(recent)} Transaktionen",
            description="\n".join(lines),
            color=discord.Color.from_rgb(84, 153, 199),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="💰 Aktueller Stand", value=f"`{data.get('balance', 0.0):,.2f} {currency}`", inline=True)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    @kasse.command(name="log", description="[Admin] Setzt den Log-Kanal für automatische Pay-Benachrichtigungen.")
    @app_commands.describe(kanal="Kanal für Pay-Logs (leer lassen zum Deaktivieren)")
    @app_commands.checks.has_permissions(administrator=True)
    async def kasse_log(self, interaction: discord.Interaction,
                        kanal: discord.TextChannel = None):
        ch_id = kanal.id if kanal else 0

        def update_log_ch(d):
            d["log_channel_id"] = ch_id
            return d

        await self.kasse_store.update(update_log_ch)

        if kanal:
            await interaction.response.send_message(
                embed=success_embed("✅ Log-Kanal gesetzt",
                                   f"Pay-Logs werden ab jetzt in {kanal.mention} gepostet."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=success_embed("✅ Log-Kanal deaktiviert", "Pay-Logs werden nicht mehr gepostet."),
                ephemeral=True,
            )

    @kasse.command(name="korrektur", description="[Admin] Kassenstand manuell korrigieren.")
    @app_commands.describe(
        betrag="Neuer Kassenstand (absoluter Wert)",
        grund="Grund für die Korrektur",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def kasse_korrektur(self, interaction: discord.Interaction,
                              betrag: float, grund: str = "Manuelle Korrektur"):
        old_balance: list[float] = [0.0]

        def do_korrektur(d):
            old_balance[0] = d.get("balance", 0.0)
            d["balance"]   = round(betrag, 2)
            d.setdefault("transactions", []).append({
                "ts":       datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "sender":   "Admin",
                "receiver": "Kasse",
                "amount":   betrag,
                "currency": "Coins",
                "raw":      f"[Manuelle Korrektur] {grund}",
            })
            return d

        await self.kasse_store.update(do_korrektur)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Kassenstand korrigiert",
                f"**Alt:** `{old_balance[0]:,.2f}`\n**Neu:** `{betrag:,.2f}`\n**Grund:** {grund}",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(McVerify(bot))
