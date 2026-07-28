"""
Captcha, Quarantäne und Alt-Account-Erkennung.

Funktionen:
  • Math-Captcha bei Beitritt (Rechenaufgabe in einer Embed-Nachricht mit Button)
  • Quarantäne-Rolle: neue Mitglieder erhalten zunächst eine eingeschränkte Rolle
  • Alt-Account-Erkennung: Accounts unter X Tagen werden markiert/geloggt
  • Konfigurierbare Schwellwerte und Aktionen (log, quarantine, kick)
  • Automatische Freigabe nach bestandenem Captcha (Quarantäne-Rolle wird entfernt)

Konfiguration: /captcha configure
"""
from __future__ import annotations

import asyncio
import datetime
import random
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import error_embed, info_embed, success_embed, warning_embed

CONFIG_PATH   = "data/captcha_config.json"
PENDING_PATH  = "data/captcha_pending.json"


def _default_config() -> dict:
    return {}


def _guild_config(data: dict, guild_id: int) -> dict:
    """Gibt die Guild-Konfiguration mit sicheren Defaults zurück."""
    stored = data.get(str(guild_id), {})
    return {
        "enabled":              stored.get("enabled", False),
        "captcha_enabled":      stored.get("captcha_enabled", True),
        "captcha_channel_id":   stored.get("captcha_channel_id"),       # Kanal für Captcha-Nachrichten
        "quarantine_role_id":   stored.get("quarantine_role_id"),       # Rolle für unverifiedte Mitglieder
        "member_role_id":       stored.get("member_role_id"),           # Rolle nach bestandenem Captcha
        "log_channel_id":       stored.get("log_channel_id"),
        "alt_min_days":         stored.get("alt_min_days", 30),         # Account muss X Tage alt sein
        "alt_action":           stored.get("alt_action", "log"),        # log | quarantine | kick
        "captcha_timeout_min":  stored.get("captcha_timeout_min", 10),  # Minuten bis Auto-Kick
    }


# ── Captcha View ──────────────────────────────────────────────────────────────

class CaptchaView(discord.ui.View):
    """
    Zeigt eine einfache Mathe-Rechenaufgabe als persistenten Button.
    Der Nutzer muss per Modal die korrekte Zahl eingeben.
    """

    def __init__(self, member_id: int, answer: int, guild_id: int):
        super().__init__(timeout=None)
        self.member_id = member_id
        self.answer    = answer
        self.guild_id  = guild_id
        self.verify_btn.custom_id = f"captcha_verify:{guild_id}:{member_id}"

    @discord.ui.button(label="✅ Antwort eingeben", style=discord.ButtonStyle.success)
    async def verify_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        # Nur der betroffene Nutzer darf antworten
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("❌ Das ist nicht dein Captcha.", ephemeral=True)
            return
        await interaction.response.send_modal(
            CaptchaModal(self.member_id, self.answer, self.guild_id)
        )


class CaptchaModal(discord.ui.Modal, title="🔐 Captcha lösen"):
    """Nimmt die Captcha-Antwort des Nutzers entgegen."""

    antwort = discord.ui.TextInput(
        label="Deine Antwort (nur Zahl)",
        placeholder="z.B. 42",
        min_length=1,
        max_length=10,
    )

    def __init__(self, member_id: int, answer: int, guild_id: int):
        super().__init__()
        self.member_id = member_id
        self.answer    = answer
        self.guild_id  = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog: CaptchaCog | None = interaction.client.get_cog("CaptchaCog")
        if cog is None:
            await interaction.response.send_message("System nicht verfügbar.", ephemeral=True)
            return

        try:
            user_answer = int(self.antwort.value.strip())
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültige Eingabe", "Bitte gib nur eine Zahl ein."),
                ephemeral=True,
            )
            return

        if user_answer == self.answer:
            await cog.pass_captcha(interaction, self.member_id, self.guild_id)
        else:
            # Fehlversuch zählen
            await cog.fail_captcha(interaction, self.member_id, self.guild_id)


# ── Captcha Cog ───────────────────────────────────────────────────────────────

class CaptchaCog(commands.Cog, name="CaptchaCog"):
    """Captcha-Verifikation, Quarantäne-Modus und Alt-Account-Erkennung."""

    captcha_group = app_commands.Group(name="captcha", description="Captcha & Verifikations-Einstellungen.")

    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self.config_store = JSONStore(CONFIG_PATH,  _default_config())
        self.pending      = JSONStore(PENDING_PATH, {})   # {guild_id: {user_id: {answer, attempts, msg_id}}}

    async def _send_log(
        self,
        guild:   discord.Guild,
        config:  dict,
        embed:   discord.Embed,
    ) -> None:
        log_ch = guild.get_channel(config["log_channel_id"]) if config["log_channel_id"] else None
        if isinstance(log_ch, discord.TextChannel):
            try:
                await log_ch.send(embed=embed)
            except discord.HTTPException:
                pass

    # ── Captcha-Logik ─────────────────────────────────────────────────────────

    @staticmethod
    def _generate_captcha() -> tuple[str, int]:
        """Generiert eine einfache Rechenaufgabe und die korrekte Antwort."""
        ops = [
            ("+",  lambda a, b: a + b),
            ("-",  lambda a, b: a - b),
            ("×",  lambda a, b: a * b),
        ]
        a   = random.randint(2, 15)
        b   = random.randint(2, 15)
        op_str, op_fn = random.choice(ops)
        return f"{a} {op_str} {b}", op_fn(a, b)

    async def _send_captcha(self, member: discord.Member, config: dict) -> None:
        """Sendet das Captcha in den konfigurierten Kanal oder per DM."""
        question, answer = self._generate_captcha()

        embed = discord.Embed(
            title="🔐 Captcha-Verifikation",
            description=(
                f"Willkommen auf **{member.guild.name}**!\n\n"
                f"Bitte löse die folgende Aufgabe, um Zugriff zu erhalten:\n\n"
                f"## `{question} = ?`\n\n"
                f"Klicke auf den Button und gib das Ergebnis ein.\n"
                f"Du hast **{config['captcha_timeout_min']} Minuten** Zeit."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=f"{member.guild.name} │ Sicherheitssystem")

        view = CaptchaView(member.id, answer, member.guild.id)
        sent_msg: discord.Message | None = None

        captcha_ch = member.guild.get_channel(config["captcha_channel_id"])
        if isinstance(captcha_ch, discord.TextChannel):
            try:
                sent_msg = await captcha_ch.send(
                    content=member.mention,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass
        else:
            # Fallback: Per DM senden
            try:
                sent_msg = await member.send(embed=embed, view=view)
            except (discord.Forbidden, discord.HTTPException):
                pass

        if sent_msg:
            # Captcha-Daten speichern
            def mutate(data: dict) -> dict:
                data.setdefault(str(member.guild.id), {})[str(member.id)] = {
                    "answer":     answer,
                    "attempts":   0,
                    "msg_id":     sent_msg.id,
                    "channel_id": sent_msg.channel.id,
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
                return data
            await self.pending.update(mutate)

            # Timeout-Task starten
            asyncio.create_task(
                self._captcha_timeout(member, config, sent_msg)
            )

    async def _captcha_timeout(
        self,
        member:   discord.Member,
        config:   dict,
        message:  discord.Message,
    ) -> None:
        """Kickt den Nutzer, wenn das Captcha nach X Minuten nicht gelöst wurde."""
        await asyncio.sleep(config["captcha_timeout_min"] * 60)

        # Prüfen ob noch ausstehend
        pending = await self.pending.read()
        if str(member.id) not in pending.get(str(member.guild.id), {}):
            return  # Bereits gelöst

        # Captcha-Nachricht löschen
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        # Pending-Eintrag entfernen
        def mutate(data: dict) -> dict:
            data.get(str(member.guild.id), {}).pop(str(member.id), None)
            return data
        await self.pending.update(mutate)

        # Nutzer kicken
        try:
            await member.kick(reason="Captcha nicht bestanden (Timeout)")
            await self._send_log(
                member.guild, config,
                warning_embed(
                    "🔐 Captcha-Timeout",
                    f"{member.mention} wurde gekickt — Captcha nicht rechtzeitig gelöst.",
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def pass_captcha(
        self,
        interaction: discord.Interaction,
        member_id:   int,
        guild_id:    int,
    ) -> None:
        """Verarbeitet ein erfolgreich gelöstes Captcha."""
        guild  = self.bot.get_guild(guild_id)
        member = guild.get_member(member_id) if guild else None

        config_data = await self.config_store.read()
        config      = _guild_config(config_data, guild_id)

        # Pending entfernen
        def mutate(data: dict) -> dict:
            data.get(str(guild_id), {}).pop(str(member_id), None)
            return data
        await self.pending.update(mutate)

        # Captcha-Nachricht löschen (falls im selben Kanal)
        try:
            await interaction.message.delete()
        except (discord.HTTPException, AttributeError):
            pass

        if member and guild:
            # Quarantäne-Rolle entfernen
            quarantine_role = guild.get_role(config["quarantine_role_id"]) if config["quarantine_role_id"] else None
            if quarantine_role and quarantine_role in member.roles:
                try:
                    await member.remove_roles(quarantine_role, reason="Captcha bestanden")
                except discord.HTTPException:
                    pass

            # Member-Rolle hinzufügen
            member_role = guild.get_role(config["member_role_id"]) if config["member_role_id"] else None
            if member_role:
                try:
                    await member.add_roles(member_role, reason="Captcha bestanden")
                except discord.HTTPException:
                    pass

            await self._send_log(
                guild, config,
                success_embed("✅ Captcha bestanden", f"{member.mention} hat das Captcha gelöst und Zugriff erhalten."),
            )

        await interaction.response.send_message(
            embed=success_embed("✅ Verifiziert!", "Du hast das Captcha gelöst und erhältst jetzt Zugriff."),
            ephemeral=True,
        )

    async def fail_captcha(
        self,
        interaction: discord.Interaction,
        member_id:   int,
        guild_id:    int,
    ) -> None:
        """Zählt einen Fehlversuch. Nach 3 Fehlversuchen → Kick."""
        pending = await self.pending.read()
        entry   = pending.get(str(guild_id), {}).get(str(member_id), {})
        attempts = entry.get("attempts", 0) + 1

        def mutate(data: dict) -> dict:
            t = data.get(str(guild_id), {}).get(str(member_id))
            if t:
                t["attempts"] = attempts
            return data
        await self.pending.update(mutate)

        if attempts >= 3:
            # Zu viele Fehlversuche → Kick
            guild  = self.bot.get_guild(guild_id)
            member = guild.get_member(member_id) if guild else None

            config_data = await self.config_store.read()
            config      = _guild_config(config_data, guild_id)

            def rm_mutate(data: dict) -> dict:
                data.get(str(guild_id), {}).pop(str(member_id), None)
                return data
            await self.pending.update(rm_mutate)

            await interaction.response.send_message(
                embed=error_embed("❌ Zu viele Fehlversuche", "Du wirst vom Server entfernt."),
                ephemeral=True,
            )
            if member:
                try:
                    await member.kick(reason="Captcha nicht bestanden (3 Fehlversuche)")
                    await self._send_log(
                        guild, config,
                        warning_embed("🔐 Captcha fehlgeschlagen", f"{member.mention} wurde nach 3 Fehlversuchen gekickt."),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
        else:
            await interaction.response.send_message(
                embed=warning_embed(
                    f"❌ Falsch! ({attempts}/3 Versuche)",
                    "Versuche es noch einmal.",
                ),
                ephemeral=True,
            )

    # ── Alt-Account-Erkennung ─────────────────────────────────────────────────

    async def _check_alt(self, member: discord.Member, config: dict) -> None:
        """Prüft ob ein Konto ein potenzieller Alt-Account ist."""
        age_days = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
        if age_days >= config["alt_min_days"]:
            return  # Kein Alt-Account

        action = config["alt_action"]
        embed  = warning_embed(
            "🕵️ Möglicher Alt-Account",
            f"**Mitglied:** {member.mention}\n"
            f"**Account-Alter:** {age_days} Tage\n"
            f"**Mindest-Alter:** {config['alt_min_days']} Tage\n"
            f"**Aktion:** `{action}`",
        )

        await self._send_log(member.guild, config, embed)

        if action == "quarantine":
            quarantine_role = member.guild.get_role(config["quarantine_role_id"])
            if quarantine_role:
                try:
                    await member.add_roles(quarantine_role, reason=f"Alt-Account-Erkennung: {age_days} Tage alt")
                except discord.HTTPException:
                    pass

        elif action == "kick":
            try:
                await member.kick(reason=f"Alt-Account: Konto nur {age_days} Tage alt")
            except (discord.Forbidden, discord.HTTPException):
                pass

    # ── Listener ─────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        data   = await self.config_store.read()
        config = _guild_config(data, member.guild.id)

        if not config["enabled"]:
            return

        # Alt-Account-Check immer zuerst
        await self._check_alt(member, config)

        # Quarantäne-Rolle sofort zuweisen (unabhängig vom Captcha)
        if config["quarantine_role_id"]:
            quarantine_role = member.guild.get_role(config["quarantine_role_id"])
            if quarantine_role:
                try:
                    await member.add_roles(quarantine_role, reason="Quarantäne: Neues Mitglied")
                except discord.HTTPException:
                    pass

        # Captcha versenden
        if config["captcha_enabled"] and config["captcha_channel_id"]:
            await self._send_captcha(member, config)

    # ── Slash-Commands ────────────────────────────────────────────────────────

    @captcha_group.command(name="configure", description="Konfiguriert das Captcha- und Quarantäne-System.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        aktiviert="System aktivieren/deaktivieren",
        captcha_kanal="Kanal in dem Captchas erscheinen",
        quarantaene_rolle="Rolle für neue, unverifizierende Mitglieder",
        member_rolle="Rolle nach bestandenem Captcha",
        log_kanal="Kanal für Sicherheitsmeldungen",
        alt_tage="Account-Mindestalter in Tagen",
        alt_aktion="Aktion bei Alt-Account-Erkennung",
        captcha_timeout="Minuten bis Auto-Kick bei nicht gelöstem Captcha",
    )
    @app_commands.choices(alt_aktion=[
        app_commands.Choice(name="📋 Nur loggen",        value="log"),
        app_commands.Choice(name="🔒 Quarantäne geben",  value="quarantine"),
        app_commands.Choice(name="👢 Sofort kicken",      value="kick"),
    ])
    async def captcha_configure(
        self,
        interaction:    discord.Interaction,
        aktiviert:      bool                    = None,
        captcha_kanal:  discord.TextChannel     = None,
        quarantaene_rolle: discord.Role         = None,
        member_rolle:   discord.Role            = None,
        log_kanal:      discord.TextChannel     = None,
        alt_tage:       app_commands.Range[int, 1, 365] = None,
        alt_aktion:     str                     = None,
        captcha_timeout: app_commands.Range[int, 1, 60] = None,
    ) -> None:
        def mutate(data: dict) -> dict:
            cfg = data.setdefault(str(interaction.guild.id), {})
            if aktiviert         is not None: cfg["enabled"]             = aktiviert
            if captcha_kanal     is not None: cfg["captcha_channel_id"]  = captcha_kanal.id
            if quarantaene_rolle is not None: cfg["quarantine_role_id"]  = quarantaene_rolle.id
            if member_rolle      is not None: cfg["member_role_id"]      = member_rolle.id
            if log_kanal         is not None: cfg["log_channel_id"]      = log_kanal.id
            if alt_tage          is not None: cfg["alt_min_days"]        = alt_tage
            if alt_aktion        is not None: cfg["alt_action"]          = alt_aktion
            if captcha_timeout   is not None: cfg["captcha_timeout_min"] = captcha_timeout
            return data

        await self.config_store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("✅ Captcha/Quarantäne konfiguriert", "Einstellungen gespeichert."),
            ephemeral=True,
        )

    @captcha_group.command(name="status", description="Zeigt die Captcha-Konfiguration.")
    @app_commands.checks.has_permissions(administrator=True)
    async def captcha_status(self, interaction: discord.Interaction) -> None:
        data   = await self.config_store.read()
        config = _guild_config(data, interaction.guild.id)

        captcha_ch_text = f"<#{config['captcha_channel_id']}>" if config['captcha_channel_id'] else "Nicht gesetzt"
        quar_role_text  = f"<@&{config['quarantine_role_id']}>" if config['quarantine_role_id'] else "Nicht gesetzt"
        mem_role_text   = f"<@&{config['member_role_id']}>"     if config['member_role_id']     else "Nicht gesetzt"
        embed = info_embed(
            "🔐 Captcha & Quarantäne",
            f"**Status:** {'✅ Aktiv' if config['enabled'] else '❌ Inaktiv'}\n"
            f"**Captcha:** {'✅' if config['captcha_enabled'] else '❌'} "
            f"| Timeout: {config['captcha_timeout_min']}min\n"
            f"**Captcha-Kanal:** {captcha_ch_text}\n"
            f"**Quarantäne-Rolle:** {quar_role_text}\n"
            f"**Member-Rolle:** {mem_role_text}\n"
            f"**Alt-Erkennung:** Accounts unter **{config['alt_min_days']}** Tagen → `{config['alt_action']}`",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @captcha_group.command(name="verify", description="Verifiziert ein Mitglied manuell (überspringt Captcha).")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def captcha_verify_manual(self, interaction: discord.Interaction, mitglied: discord.Member) -> None:
        data   = await self.config_store.read()
        config = _guild_config(data, interaction.guild.id)

        quarantine_role = interaction.guild.get_role(config["quarantine_role_id"]) if config["quarantine_role_id"] else None
        member_role     = interaction.guild.get_role(config["member_role_id"])     if config["member_role_id"]     else None

        if quarantine_role and quarantine_role in mitglied.roles:
            try:
                await mitglied.remove_roles(quarantine_role, reason=f"Manuelle Verifikation von {interaction.user}")
            except discord.HTTPException:
                pass

        if member_role:
            try:
                await mitglied.add_roles(member_role, reason=f"Manuelle Verifikation von {interaction.user}")
            except discord.HTTPException:
                pass

        # Pending-Captcha entfernen
        def mutate(data: dict) -> dict:
            data.get(str(interaction.guild.id), {}).pop(str(mitglied.id), None)
            return data
        await self.pending.update(mutate)

        await interaction.response.send_message(
            embed=success_embed("✅ Manuell verifiziert", f"{mitglied.mention} wurde von {interaction.user.mention} verifiziert."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CaptchaCog(bot))
