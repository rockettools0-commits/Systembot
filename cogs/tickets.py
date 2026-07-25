"""
Multi-Ticket-System für TRPC.
Erlaubt beliebig viele Ticket-Panels (Support, Allianz, Trading, ...),
jeweils mit eigener Zielkategorie, Anzeigename, Bild und gesperrter Rolle.
Transkript wird als HTML + TXT per DM und in den Log-Kanal gesendet.
"""

import asyncio
import html as _html
import io
import datetime
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore

CONFIG_PATH       = "data/tickets_config.json"
OPEN_TICKETS_PATH = "data/tickets_open.json"


def default_config():
    return {"panels": {}}  # panel_id (message_id) -> config dict


def default_open_tickets():
    return {}  # channel_id (str) -> ticket info dict


# ── HTML-Transkript-Generator ──────────────────────────────────────────────────

def _build_html_transcript(
    panel_name:   str,
    channel_name: str,
    created_at:   str,
    closed_at:    datetime.datetime,
    closed_by:    str,
    messages:     List[discord.Message],
) -> str:
    """Baut ein vollständiges, selbst-enthaltendes HTML-Transkript."""

    def esc(text: str) -> str:
        return _html.escape(str(text))

    # Nachrichten-Zeilen aufbauen
    rows: list[str] = []
    for msg in messages:
        ts       = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        name     = esc(str(msg.author))
        uid      = msg.author.id
        avatar   = str(msg.author.display_avatar.replace(size=64, format="png").url)
        content  = esc(msg.content) if msg.content else "<em style='color:#666'>[kein Textinhalt]</em>"
        is_bot   = msg.author.bot

        attachments_html = ""
        for att in msg.attachments:
            att_name = esc(att.filename)
            att_url  = esc(att.url)
            if att.content_type and att.content_type.startswith("image/"):
                attachments_html += (
                    f'<div class="attach">'
                    f'<a href="{att_url}" target="_blank">'
                    f'<img src="{att_url}" alt="{att_name}" style="max-width:320px;max-height:240px;border-radius:6px;margin-top:6px;">'
                    f'</a></div>'
                )
            else:
                attachments_html += (
                    f'<div class="attach">📎 <a href="{att_url}" target="_blank">{att_name}</a></div>'
                )

        row_class = "msg bot-msg" if is_bot else "msg"
        rows.append(f"""
        <div class="{row_class}">
          <img class="avatar" src="{esc(avatar)}" alt="{name}" onerror="this.style.display='none'">
          <div class="bubble">
            <div class="meta">
              <span class="author {'bot-tag' if is_bot else ''}">{name}</span>
              {'<span class="badge">BOT</span>' if is_bot else ''}
              <span class="uid">({uid})</span>
              <span class="ts">{esc(ts)} UTC</span>
            </div>
            <div class="content">{content}</div>
            {attachments_html}
          </div>
        </div>""")

    rows_html = "\n".join(rows) if rows else '<p style="color:#888;text-align:center">Keine Nachrichten.</p>'
    msg_count = len(messages)
    closed_str = closed_at.strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Transkript — #{esc(channel_name)}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#1a1a24;color:#dcddde;font-family:"Segoe UI",system-ui,sans-serif;font-size:15px;line-height:1.5}}
  a{{color:#00b0f4;text-decoration:none}} a:hover{{text-decoration:underline}}
  .header{{background:linear-gradient(135deg,#2e2e3e,#1e1e2e);padding:28px 32px;border-bottom:3px solid #2ec471}}
  .header h1{{font-size:22px;font-weight:700;color:#fff;margin-bottom:4px}}
  .header h1 span{{color:#2ec471}}
  .meta-grid{{display:flex;flex-wrap:wrap;gap:18px;margin-top:14px}}
  .meta-item{{background:#ffffff0d;border-radius:8px;padding:10px 16px;min-width:160px}}
  .meta-item .label{{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#888;margin-bottom:2px}}
  .meta-item .value{{font-size:14px;font-weight:600;color:#fff}}
  .msg-count{{background:#2ec47122;border:1px solid #2ec471;border-radius:8px;padding:10px 16px}}
  .msg-count .value{{color:#2ec471}}
  .messages{{max-width:900px;margin:0 auto;padding:24px 16px}}
  .msg{{display:flex;gap:14px;padding:10px 12px;border-radius:8px;margin-bottom:4px;transition:background .1s}}
  .msg:hover{{background:#ffffff07}}
  .bot-msg{{background:#ffffff05}}
  .avatar{{width:40px;height:40px;border-radius:50%;flex-shrink:0;margin-top:2px;object-fit:cover}}
  .bubble{{flex:1;min-width:0}}
  .meta{{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px;margin-bottom:3px}}
  .author{{font-weight:700;color:#fff;font-size:15px}}
  .bot-tag{{color:#7289da}}
  .badge{{background:#7289da;color:#fff;font-size:10px;font-weight:700;padding:1px 5px;border-radius:4px;text-transform:uppercase;letter-spacing:.05em}}
  .uid{{font-size:11px;color:#555}}
  .ts{{font-size:11px;color:#555;margin-left:auto}}
  .content{{color:#dcddde;word-break:break-word;white-space:pre-wrap}}
  .attach{{margin-top:5px}}
  .footer{{text-align:center;padding:24px;font-size:12px;color:#444;border-top:1px solid #2a2a3a;margin-top:16px}}
  .footer span{{color:#2ec471;font-weight:600}}
</style>
</head>
<body>
<div class="header">
  <h1>🎫 Ticket-Transkript — <span>#{esc(channel_name)}</span></h1>
  <div class="meta-grid">
    <div class="meta-item"><div class="label">Panel</div><div class="value">{esc(panel_name)}</div></div>
    <div class="meta-item"><div class="label">Erstellt</div><div class="value">{esc(created_at[:19] if created_at else "—")}</div></div>
    <div class="meta-item"><div class="label">Geschlossen</div><div class="value">{esc(closed_str)} UTC</div></div>
    <div class="meta-item"><div class="label">Geschlossen von</div><div class="value">{esc(closed_by)}</div></div>
    <div class="meta-item msg-count"><div class="label">Nachrichten</div><div class="value">{msg_count}</div></div>
  </div>
</div>
<div class="messages">
{rows_html}
</div>
<div class="footer">Erstellt von <span>{esc(panel_name)}</span> &mdash; {esc(closed_str)} UTC</div>
</body>
</html>"""


class TicketOpenView(discord.ui.View):
    """Persistenter View für den 'Ticket öffnen'-Button eines Panels."""

    def __init__(self, panel_id: str):
        super().__init__(timeout=None)
        self.panel_id = panel_id
        self.open_button.custom_id = f"ticket_open:{panel_id}"

    @discord.ui.button(label="🎫 Ticket öffnen", style=discord.ButtonStyle.green)
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "Tickets" = interaction.client.get_cog("Tickets")
        if cog is None:
            await interaction.response.send_message(
                "Ticket-System aktuell nicht verfügbar.", ephemeral=True
            )
            return
        await cog.handle_ticket_open(interaction, self.panel_id)


class TicketCloseView(discord.ui.View):
    """Persistenter View für den 'Ticket schließen'-Button innerhalb eines Tickets."""

    def __init__(self):
        super().__init__(timeout=None)
        self.close_button.custom_id = "ticket_close"

    @discord.ui.button(label="🔒 Ticket schließen", style=discord.ButtonStyle.red)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "Tickets" = interaction.client.get_cog("Tickets")
        if cog is None:
            await interaction.response.send_message(
                "Ticket-System aktuell nicht verfügbar.", ephemeral=True
            )
            return
        await cog.handle_ticket_close(interaction)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_store = JSONStore(CONFIG_PATH, default_config())
        self.open_store = JSONStore(OPEN_TICKETS_PATH, default_open_tickets())

    # ── /ticket Gruppe ────────────────────────────────────────────────────────
    ticket = app_commands.Group(name="ticket", description="Ticket-Verwaltung.")

    async def cog_load(self):
        # Persistente Views registrieren, damit Buttons nach Bot-Neustart weiter funktionieren.
        self.bot.add_view(TicketCloseView())
        config = await self.config_store.read()
        for panel_id in config.get("panels", {}):
            self.bot.add_view(TicketOpenView(panel_id))

    # ---------- Slash Commands ----------

    @ticket.command(
        name="setup",
        description="Erstellt ein neues Ticket-Panel für einen bestimmten Bereich.",
    )
    @app_commands.describe(
        kanal="Kanal, in dem das Panel gepostet wird",
        kategorie_id="ID der Kategorie, in der neue Ticket-Kanäle erstellt werden",
        anzeige_name="Name/Titel, der im Embed und Kanalnamen verwendet wird",
        bild_url="URL des Thumbnails für das Embed",
        gesperrte_rolle="Rolle, die KEINE Tickets in diesem System öffnen darf",
        log_kanal="Kanal, in den Transkripte beim Schließen gesendet werden",
        support_rolle_1="Support-Rolle 1 — erhält Zugriff auf jedes Ticket und wird gepingt",
        support_rolle_2="Support-Rolle 2 (optional)",
        support_rolle_3="Support-Rolle 3 (optional)",
        support_rolle_4="Support-Rolle 4 (optional)",
        support_rolle_5="Support-Rolle 5 (optional)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_setup(
        self,
        interaction: discord.Interaction,
        kanal: discord.TextChannel,
        kategorie_id: str,
        anzeige_name: str,
        bild_url: str,
        gesperrte_rolle: discord.Role,
        log_kanal: discord.TextChannel,
        support_rolle_1: discord.Role = None,
        support_rolle_2: discord.Role = None,
        support_rolle_3: discord.Role = None,
        support_rolle_4: discord.Role = None,
        support_rolle_5: discord.Role = None,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            kategorie_id_int = int(kategorie_id)
        except ValueError:
            await interaction.followup.send(
                "❌ `kategorie_id` muss eine gültige Zahl (Kategorie-ID) sein.", ephemeral=True
            )
            return

        kategorie = interaction.guild.get_channel(kategorie_id_int)
        if kategorie is None or not isinstance(kategorie, discord.CategoryChannel):
            await interaction.followup.send(
                "❌ Es wurde keine gültige Kategorie mit dieser ID gefunden.", ephemeral=True
            )
            return

        # Support-Rollen-IDs sammeln (None-Werte herausfiltern)
        support_role_ids = [
            r.id for r in (support_rolle_1, support_rolle_2, support_rolle_3,
                           support_rolle_4, support_rolle_5)
            if r is not None
        ]

        embed = discord.Embed(
            title=f"🎫  {anzeige_name}",
            description=(
                f"Klicke auf den Button unten, um ein **{anzeige_name}**-Ticket zu eröffnen.\n"
                f"Ein Teammitglied wird sich schnellstmöglich um dich kümmern.\n\n"
                f"⚡ Tickets werden automatisch als Transkript gespeichert."
            ),
            color=discord.Color.from_rgb(88, 101, 242),
        )
        if bild_url and bild_url.startswith("http"):
            embed.set_thumbnail(url=bild_url)
        embed.set_footer(text=f"{interaction.guild.name} │ Ticket-System")

        try:
            sent_message = await kanal.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Ich habe keine Berechtigung, in {kanal.mention} zu senden.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Fehler beim Senden des Panels: {e}", ephemeral=True)
            return

        panel_id = str(sent_message.id)
        panel_data = {
            "channel_id": kanal.id,
            "message_id": sent_message.id,
            "kategorie_id": kategorie_id_int,
            "anzeige_name": anzeige_name,
            "bild_url": bild_url,
            "gesperrte_rolle_id": gesperrte_rolle.id,
            "log_kanal_id": log_kanal.id,
            "support_role_ids": support_role_ids,
            "ticket_message": "",
            "rating_log_kanal_id": log_kanal.id,
        }

        def mutate(data):
            data.setdefault("panels", {})[panel_id] = panel_data
            return data

        await self.config_store.update(mutate)

        view = TicketOpenView(panel_id)
        self.bot.add_view(view)
        await sent_message.edit(view=view)

        role_info = ""
        if support_role_ids:
            mentions = " ".join(f"<@&{rid}>" for rid in support_role_ids)
            role_info = f"\n🛠️ Support-Rollen: {mentions}"
        await interaction.followup.send(
            f"✅ Ticket-Panel **{anzeige_name}** wurde in {kanal.mention} erstellt.{role_info}",
            ephemeral=True,
        )

    # ---------- /ticket info ----------

    @ticket.command(name="info", description="Zeigt alle offenen Tickets des Servers (Admin).")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_info(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        open_tickets = await self.open_store.read()
        config       = await self.config_store.read()
        guild        = interaction.guild

        guild_tickets = {
            ch_id: info for ch_id, info in open_tickets.items()
            if guild.get_channel(int(ch_id)) is not None
        }

        embed = discord.Embed(
            title=f"🎫 Offene Tickets — {guild.name}",
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=f"{guild.name} │ Ticket-System")

        if not guild_tickets:
            embed.description = "Derzeit sind keine offenen Tickets vorhanden."
        else:
            lines = []
            for ch_id, info in list(guild_tickets.items())[:20]:
                channel  = guild.get_channel(int(ch_id))
                ch_text  = channel.mention if channel else f"`#{ch_id}`"
                user     = guild.get_member(info.get("user_id", 0))
                user_str = user.mention if user else f"<@{info.get('user_id', '?')}>"
                panel    = info.get("anzeige_name", "?")
                created  = info.get("created_at", "")[:10]
                lines.append(f"{ch_text} — {user_str} — **{panel}** — {created}")
            embed.description = "\n".join(lines)
            embed.add_field(name="📊 Gesamt offen", value=str(len(guild_tickets)), inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- /ticket gui ----------

    @ticket.command(name="gui", description="Öffnet die Ticket-GUI zur Verwaltung aller Panels (Admin).")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_gui(self, interaction: discord.Interaction):
        # Lazy import bricht den zirkulären ticket_gui → tickets Import
        from cogs.ticket_gui import _get_panels, _main_menu_embed, TicketGuiMainView
        panels = await _get_panels()
        await interaction.response.send_message(
            embed=_main_menu_embed(len(panels), interaction.guild),
            view=TicketGuiMainView(),
            ephemeral=True,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ Du benötigst Administrator-Rechte für diesen Befehl.", ephemeral=True
            )
        else:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ Unerwarteter Fehler: {error}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Unerwarteter Fehler: {error}", ephemeral=True)

    # ---------- Button-Handler ----------

    async def handle_ticket_open(self, interaction: discord.Interaction, panel_id: str):
        config = await self.config_store.read()
        panel = config.get("panels", {}).get(panel_id)

        if panel is None:
            await interaction.response.send_message(
                "❌ Dieses Ticket-Panel ist nicht mehr gültig.", ephemeral=True
            )
            return

        guild = interaction.guild
        member = interaction.user

        gesperrte_rolle = guild.get_role(panel["gesperrte_rolle_id"])
        if gesperrte_rolle is not None and gesperrte_rolle in member.roles:
            await interaction.response.send_message(
                f"❌ Du besitzt die Rolle **{gesperrte_rolle.name}** und kannst hier kein Ticket öffnen.",
                ephemeral=True,
            )
            return

        kategorie = guild.get_channel(panel["kategorie_id"])
        if kategorie is None:
            await interaction.response.send_message(
                "❌ Die Zielkategorie existiert nicht mehr. Bitte kontaktiere einen Admin.",
                ephemeral=True,
            )
            return

        # Prüfen ob bereits ein offenes Ticket dieses Panels für den User existiert
        open_tickets = await self.open_store.read()
        for ch_id, info in open_tickets.items():
            if info.get("user_id") == member.id and info.get("panel_id") == panel_id:
                existing = guild.get_channel(int(ch_id))
                if existing:
                    await interaction.response.send_message(
                        f"❌ Du hast bereits ein offenes Ticket: {existing.mention}", ephemeral=True
                    )
                    return

        await interaction.response.defer(ephemeral=True)

        safe_name = panel["anzeige_name"].lower().replace(" ", "-")
        channel_name = f"{safe_name}-{member.name}"[:95]

        # Support-Rollen aus Panel-Config auflösen
        support_role_ids: list[int] = panel.get("support_role_ids") or []
        support_roles: list[discord.Role] = [
            r for rid in support_role_ids
            if (r := guild.get_role(rid)) is not None
        ]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
            ),
        }
        # Support-Rollen: Lesezugriff + Schreiben im Ticket-Kanal
        for role in support_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            )

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=kategorie,
                overwrites=overwrites,
                topic=f"Ticket von {member} ({member.id}) | Panel: {panel['anzeige_name']}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Mir fehlen die Berechtigungen, um einen Ticket-Kanal zu erstellen.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Fehler beim Erstellen des Kanals: {e}", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🎫  {panel['anzeige_name']}",
            description=(
                f"Willkommen {member.mention}!\n\n"
                f"Beschreibe dein Anliegen so genau wie möglich.\n"
                f"Ein Teammitglied wird sich bald um dich kümmern.\n\n"
                f"🔒 Nutze den Button unten um das Ticket zu schließen."
            ),
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=f"{interaction.guild.name} │ Ticket-System")
        bild = panel.get("bild_url", "")
        if bild and bild.startswith("http"):
            embed.set_thumbnail(url=bild)
        if support_roles:
            embed.add_field(
                name="🛠️ Support-Team",
                value=" ".join(r.mention for r in support_roles),
                inline=False,
            )

        # Ping-Content: User + alle Support-Rollen
        ping_parts = [member.mention] + [r.mention for r in support_roles]
        ping_content = " ".join(ping_parts)

        await ticket_channel.send(
            content=ping_content,
            embed=embed,
            view=TicketCloseView(),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )

        def mutate(data):
            data[str(ticket_channel.id)] = {
                "user_id": member.id,
                "panel_id": panel_id,
                "anzeige_name": panel["anzeige_name"],
                "log_kanal_id": panel["log_kanal_id"],
                "support_role_ids": support_role_ids,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            return data

        await self.open_store.update(mutate)

        await interaction.followup.send(
            f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}", ephemeral=True
        )

    async def handle_ticket_close(self, interaction: discord.Interaction):
        channel      = interaction.channel
        open_tickets = await self.open_store.read()
        info         = open_tickets.get(str(channel.id))

        if info is None:
            await interaction.response.send_message(
                "❌ Dies ist kein registrierter Ticket-Kanal.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "🔒 Ticket wird geschlossen und Transkript erstellt...", ephemeral=True
        )

        closed_at   = datetime.datetime.now(datetime.timezone.utc)
        panel_name  = info.get("anzeige_name", "Ticket")
        base_name   = f"transcript-{channel.name}"

        # ── Nachrichten sammeln ───────────────────────────────────────────────
        messages: list[discord.Message] = []
        try:
            async for msg in channel.history(limit=None, oldest_first=True):
                messages.append(msg)
        except discord.HTTPException:
            pass

        # ── TXT-Transkript ────────────────────────────────────────────────────
        txt_lines = [
            "=" * 60,
            f"  {interaction.guild.name} — Ticket-Transkript",
            "=" * 60,
            f"  Panel      : {panel_name}",
            f"  Kanal      : #{channel.name}",
            f"  Erstellt   : {info.get('created_at', 'unbekannt')}",
            f"  Geschlossen: {closed_at.strftime('%Y-%m-%d %H:%M:%S')} UTC",
            f"  Geschl. von: {interaction.user} ({interaction.user.id})",
            "=" * 60,
            "",
        ]
        for msg in messages:
            ts      = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            content = msg.content or "[kein Textinhalt]"
            attach  = (" | Anhänge: " + ", ".join(a.url for a in msg.attachments)
                       if msg.attachments else "")
            txt_lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {content}{attach}")

        txt_bytes = "\n".join(txt_lines).encode("utf-8")

        # ── HTML-Transkript ───────────────────────────────────────────────────
        html_bytes = _build_html_transcript(
            panel_name  = panel_name,
            channel_name= channel.name,
            created_at  = info.get("created_at", ""),
            closed_at   = closed_at,
            closed_by   = str(interaction.user),
            messages    = messages,
        ).encode("utf-8")

        # ── Log-Kanal: Embed + beide Dateien ─────────────────────────────────
        log_kanal = interaction.guild.get_channel(info.get("log_kanal_id"))
        if log_kanal:
            try:
                log_embed = discord.Embed(
                    title="🔒  Ticket geschlossen",
                    color=discord.Color.from_rgb(235, 77, 75),
                    timestamp=closed_at,
                )
                log_embed.add_field(name="📋 Panel",           value=panel_name,                         inline=True)
                log_embed.add_field(name="📁 Kanal",           value=f"#{channel.name}",                 inline=True)
                log_embed.add_field(name="\u200b",             value="\u200b",                           inline=True)
                log_embed.add_field(name="👤 Ersteller",       value=f"<@{info.get('user_id')}>",        inline=True)
                log_embed.add_field(name="🛡️ Geschlossen von", value=interaction.user.mention,           inline=True)
                log_embed.add_field(name="🕐 Zeitpunkt",       value=f"<t:{int(closed_at.timestamp())}:F>", inline=True)
                _sup_ids = info.get("support_role_ids") or []
                if _sup_ids:
                    log_embed.add_field(
                        name="🛠️ Support-Rollen",
                        value=" ".join(f"<@&{rid}>" for rid in _sup_ids),
                        inline=False,
                    )
                log_embed.set_footer(text=f"{interaction.guild.name} │ Ticket-System")
                await log_kanal.send(
                    embed=log_embed,
                    files=[
                        discord.File(io.BytesIO(txt_bytes),  filename=f"{base_name}.txt"),
                        discord.File(io.BytesIO(html_bytes), filename=f"{base_name}.html"),
                    ],
                )
            except discord.HTTPException:
                pass

        # ── DM an den Ticket-Ersteller: Embed + HTML + TXT ───────────────────
        ticket_owner = interaction.guild.get_member(info.get("user_id"))
        if ticket_owner:
            dm_embed = discord.Embed(
                title="📋  Dein Ticket wurde geschlossen",
                description=(
                    f"Das Transkript deines Tickets ist im Anhang — "
                    "öffne die **.html**-Datei im Browser für die beste Ansicht."
                ),
                color=discord.Color.from_rgb(88, 101, 242),
                timestamp=closed_at,
            )
            dm_embed.add_field(name="🏰 Server",          value=interaction.guild.name,                 inline=True)
            dm_embed.add_field(name="📋 Panel",           value=panel_name,                             inline=True)
            dm_embed.add_field(name="🛡️ Geschlossen von", value=str(interaction.user),                  inline=True)
            dm_embed.add_field(name="🕐 Zeitpunkt",       value=f"<t:{int(closed_at.timestamp())}:F>",  inline=False)
            dm_embed.set_footer(text=f"{interaction.guild.name} │ System")
            if interaction.guild.icon:
                dm_embed.set_thumbnail(url=interaction.guild.icon.url)
            try:
                await ticket_owner.send(
                    embed=dm_embed,
                    files=[
                        discord.File(io.BytesIO(html_bytes), filename=f"{base_name}.html"),
                        discord.File(io.BytesIO(txt_bytes),  filename=f"{base_name}.txt"),
                    ],
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        # ── Store bereinigen & Kanal löschen ─────────────────────────────────
        def mutate(data):
            data.pop(str(channel.id), None)
            return data

        await self.open_store.update(mutate)
        asyncio.create_task(self._delete_channel_after(channel, interaction.user))

    async def _delete_channel_after(self, channel: discord.TextChannel, closer: discord.Member):
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket geschlossen von {closer}")
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
