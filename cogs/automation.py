"""
Visueller Automation-Builder — komplett interaktiv über Buttons und Modals.

Unterstützte Trigger:
  member_join    — Mitglied betritt den Server
  member_leave   — Mitglied verlässt den Server
  message_keyword — Nachricht enthält ein Schlüsselwort
  reaction_add   — Reaction wird hinzugefügt
  role_assign    — Nutzer erhält eine bestimmte Rolle

Unterstützte Aktionen:
  send_message   — Sendet eine Nachricht in einen Kanal
  send_dm        — Sendet dem auslösenden Nutzer eine DM
  add_role       — Gibt dem Nutzer eine Rolle
  remove_role    — Entfernt eine Rolle vom Nutzer
  kick_member    — Kickt den Nutzer (erfordert Begründung)
  timeout        — Setzt den Nutzer temporär auf Timeout
  log            — Schreibt einen Eintrag in den Log-Kanal

Jede Automation wird per GUID gespeichert und kann einzeln
aktiviert/deaktiviert, bearbeitet und gelöscht werden.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import error_embed, info_embed, success_embed, warning_embed

PATH = "data/automation_config.json"

# ── Metadaten für Trigger und Aktionen ───────────────────────────────────────

TRIGGERS: dict[str, str] = {
    "member_join":      "👋 Mitglied beigetreten",
    "member_leave":     "🚪 Mitglied verlassen",
    "message_keyword":  "💬 Nachrichten-Schlüsselwort",
    "reaction_add":     "👍 Reaction hinzugefügt",
    "role_assign":      "🎭 Rolle erhalten",
    "custom_command":   "⌨️ Eigener Textbefehl (!cmd)",
}

ACTIONS: dict[str, str] = {
    "send_message":  "📢 Nachricht in Kanal senden",
    "send_dm":       "📨 DM an Nutzer senden",
    "add_role":      "➕ Rolle hinzufügen",
    "remove_role":   "➖ Rolle entfernen",
    "kick_member":   "👢 Nutzer kicken",
    "timeout":       "🔇 Nutzer timeouten",
    "log":           "📋 In Log-Kanal schreiben",
}


# ── Modals ────────────────────────────────────────────────────────────────────

class AutomationNameModal(discord.ui.Modal, title="🤖 Automation benennen"):
    """Erster Schritt: Name und optionale Beschreibung der neuen Automation eingeben."""
    name = discord.ui.TextInput(
        label="Name der Automation",
        placeholder="z.B. Willkommen-Nachricht",
        min_length=2,
        max_length=80,
    )
    description = discord.ui.TextInput(
        label="Beschreibung (optional)",
        placeholder="Was macht diese Automation?",
        required=False,
        max_length=200,
        style=discord.TextStyle.short,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Öffnet direkt den Trigger-Auswahl-View nach dem Benennen
        view = TriggerSelectView(
            name=self.name.value,
            description=self.description.value or "",
        )
        await interaction.response.send_message(
            embed=info_embed(
                "🤖 Automation erstellen — Schritt 2/3",
                f"**Name:** {self.name.value}\n\nWähle jetzt den **Trigger** aus:",
            ),
            view=view,
            ephemeral=True,
        )


class KeywordModal(discord.ui.Modal, title="💬 Schlüsselwort festlegen"):
    """Wird aufgerufen wenn Trigger = message_keyword, um das Schlüsselwort einzugeben."""
    keyword = discord.ui.TextInput(
        label="Schlüsselwort / Phrase",
        placeholder='z.B. "hallo" oder "discord.gg"',
        min_length=1,
        max_length=100,
    )

    def __init__(self, builder: dict):
        super().__init__()
        self.builder = builder

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.builder["trigger_config"] = {"keyword": self.keyword.value.lower()}
        view = ActionSelectView(self.builder)
        await interaction.response.edit_message(
            embed=info_embed(
                "🤖 Automation erstellen — Schritt 3/3",
                f"**Schlüsselwort:** `{self.keyword.value}`\n\nWähle jetzt die **Aktion**:",
            ),
            view=view,
        )


class ActionConfigModal(discord.ui.Modal, title="⚙️ Aktion konfigurieren"):
    """Nimmt die aktionsspezifischen Parameter entgegen (Text, Kanal-ID, etc.)."""
    config_text = discord.ui.TextInput(
        label="Konfiguration (JSON-Parameter)",
        placeholder='{"channel_id": 123456, "message": "Willkommen {user}!"}',
        style=discord.TextStyle.paragraph,
        min_length=2,
        max_length=900,
    )

    def __init__(self, builder: dict, action: str):
        super().__init__()
        self.builder = builder
        self.action  = action
        # Vorausgefüllte Beispiele je nach Aktionstyp
        examples = {
            "send_message": '{"channel_id": 0, "message": "Willkommen {user} auf {server}!"}',
            "send_dm":      '{"message": "Willkommen auf {server}! Lies bitte die Regeln."}',
            "add_role":     '{"role_id": 0}',
            "remove_role":  '{"role_id": 0}',
            "kick_member":  '{"reason": "Automatischer Kick"}',
            "timeout":      '{"minutes": 10, "reason": "Automatischer Timeout"}',
            "log":          '{"channel_id": 0, "message": "Nutzer {user} hat {trigger} ausgelöst"}',
        }
        self.config_text.default = examples.get(action, "{}")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        import json as _json
        try:
            config = _json.loads(self.config_text.value)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültiges JSON", "Bitte überprüfe das Format und versuche es erneut."),
                ephemeral=True,
            )
            return

        self.builder["action"]        = self.action
        self.builder["action_config"] = config

        # Automation speichern
        store      = JSONStore(PATH, {})
        auto_id    = str(uuid.uuid4())[:8]
        guild_id   = str(interaction.guild.id)

        def mutate(data: dict) -> dict:
            guild_autos = data.setdefault(guild_id, {}).setdefault("automations", {})
            guild_autos[auto_id] = {
                **self.builder,
                "id":        auto_id,
                "enabled":   True,
                "created_by": interaction.user.id,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            return data

        await store.update(mutate)

        embed = success_embed(
            "✅ Automation erstellt!",
            f"**Name:** {self.builder['name']}\n"
            f"**Trigger:** {TRIGGERS.get(self.builder['trigger'], self.builder['trigger'])}\n"
            f"**Aktion:** {ACTIONS.get(self.action, self.action)}\n"
            f"**ID:** `{auto_id}`",
        )
        await interaction.response.edit_message(embed=embed, view=None)


# ── Select Views ──────────────────────────────────────────────────────────────

class TriggerSelectView(discord.ui.View):
    """Zeigt alle verfügbaren Trigger als Select-Menü an."""

    def __init__(self, name: str, description: str):
        super().__init__(timeout=120)
        self.builder = {"name": name, "description": description}

        options = [
            discord.SelectOption(label=label, value=key, emoji=label[:2])
            for key, label in TRIGGERS.items()
        ]
        self.select.options = options

    @discord.ui.select(placeholder="Wähle einen Trigger…", min_values=1, max_values=1)
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        trigger = select.values[0]
        self.builder["trigger"] = trigger

        # Für keyword-Trigger: extra Modal öffnen
        if trigger == "message_keyword":
            await interaction.response.send_modal(KeywordModal(self.builder))
        elif trigger == "custom_command":
            await interaction.response.send_modal(_CustomCommandModal(self.builder))
        else:
            # Direkt zu Aktion weiterleiten
            view = ActionSelectView(self.builder)
            await interaction.response.edit_message(
                embed=info_embed(
                    "🤖 Automation erstellen — Schritt 3/3",
                    f"**Trigger:** {TRIGGERS[trigger]}\n\nWähle jetzt die **Aktion**:",
                ),
                view=view,
            )


class _CustomCommandModal(discord.ui.Modal, title="⌨️ Befehl festlegen"):
    """Trigger = custom_command: Welcher !befehl soll es sein?"""
    cmd = discord.ui.TextInput(
        label="Befehlsname (ohne !)",
        placeholder="z.B. regel1",
        min_length=1,
        max_length=40,
    )

    def __init__(self, builder: dict):
        super().__init__()
        self.builder = builder

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.builder["trigger_config"] = {"command": self.cmd.value.lower().lstrip("!")}
        view = ActionSelectView(self.builder)
        await interaction.response.edit_message(
            embed=info_embed(
                "🤖 Automation erstellen — Schritt 3/3",
                f"**Befehl:** `!{self.cmd.value}`\n\nWähle jetzt die **Aktion**:",
            ),
            view=view,
        )


class ActionSelectView(discord.ui.View):
    """Zeigt alle verfügbaren Aktionen als Select-Menü an."""

    def __init__(self, builder: dict):
        super().__init__(timeout=120)
        self.builder = builder

        options = [
            discord.SelectOption(label=label, value=key, emoji=label[:2])
            for key, label in ACTIONS.items()
        ]
        self.select.options = options

    @discord.ui.select(placeholder="Wähle eine Aktion…", min_values=1, max_values=1)
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        action = select.values[0]
        await interaction.response.send_modal(ActionConfigModal(self.builder, action))


# ── Automation-Laufzeit ───────────────────────────────────────────────────────

def _replace_vars(text: str, member: discord.Member | None, guild: discord.Guild | None) -> str:
    """Ersetzt Template-Variablen in Nachrichten."""
    if member:
        text = text.replace("{user}",         member.mention)
        text = text.replace("{user.name}",    member.display_name)
        text = text.replace("{user.id}",      str(member.id))
    if guild:
        text = text.replace("{server}",       guild.name)
        text = text.replace("{member_count}", str(guild.member_count or "?"))
    return text


async def _execute_action(
    bot:        commands.Bot,
    guild:      discord.Guild,
    member:     discord.Member | None,
    automation: dict,
) -> None:
    """Führt die konfigurierte Aktion einer Automation aus."""
    action = automation.get("action")
    cfg    = automation.get("action_config", {})

    try:
        if action == "send_message":
            channel = guild.get_channel(int(cfg.get("channel_id", 0)))
            if isinstance(channel, discord.TextChannel):
                msg = _replace_vars(cfg.get("message", ""), member, guild)
                await channel.send(msg, allowed_mentions=discord.AllowedMentions(users=True))

        elif action == "send_dm" and member:
            msg = _replace_vars(cfg.get("message", ""), member, guild)
            try:
                await member.send(msg)
            except (discord.Forbidden, discord.HTTPException):
                pass

        elif action == "add_role" and member:
            role = guild.get_role(int(cfg.get("role_id", 0)))
            if role:
                await member.add_roles(role, reason="Automation")

        elif action == "remove_role" and member:
            role = guild.get_role(int(cfg.get("role_id", 0)))
            if role:
                await member.remove_roles(role, reason="Automation")

        elif action == "kick_member" and member:
            await member.kick(reason=cfg.get("reason", "Automation"))

        elif action == "timeout" and member:
            until = discord.utils.utcnow() + datetime.timedelta(minutes=int(cfg.get("minutes", 10)))
            await member.timeout(until, reason=cfg.get("reason", "Automation"))

        elif action == "log":
            channel = guild.get_channel(int(cfg.get("channel_id", 0)))
            if isinstance(channel, discord.TextChannel):
                msg = _replace_vars(cfg.get("message", "Automation ausgelöst"), member, guild)
                embed = discord.Embed(
                    title="🤖 Automation ausgelöst",
                    description=msg,
                    color=discord.Color.blurple(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                )
                await channel.send(embed=embed)

    except (discord.Forbidden, discord.HTTPException, ValueError):
        pass  # Fehler still ignorieren — kein Crash des Bot-Prozesses


# ── Cog ───────────────────────────────────────────────────────────────────────

class Automation(commands.Cog):
    """Visueller Automation-Builder mit Buttons und Modals."""

    automation = app_commands.Group(name="automation", description="Server-Automationen und eigene Antworten.")

    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self.store = JSONStore(PATH, {})

    async def _guild_automations(self, guild_id: int) -> dict[str, dict]:
        data = await self.store.read()
        return data.get(str(guild_id), {}).get("automations", {})

    async def _fire(self, guild: discord.Guild, trigger: str, member: discord.Member | None, **ctx) -> None:
        """Feuert alle aktivierten Automations, die auf diesen Trigger hören."""
        automations = await self._guild_automations(guild.id)
        for auto in automations.values():
            if not auto.get("enabled", True):
                continue
            if auto.get("trigger") != trigger:
                continue

            # Keyword-Trigger: nur wenn Schlüsselwort enthalten ist
            if trigger == "message_keyword":
                keyword = auto.get("trigger_config", {}).get("keyword", "")
                content = ctx.get("content", "").lower()
                if keyword not in content:
                    continue

            await _execute_action(self.bot, guild, member, auto)

    # ── Slash-Commands ────────────────────────────────────────────────────────

    @automation.command(name="create", description="Erstellt eine neue Automation mit dem visuellen Builder.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def create(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AutomationNameModal())

    @automation.command(name="list", description="Zeigt alle Automationen dieses Servers.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_automations(self, interaction: discord.Interaction) -> None:
        automations = await self._guild_automations(interaction.guild.id)
        if not automations:
            await interaction.response.send_message(
                embed=info_embed("🤖 Automationen", "Noch keine Automationen erstellt.\n`/automation create` zum Starten."),
                ephemeral=True,
            )
            return

        lines = []
        for auto in automations.values():
            status = "✅" if auto.get("enabled", True) else "❌"
            trigger = TRIGGERS.get(auto.get("trigger", ""), auto.get("trigger", "?"))
            action  = ACTIONS.get(auto.get("action", ""), auto.get("action", "?"))
            lines.append(f"{status} **{auto['name']}** `{auto['id']}` — {trigger} → {action}")

        embed = info_embed("🤖 Automationen", "\n".join(lines[:20]))
        embed.set_footer(text=f"{len(automations)} Automationen total | AVOKE")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @automation.command(name="toggle", description="Aktiviert oder deaktiviert eine Automation.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle(self, interaction: discord.Interaction, automation_id: str) -> None:
        automations = await self._guild_automations(interaction.guild.id)
        if automation_id not in automations:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht gefunden", f"Keine Automation mit ID `{automation_id}`."),
                ephemeral=True,
            )
            return

        def mutate(data: dict) -> dict:
            auto = data[str(interaction.guild.id)]["automations"][automation_id]
            auto["enabled"] = not auto.get("enabled", True)
            return data

        result = await self.store.update(mutate)
        new_state = result[str(interaction.guild.id)]["automations"][automation_id]["enabled"]
        await interaction.response.send_message(
            embed=success_embed(
                f"{'✅ Aktiviert' if new_state else '❌ Deaktiviert'}",
                f"Automation `{automation_id}` wurde {'aktiviert' if new_state else 'deaktiviert'}.",
            ),
            ephemeral=True,
        )

    @automation.command(name="delete", description="Löscht eine Automation dauerhaft.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def delete(self, interaction: discord.Interaction, automation_id: str) -> None:
        def mutate(data: dict) -> dict:
            data.get(str(interaction.guild.id), {}).get("automations", {}).pop(automation_id, None)
            return data

        await self.store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("🗑️ Automation gelöscht", f"ID `{automation_id}` wurde entfernt."),
            ephemeral=True,
        )

    @automation.command(name="status", description="Zeigt den Status aller aktiven Automationen.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction) -> None:
        automations = await self._guild_automations(interaction.guild.id)
        active   = sum(1 for a in automations.values() if a.get("enabled", True))
        inactive = len(automations) - active
        embed = info_embed(
            "⚙️ Automation Center",
            f"**✅ Aktiv:** {active}\n**❌ Inaktiv:** {inactive}\n**Gesamt:** {len(automations)}",
        )
        trigger_counts: dict[str, int] = {}
        for a in automations.values():
            t = a.get("trigger", "?")
            trigger_counts[t] = trigger_counts.get(t, 0) + 1
        if trigger_counts:
            lines = [f"{TRIGGERS.get(t, t)}: **{c}**" for t, c in trigger_counts.items()]
            embed.add_field(name="Trigger-Übersicht", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Listener ─────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._fire(member.guild, "member_join", member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self._fire(member.guild, "member_leave", member)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return

        # Keyword-Trigger
        await self._fire(message.guild, "message_keyword", message.author, content=message.content)

        # Eigene Text-Befehle (!cmd)
        if message.content.startswith("!"):
            cmd = message.content[1:].strip().lower().split(maxsplit=1)[0]
            automations = await self._guild_automations(message.guild.id)
            for auto in automations.values():
                if (
                    auto.get("enabled", True)
                    and auto.get("trigger") == "custom_command"
                    and auto.get("trigger_config", {}).get("command") == cmd
                ):
                    await _execute_action(self.bot, message.guild, message.author, auto)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        await self._fire(guild, "reaction_add", member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """Erkennt Rollenzuweisungen für den role_assign Trigger."""
        new_roles = set(after.roles) - set(before.roles)
        if not new_roles:
            return
        automations = await self._guild_automations(after.guild.id)
        for auto in automations.values():
            if not auto.get("enabled", True) or auto.get("trigger") != "role_assign":
                continue
            role_id = auto.get("trigger_config", {}).get("role_id")
            if role_id and any(r.id == int(role_id) for r in new_roles):
                await _execute_action(self.bot, after.guild, after, auto)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Automation(bot))
