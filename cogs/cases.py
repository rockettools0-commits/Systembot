"""
Case-System — vollständiges Moderations-Fallverwaltungssystem.

Funktionen:
  • Jede Moderation erzeugt automatisch einen Fall mit eindeutiger ID
  • Fälle können kommentiert und nachträglich bearbeitet werden
  • Benutzer können schriftliche Einsprüche (Appeals) einreichen
  • Moderatoren können Einsprüche annehmen oder ablehnen
  • Vollständige Fallhistorie pro Server und pro Nutzer
  • /case search  — Volltextsuche über alle Fälle
  • /case stats   — Server-weite Moderationsstatistiken
  • Datenschutz: Einsprüche sind nur für Admins einsehbar
"""
from __future__ import annotations

import datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import (
    error_embed, info_embed, success_embed, warning_embed, dark_embed
)

CASES_PATH   = "data/cases.json"
APPEALS_PATH = "data/appeals.json"

# Gültige Aktionstypen für automatisch erstellte Fälle
CASE_ACTIONS = {
    "warn":    ("⚠️",  discord.Color.from_rgb(243, 156, 18)),   # Verwarnung
    "mute":    ("🔇",  discord.Color.from_rgb(130, 80,  255)),   # Timeout/Mute
    "kick":    ("👢",  discord.Color.from_rgb(235, 200, 75)),    # Kick
    "ban":     ("🔨",  discord.Color.from_rgb(235, 77,  75)),    # Ban
    "unban":   ("🔓",  discord.Color.from_rgb(88,  214, 141)),   # Unban
    "unmute":  ("🔊",  discord.Color.from_rgb(88,  214, 141)),   # Unmute
    "note":    ("📝",  discord.Color.from_rgb(84,  153, 199)),   # Notiz
    "softban": ("🔁",  discord.Color.from_rgb(230, 126, 34)),    # Softban
}

APPEAL_STATUS = {
    "pending":  "⏳ Ausstehend",
    "accepted": "✅ Angenommen",
    "denied":   "❌ Abgelehnt",
}


def _next_case_id(guild_data: dict) -> int:
    """Gibt die nächste freie Fall-ID zurück (auto-increment)."""
    existing = [int(k) for k in guild_data.get("cases", {}) if str(k).isdigit()]
    return max(existing, default=0) + 1


class AppealModal(discord.ui.Modal, title="📩 Einspruch einreichen"):
    """Modal, das der Nutzer ausfüllt, um einen Einspruch zu einem Fall einzureichen."""

    reason = discord.ui.TextInput(
        label="Dein Einspruch",
        placeholder="Erkläre, warum du denkst, dass diese Maßnahme ungerechtfertigt war…",
        style=discord.TextStyle.paragraph,
        min_length=20,
        max_length=1000,
    )

    def __init__(self, case_id: int, guild_id: int):
        super().__init__()
        self.case_id  = case_id
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        store = JSONStore(APPEALS_PATH, {})

        def mutate(data: dict) -> dict:
            guild_appeals = data.setdefault(str(self.guild_id), {})
            # Nur ein offener Einspruch pro Fall erlaubt
            guild_appeals[str(self.case_id)] = {
                "user_id":     interaction.user.id,
                "reason":      self.reason.value,
                "status":      "pending",
                "submitted":   datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "reviewed_by": None,
                "review_note": None,
            }
            return data

        await store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed(
                "📩 Einspruch eingereicht",
                f"Dein Einspruch zu Fall **#{self.case_id}** wurde hinterlegt.\n"
                "Das Moderations-Team wird ihn prüfen und dich benachrichtigen.",
            ),
            ephemeral=True,
        )


class AppealReviewView(discord.ui.View):
    """Buttons zum Annehmen/Ablehnen eines Einspruchs, erscheinen im Moderationskanal."""

    def __init__(self, case_id: int, guild_id: int, user_id: int):
        super().__init__(timeout=None)
        self.case_id  = case_id
        self.guild_id = guild_id
        self.user_id  = user_id
        # Persistente custom_ids damit die Buttons nach einem Neustart weiterhin funktionieren
        self.accept_btn.custom_id = f"appeal_accept:{guild_id}:{case_id}"
        self.deny_btn.custom_id   = f"appeal_deny:{guild_id}:{case_id}"

    async def _update(self, interaction: discord.Interaction, new_status: str) -> None:
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message("❌ Keine Berechtigung.", ephemeral=True)
            return

        store = JSONStore(APPEALS_PATH, {})

        def mutate(data: dict) -> dict:
            appeal = data.setdefault(str(self.guild_id), {}).get(str(self.case_id))
            if appeal:
                appeal["status"]      = new_status
                appeal["reviewed_by"] = interaction.user.id
                appeal["review_ts"]   = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return data

        await store.update(mutate)

        # Buttons nach Entscheidung deaktivieren
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        # DM an den Antragsteller
        member = interaction.guild.get_member(self.user_id)
        if member:
            color  = discord.Color.green() if new_status == "accepted" else discord.Color.red()
            status = "✅ angenommen" if new_status == "accepted" else "❌ abgelehnt"
            try:
                await member.send(embed=discord.Embed(
                    title=f"📩 Einspruch {status}",
                    description=(
                        f"Dein Einspruch zu Fall **#{self.case_id}** wurde von "
                        f"{interaction.user.mention} **{status}**."
                    ),
                    color=color,
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                ))
            except (discord.Forbidden, discord.HTTPException):
                pass

    @discord.ui.button(label="✅ Annehmen", style=discord.ButtonStyle.success)
    async def accept_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._update(interaction, "accepted")

    @discord.ui.button(label="❌ Ablehnen", style=discord.ButtonStyle.danger)
    async def deny_btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._update(interaction, "denied")


class Cases(commands.Cog):
    """Vollständiges Case-Verwaltungssystem mit Kommentaren und Einsprüchen."""

    cases_group   = app_commands.Group(name="case",   description="Fallverwaltung — Moderationshistorie.")
    appeals_group = app_commands.Group(name="appeal", description="Einsprüche gegen Moderationsmaßnahmen.")

    def __init__(self, bot: commands.Bot):
        self.bot          = bot
        self.store        = JSONStore(CASES_PATH,   {})
        self.appeal_store = JSONStore(APPEALS_PATH, {})

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API — wird von anderen Cogs aufgerufen
    # ─────────────────────────────────────────────────────────────────────────

    async def create_case(
        self,
        guild_id:  int,
        user_id:   int,
        mod_id:    int,
        action:    str,
        reason:    str,
        extra:     dict | None = None,
    ) -> int:
        """
        Legt einen neuen Fall an und gibt die Fall-ID zurück.
        Wird von anderen Cogs (Moderation, Ban, etc.) aufgerufen.
        """
        def mutate(data: dict) -> dict:
            guild_data = data.setdefault(str(guild_id), {"cases": {}})
            case_id    = _next_case_id(guild_data)
            guild_data["cases"][str(case_id)] = {
                "id":        case_id,
                "action":    action,
                "user_id":   user_id,
                "mod_id":    mod_id,
                "reason":    reason or "Kein Grund angegeben",
                "comments":  [],          # Liste von {author_id, text, ts}
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                **(extra or {}),
            }
            return data

        result  = await self.store.update(mutate)
        guild_d = result.get(str(guild_id), {})
        cases   = guild_d.get("cases", {})
        return max(int(k) for k in cases if str(k).isdigit())

    def _case_embed(self, case: dict, case_id: int) -> discord.Embed:
        """Erstellt ein formatiertes Embed für einen einzelnen Fall."""
        action        = case.get("action", "note")
        emoji, color  = CASE_ACTIONS.get(action, ("📋", discord.Color.blurple()))
        embed = discord.Embed(
            title       = f"{emoji} Fall #{case_id} — {action.upper()}",
            description = case.get("reason", "—"),
            color       = color,
            timestamp   = (
                datetime.datetime.fromisoformat(case["timestamp"])
                if "timestamp" in case else discord.utils.utcnow()
            ),
        )
        embed.add_field(name="👤 Nutzer",     value=f"<@{case['user_id']}>", inline=True)
        embed.add_field(name="🛡️ Moderator", value=f"<@{case['mod_id']}>",  inline=True)

        # Nachträgliche Bearbeitung anzeigen
        if case.get("edited_by"):
            embed.add_field(
                name   = "✏️ Zuletzt bearbeitet",
                value  = f"<@{case['edited_by']}> am {case.get('edited_at', '?')[:10]}",
                inline = True,
            )

        comments = case.get("comments", [])
        if comments:
            last = comments[-1]
            embed.add_field(
                name  = f"💬 Letzter Kommentar ({len(comments)} total)",
                value = f"<@{last['author_id']}>: {last['text'][:200]}",
                inline=False,
            )
        embed.set_footer(text="AVOKE │ Case System")
        return embed

    # ─────────────────────────────────────────────────────────────────────────
    # /case Gruppe
    # ─────────────────────────────────────────────────────────────────────────

    @cases_group.command(name="view", description="Zeigt einen bestimmten Fall an.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def case_view(self, interaction: discord.Interaction, fall_id: int) -> None:
        data  = await self.store.read()
        cases = data.get(str(interaction.guild.id), {}).get("cases", {})
        case  = cases.get(str(fall_id))
        if not case:
            await interaction.response.send_message(
                embed=error_embed("❌ Fall nicht gefunden", f"Kein Fall mit ID **#{fall_id}** vorhanden."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=self._case_embed(case, fall_id), ephemeral=True)

    @cases_group.command(name="list", description="Listet alle Fälle eines Mitglieds auf.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def case_list(self, interaction: discord.Interaction, mitglied: discord.Member) -> None:
        data  = await self.store.read()
        cases = data.get(str(interaction.guild.id), {}).get("cases", {})
        # Alle Fälle dieses Nutzers filtern
        user_cases = [
            (int(cid), c) for cid, c in cases.items()
            if c.get("user_id") == mitglied.id
        ]
        user_cases.sort(key=lambda x: x[0], reverse=True)

        if not user_cases:
            await interaction.response.send_message(
                embed=info_embed(f"📋 Fälle — {mitglied.display_name}", "Keine Fälle vorhanden."),
                ephemeral=True,
            )
            return

        lines = []
        for cid, c in user_cases[:20]:
            emoji, _ = CASE_ACTIONS.get(c["action"], ("📋", None))
            ts       = c["timestamp"][:10] if "timestamp" in c else "?"
            mod_id   = c['mod_id']
            lines.append(f"`#{cid}` {emoji} **{c['action'].upper()}** — {ts} — von <@{mod_id}>")

        embed = info_embed(
            f"📋 Fallhistorie — {mitglied.display_name}",
            "\n".join(lines),
        )
        embed.add_field(name="Gesamt", value=str(len(user_cases)), inline=True)
        embed.set_thumbnail(url=mitglied.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @cases_group.command(name="comment", description="Fügt einen Kommentar zu einem Fall hinzu.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def case_comment(
        self,
        interaction: discord.Interaction,
        fall_id:     int,
        kommentar:   app_commands.Range[str, 1, 500],
    ) -> None:
        data  = await self.store.read()
        cases = data.get(str(interaction.guild.id), {}).get("cases", {})
        if str(fall_id) not in cases:
            await interaction.response.send_message(
                embed=error_embed("❌ Fall nicht gefunden"), ephemeral=True
            )
            return

        def mutate(d: dict) -> dict:
            d[str(interaction.guild.id)]["cases"][str(fall_id)]["comments"].append({
                "author_id": interaction.user.id,
                "text":      kommentar,
                "ts":        datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
            return d

        await self.store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("💬 Kommentar hinzugefügt", f"Fall **#{fall_id}** wurde kommentiert."),
            ephemeral=True,
        )

    @cases_group.command(name="edit", description="Bearbeitet den Grund eines Falls nachträglich.")
    @app_commands.checks.has_permissions(administrator=True)
    async def case_edit(
        self,
        interaction: discord.Interaction,
        fall_id:     int,
        neuer_grund: app_commands.Range[str, 1, 500],
    ) -> None:
        data  = await self.store.read()
        cases = data.get(str(interaction.guild.id), {}).get("cases", {})
        if str(fall_id) not in cases:
            await interaction.response.send_message(embed=error_embed("❌ Fall nicht gefunden"), ephemeral=True)
            return

        def mutate(d: dict) -> dict:
            c = d[str(interaction.guild.id)]["cases"][str(fall_id)]
            c["reason"]    = neuer_grund
            c["edited_by"] = interaction.user.id
            c["edited_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            return d

        await self.store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("✏️ Fall bearbeitet", f"Grund für Fall **#{fall_id}** aktualisiert."),
            ephemeral=True,
        )

    @cases_group.command(name="delete", description="Löscht einen Fall dauerhaft (Admin only).")
    @app_commands.checks.has_permissions(administrator=True)
    async def case_delete(self, interaction: discord.Interaction, fall_id: int) -> None:
        def mutate(d: dict) -> dict:
            d.get(str(interaction.guild.id), {}).get("cases", {}).pop(str(fall_id), None)
            return d

        await self.store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed("🗑️ Fall gelöscht", f"Fall **#{fall_id}** wurde dauerhaft entfernt."),
            ephemeral=True,
        )

    @cases_group.command(name="search", description="Sucht Fälle nach Grund, Nutzer-ID oder Moderator.")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.describe(
        suchbegriff="Freitext-Suche im Grund oder Teil einer Nutzer/Moderator-ID",
        aktion="Optional: Nur Fälle mit dieser Aktion anzeigen",
    )
    @app_commands.choices(aktion=[
        app_commands.Choice(name=name, value=key)
        for key, name in [
            ("warn",    "⚠️ Warnung"),
            ("mute",    "🔇 Mute"),
            ("kick",    "👢 Kick"),
            ("ban",     "🔨 Ban"),
            ("unban",   "🔓 Unban"),
            ("unmute",  "🔊 Unmute"),
            ("note",    "📝 Notiz"),
            ("softban", "🔁 Softban"),
        ]
    ])
    async def case_search(
        self,
        interaction: discord.Interaction,
        suchbegriff: str              = "",
        aktion:      str | None       = None,
    ) -> None:
        data  = await self.store.read()
        cases = data.get(str(interaction.guild.id), {}).get("cases", {})

        q = suchbegriff.lower().strip()

        results: list[tuple[int, dict]] = []
        for cid, c in cases.items():
            # Aktionsfilter
            if aktion and c.get("action") != aktion:
                continue
            # Freitextsuche
            if q:
                searchable = " ".join([
                    str(c.get("reason", "")),
                    str(c.get("user_id", "")),
                    str(c.get("mod_id", "")),
                    c.get("action", ""),
                ]).lower()
                if q not in searchable:
                    continue
            results.append((int(cid), c))

        if not results:
            await interaction.response.send_message(
                embed=info_embed("🔍 Suche", "Keine Fälle gefunden, die den Kriterien entsprechen."),
                ephemeral=True,
            )
            return

        results.sort(key=lambda x: x[0], reverse=True)
        lines = []
        for cid, c in results[:15]:
            emoji, _ = CASE_ACTIONS.get(c["action"], ("📋", None))
            ts_str   = c["timestamp"][:10] if "timestamp" in c else "?"
            reason   = c.get("reason", "—")[:60]
            lines.append(
                f"`#{cid}` {emoji} **{c['action'].upper()}** — <@{c['user_id']}> — {ts_str}\n"
                f"  └ {reason}"
            )

        embed = info_embed(
            f"🔍 Suchergebnisse ({len(results)} Treffer)",
            "\n".join(lines),
        )
        if len(results) > 15:
            embed.set_footer(text=f"Zeige 15 von {len(results)} Treffern | AVOKE Case System")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @cases_group.command(name="stats", description="Zeigt Server-weite Moderationsstatistiken.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def case_stats(self, interaction: discord.Interaction) -> None:
        data  = await self.store.read()
        cases = data.get(str(interaction.guild.id), {}).get("cases", {})

        if not cases:
            await interaction.response.send_message(
                embed=info_embed("📊 Case-Statistiken", "Noch keine Fälle vorhanden."),
                ephemeral=True,
            )
            return

        # Aktionszählung
        action_counts: dict[str, int] = {}
        mod_counts:    dict[int, int] = {}
        recent_7d = 0
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)

        for c in cases.values():
            action = c.get("action", "note")
            action_counts[action] = action_counts.get(action, 0) + 1
            mod_id = c.get("mod_id")
            if mod_id:
                mod_counts[mod_id] = mod_counts.get(mod_id, 0) + 1
            ts_str = c.get("timestamp", "")
            try:
                if datetime.datetime.fromisoformat(ts_str) >= cutoff:
                    recent_7d += 1
            except ValueError:
                pass

        # Top-Moderatoren (max 5)
        top_mods = sorted(mod_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        embed = dark_embed(
            "📊 Moderations-Statistiken",
            f"**Fälle gesamt:** {len(cases)}\n"
            f"**Letzte 7 Tage:** {recent_7d}",
        )

        # Aktionsverteilung
        action_lines = []
        for act, count in sorted(action_counts.items(), key=lambda x: x[1], reverse=True):
            emoji, _ = CASE_ACTIONS.get(act, ("📋", None))
            action_lines.append(f"{emoji} {act.upper()}: **{count}**")
        embed.add_field(name="Aktionen", value="\n".join(action_lines) or "—", inline=True)

        # Top-Mods
        mod_lines = [f"<@{uid}>: **{cnt}** Fälle" for uid, cnt in top_mods]
        embed.add_field(name="Top-Moderatoren", value="\n".join(mod_lines) or "—", inline=True)

        embed.set_footer(text="AVOKE │ Case System")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # /appeal Gruppe
    # ─────────────────────────────────────────────────────────────────────────

    @appeals_group.command(name="submit", description="Reiche einen Einspruch gegen einen deiner Fälle ein.")
    async def appeal_submit(self, interaction: discord.Interaction, fall_id: int) -> None:
        # Prüfen ob der Fall dem anfragenden Nutzer gehört
        data  = await self.store.read()
        cases = data.get(str(interaction.guild.id), {}).get("cases", {})
        case  = cases.get(str(fall_id))
        if not case or case["user_id"] != interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed(
                    "❌ Kein Zugriff",
                    "Du kannst nur gegen deine eigenen Fälle Einspruch einlegen.",
                ),
                ephemeral=True,
            )
            return

        # Prüfen ob bereits ein offener Einspruch besteht
        appeals  = await self.appeal_store.read()
        existing = appeals.get(str(interaction.guild.id), {}).get(str(fall_id))
        if existing and existing["status"] == "pending":
            await interaction.response.send_message(
                embed=warning_embed(
                    "⏳ Einspruch ausstehend",
                    "Du hast für diesen Fall bereits einen offenen Einspruch.",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AppealModal(fall_id, interaction.guild.id))

    @appeals_group.command(name="list", description="Listet alle ausstehenden Einsprüche (Moderatoren).")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def appeal_list(self, interaction: discord.Interaction) -> None:
        appeals       = await self.appeal_store.read()
        guild_appeals = appeals.get(str(interaction.guild.id), {})
        pending       = [(cid, a) for cid, a in guild_appeals.items() if a["status"] == "pending"]

        if not pending:
            await interaction.response.send_message(
                embed=info_embed("📩 Einsprüche", "Keine ausstehenden Einsprüche vorhanden."),
                ephemeral=True,
            )
            return

        lines = [
            f"`Fall #{cid}` — <@{a['user_id']}> — {a['submitted'][:10]}"
            for cid, a in pending[:20]
        ]
        await interaction.response.send_message(
            embed=info_embed("📩 Ausstehende Einsprüche", "\n".join(lines)),
            ephemeral=True,
        )

    @appeals_group.command(name="review", description="Prüft einen Einspruch und zeigt Annehmen/Ablehnen-Buttons.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def appeal_review(self, interaction: discord.Interaction, fall_id: int) -> None:
        appeals = await self.appeal_store.read()
        appeal  = appeals.get(str(interaction.guild.id), {}).get(str(fall_id))
        if not appeal:
            await interaction.response.send_message(
                embed=error_embed("❌ Kein Einspruch", f"Für Fall **#{fall_id}** liegt kein Einspruch vor."),
                ephemeral=True,
            )
            return

        status_text = APPEAL_STATUS.get(appeal["status"], appeal["status"])
        embed = dark_embed(
            f"📩 Einspruch — Fall #{fall_id}",
            appeal["reason"],
        )
        embed.add_field(name="Antragsteller", value=f"<@{appeal['user_id']}>", inline=True)
        embed.add_field(name="Status",         value=status_text,              inline=True)
        embed.add_field(name="Eingereicht",    value=appeal["submitted"][:16], inline=True)

        view = (
            AppealReviewView(fall_id, interaction.guild.id, appeal["user_id"])
            if appeal["status"] == "pending"
            else discord.ui.View()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @appeals_group.command(name="history", description="Zeigt alle Einsprüche eines Nutzers.")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def appeal_history(self, interaction: discord.Interaction, mitglied: discord.Member) -> None:
        appeals       = await self.appeal_store.read()
        guild_appeals = appeals.get(str(interaction.guild.id), {})
        user_appeals  = [
            (cid, a) for cid, a in guild_appeals.items()
            if a["user_id"] == mitglied.id
        ]
        if not user_appeals:
            await interaction.response.send_message(
                embed=info_embed(f"📩 Einsprüche — {mitglied.display_name}", "Keine Einsprüche vorhanden."),
                ephemeral=True,
            )
            return

        lines = [
            f"`Fall #{cid}` — {APPEAL_STATUS.get(a['status'], a['status'])} — {a['submitted'][:10]}"
            for cid, a in user_appeals[-15:]
        ]
        await interaction.response.send_message(
            embed=info_embed(f"📩 Einsprüche — {mitglied.display_name}", "\n".join(lines)),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Cases(bot))
