"""
Owner-System — Verwaltungscommands, die ausschließlich der in der .env
konfigurierten OWNER_ID zur Verfügung stehen.

Nutzt discord.py's eingebauten `@commands.is_owner()`-Check, welcher gegen
`bot.owner_id` prüft (in main.py aus OWNER_ID geladen). Alle anderen
Benutzer erhalten "❌ Keine Berechtigung." (siehe main.py on_command_error).

Commands:
  !tickets         — Übersicht aller aktuell offenen Tickets
  !dm-all <text>   — sendet allen Mitgliedern des Servers eine DM (mit Bestätigung)

Systemverwaltung (Reload, Restart, Sync, Stats …) → !system * in system_tools.py
Jede Nutzung dieser Commands wird geloggt (User, Uhrzeit, Command).
"""

from __future__ import annotations

import asyncio
import datetime

import discord
from discord.ext import commands

from utils.logger import get_logger
from utils.owners import is_owner
from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, info_embed, warning_embed, FOOTER_TEXT, COLOR_INFO, get_footer_text

from cogs.tickets import OPEN_TICKETS_PATH

log = get_logger("system")

_open_tickets_store = JSONStore(OPEN_TICKETS_PATH, {})


class _ConfirmView(discord.ui.View):
    """Kleine Bestätigungsabfrage, ausschließlich für den Command-Auslöser."""

    def __init__(self, author_id: int, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Nur der Owner, der den Befehl ausgelöst hat, kann das bestätigen.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="✅ Ja, an alle senden", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="❌ Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


class OwnerAdmin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_action(self, ctx: commands.Context, action: str) -> None:
        """Loggt jede Owner-Command-Nutzung mit User, Uhrzeit und Command."""
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log.info(f"[OWNER-COMMAND] {action} | User: {ctx.author} ({ctx.author.id}) | Zeit: {now}")

    # ─────────────────────────────────────────────────────────────────────────
    # !tickets
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="tickets")
    async def tickets_overview(self, ctx: commands.Context):
        if not is_owner(ctx.author.id):
            return
        await self._log_action(ctx, "Tickets-Übersicht")

        open_tickets = await _open_tickets_store.read()
        if not open_tickets:
            await ctx.send(embed=info_embed("🎫 Offene Tickets", "Aktuell sind keine Tickets geöffnet."))
            return

        lines = []
        for channel_id, info in open_tickets.items():
            channel = ctx.guild.get_channel(int(channel_id)) if ctx.guild else None
            channel_display = channel.mention if channel else f"`#{channel_id}` (gelöscht?)"
            owner_display = f"<@{info.get('user_id')}>"
            panel = info.get("anzeige_name", "Unbekannt")
            created = info.get("created_at", "unbekannt")
            lines.append(f"{channel_display} — **{panel}** von {owner_display}\n> Erstellt: {created[:19].replace('T', ' ')} UTC")

        embed = discord.Embed(
            title=f"🎫 Offene Tickets ({len(open_tickets)})",
            description="\n\n".join(lines)[:4000],
            color=COLOR_INFO,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # !dm-all <Nachricht>
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="dm-all")
    async def dm_all(self, ctx: commands.Context, *, message: str):
        if not is_owner(ctx.author.id):
            return
        """Sendet allen (menschlichen) Mitgliedern des Servers eine DM."""
        if ctx.guild is None:
            await ctx.send(embed=error_embed("❌ Fehler", "Dieser Command muss auf einem Server ausgeführt werden."))
            return

        targets = [m for m in ctx.guild.members if not m.bot]
        if not targets:
            await ctx.send(embed=error_embed("❌ Keine Empfänger", "Es wurden keine passenden Mitglieder gefunden."))
            return

        preview = message if len(message) <= 1000 else message[:1000] + "…"
        confirm_embed = warning_embed(
            "⚠️ DM an alle Mitglieder bestätigen",
            f"Du bist dabei, **{len(targets)}** Mitgliedern eine DM zu senden.\n\n"
            f"**Vorschau:**\n>>> {preview}",
        )
        view = _ConfirmView(ctx.author.id)
        confirm_msg = await ctx.send(embed=confirm_embed, view=view)

        timed_out = await view.wait()
        if timed_out or view.value is not True:
            status_embed = (
                info_embed("⌛ Zeit abgelaufen", "Es wurde nicht rechtzeitig bestätigt — keine Nachrichten versendet.")
                if timed_out
                else info_embed("❎ Abgebrochen", "Es wurden keine Nachrichten versendet.")
            )
            await confirm_msg.edit(embed=status_embed, view=None)
            return

        await self._log_action(ctx, f"DM-All ({len(targets)} Empfänger)")

        progress_msg = await ctx.send(embed=info_embed("📨 Sende DMs …", f"0 / {len(targets)} verarbeitet."))

        sent, blocked, failed = 0, 0, 0
        for i, member in enumerate(targets, start=1):
            try:
                dm_embed = discord.Embed(
                    title=f"📩 Nachricht von {ctx.guild.name}",
                    description=message,
                    color=COLOR_INFO,
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                )
                if ctx.guild.icon:
                    dm_embed.set_thumbnail(url=ctx.guild.icon.url)
                dm_embed.set_footer(text=get_footer_text(ctx.guild))
                await member.send(embed=dm_embed)
                sent += 1
            except discord.Forbidden:
                blocked += 1
            except discord.HTTPException:
                failed += 1

            if i % 15 == 0 or i == len(targets):
                try:
                    await progress_msg.edit(
                        embed=info_embed(
                            "📨 Sende DMs …",
                            f"{i} / {len(targets)} verarbeitet.\n"
                            f"✅ {sent} gesendet · 🚫 {blocked} blockiert · ⚠️ {failed} fehlgeschlagen",
                        )
                    )
                except discord.HTTPException:
                    pass

            await asyncio.sleep(1.2)  # Rate-Limit-Schutz

        log.info(
            f"[OWNER-COMMAND] DM-All abgeschlossen | Gesendet: {sent} | Blockiert: {blocked} | "
            f"Fehlgeschlagen: {failed} | User: {ctx.author} ({ctx.author.id})"
        )

        result_embed = success_embed(
            "✅ DM-Versand abgeschlossen",
            f"**{sent}** erfolgreich gesendet\n"
            f"**{blocked}** haben DMs deaktiviert (blockiert)\n"
            f"**{failed}** fehlgeschlagen (anderer Fehler)",
        )
        await progress_msg.edit(embed=result_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerAdmin(bot))
