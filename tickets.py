"""
Multi-Ticket-System für AVOKE.
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
from utils.theme import get_footer_text

CONFIG_PATH       = "data/tickets_config.json"
OPEN_TICKETS_PATH = "data/tickets_open.json"
RATINGS_PATH      = "data/ticket_ratings.json"


def default_config():
    return {"panels": {}}  # panel_id (message_id) -> config dict


def default_open_tickets():
    return {}  # channel_id (str) -> ticket info dict


def default_ratings():
    return {"ratings": [], "pending": {}}


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
<div class="footer">Erstellt von <span>AVOKE | System</span> &mdash; {esc(closed_str)} UTC</div>
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


class RatingFeedbackModal(discord.ui.Modal, title="Ticketbewertung"):
    feedback = discord.ui.TextInput(
        label="Optionales Feedback",
        placeholder="Wie war dein Support-Erlebnis?",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    def __init__(self, ticket_id: str, stars: int):
        super().__init__()
        self.ticket_id = ticket_id
        self.stars = stars

    async def on_submit(self, interaction: discord.Interaction):
        cog: "Tickets" = interaction.client.get_cog("Tickets")
        if cog is None:
            await interaction.response.send_message("Ticket-System aktuell nicht verfuegbar.", ephemeral=True)
            return
        await cog.save_rating(interaction, self.ticket_id, self.stars, self.feedback.value)


class RatingButton(discord.ui.Button):
    def __init__(self, ticket_id: str, stars: int):
        super().__init__(label=f"{stars} Stern{'e' if stars != 1 else ''}", style=discord.ButtonStyle.blurple,
                         custom_id=f"ticket_rating:{ticket_id}:{stars}")
        self.ticket_id = ticket_id
        self.stars = stars

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RatingFeedbackModal(self.ticket_id, self.stars))


class TicketRatingView(discord.ui.View):
    """Persistente Sternauswahl fuer bereits geschlossene Tickets."""
    def __init__(self, ticket_id: str):
        super().__init__(timeout=None)
        for stars in range(1, 6):
            self.add_item(RatingButton(ticket_id, stars))


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_store = JSONStore(CONFIG_PATH, default_config())
        self.open_store = JSONStore(OPEN_TICKETS_PATH, default_open_tickets())
        self.ratings_store = JSONStore(RATINGS_PATH, default_ratings())
        self.open_ticket_count = 0
        self.rating_average = 0.0

    async def cog_load(self):
        # Persistente Views registrieren, damit Buttons nach Bot-Neustart weiter funktionieren.
        self.bot.add_view(TicketCloseView())
        config = await self.config_store.read()
        for panel_id in config.get("panels", {}):
            self.bot.add_view(TicketOpenView(panel_id))
        ratings = await self.ratings_store.read()
        for ticket_id in ratings.get("pending", {}):
            self.bot.add_view(TicketRatingView(ticket_id))
        await self._refresh_metrics()

    async def _refresh_metrics(self):
        self.open_ticket_count = len(await self.open_store.read())
        ratings = (await self.ratings_store.read()).get("ratings", [])
        self.rating_average = (sum(item["stars"] for item in ratings) / len(ratings)) if ratings else 0.0

    async def open_ticket_entries(self, guild: discord.Guild) -> list[str]:
        entries = []
        for channel_id, info in (await self.open_store.read()).items():
            channel = guild.get_channel(int(channel_id))
            if channel:
                entries.append(f"{channel.mention} - <@{info.get('user_id')}> - {info.get('anzeige_name', 'Ticket')}")
        return entries

    @commands.command(name="ratingstats")
    async def rating_stats(self, ctx: commands.Context):
        ratings = (await self.ratings_store.read()).get("ratings", [])
        counts = {star: sum(item.get("stars") == star for item in ratings) for star in range(1, 6)}
        average = sum(item.get("stars", 0) for item in ratings) / len(ratings) if ratings else 0.0
        embed = discord.Embed(title="Ticketbewertungen", color=discord.Color.gold())
        embed.add_field(name="Durchschnitt", value=f"{average:.2f} / 5", inline=True)
        embed.add_field(name="Anzahl", value=str(len(ratings)), inline=True)
        embed.add_field(name="Verteilung", value="\n".join(f"{'★' * star}: {counts[star]}" for star in range(1, 6)), inline=False)
        await ctx.send(embed=embed)

    # ---------- Slash Commands ----------

    @app_commands.command(
        name="ticket-setup",
        description="Erstellt ein neues Ticket-Panel für einen bestimmten Bereich.",
    )
    @app_commands.describe(
        kanal="Kanal, in dem das Panel gepostet wird",
        kategorie_id="ID der Kategorie, in der neue Ticket-Kanäle erstellt werden",
        anzeige_name="Name/Titel, der im Embed und Kanalnamen verwendet wird",
        bild_url="URL des Thumbnails für das Embed",
        gesperrte_rolle="Rolle, die KEINE Tickets in diesem System öffnen darf",
        log_kanal="Kanal, in den Transkripte beim Schließen gesendet werden",
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
        embed.set_footer(text=f"{get_footer_text(interaction)}  ·  Ticket-System")

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
            "rating_log_kanal_id": log_kanal.id,
        }

        def mutate(data):
            data.setdefault("panels", {})[panel_id] = panel_data
            return data

        await self.config_store.update(mutate)

        view = TicketOpenView(panel_id)
        self.bot.add_view(view)
        await sent_message.edit(view=view)

        await interaction.followup.send(
            f"✅ Ticket-Panel **{anzeige_name}** wurde in {kanal.mention} erstellt.",
            ephemeral=True,
        )

    @ticket_setup.error
    async def ticket_setup_error(self, interaction: discord.Interaction, error):
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

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True, read_message_history=True
            ),
        }
        for role_id in config.get("support_role_ids", []):
            role = guild.get_role(role_id)
            if role:
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
                f"Willkommen {member.mention}!\n\n{panel.get('panel_text', 'Beschreibe dein Anliegen so genau wie möglich.')}\n"
                f"Ein Teammitglied wird sich bald um dich kümmern.\n\n"
                f"🔒 Nutze den Button unten um das Ticket zu schließen."
            ),
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=f"{get_footer_text(interaction)}  ·  Ticket-System")
        bild = panel.get("bild_url", "")
        if bild and bild.startswith("http"):
            embed.set_thumbnail(url=bild)

        await ticket_channel.send(
            content=member.mention, embed=embed, view=TicketCloseView()
        )

        def mutate(data):
            data[str(ticket_channel.id)] = {
                "user_id": member.id,
                "panel_id": panel_id,
                "anzeige_name": panel["anzeige_name"],
                "log_kanal_id": panel["log_kanal_id"],
                "rating_log_kanal_id": panel.get("rating_log_kanal_id", panel["log_kanal_id"]),
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            return data

        await self.open_store.update(mutate)
        await self._refresh_metrics()

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
            "  AVOKE | System — Ticket-Transkript",
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
                log_embed.set_footer(text=f"{get_footer_text(interaction)}  ·  Ticket-System")
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
        if ticket_owner is None:
            try:
                ticket_owner = await self.bot.fetch_user(info.get("user_id"))
            except discord.HTTPException:
                ticket_owner = None
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
            dm_embed.set_footer(text=get_footer_text(interaction))
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

            # Die Bewertung wird separat versendet, damit die Interaktion auch nach dem Loeschen des Kanals funktioniert.
            ticket_id = str(channel.id)
            def add_pending(data):
                data.setdefault("pending", {})[ticket_id] = {
                    "ticket_id": ticket_id,
                    "user_id": info.get("user_id"),
                    "supporter_id": interaction.user.id,
                    "supporter_name": str(interaction.user),
                    "panel_name": panel_name,
                    "log_kanal_id": info.get("rating_log_kanal_id") or info.get("log_kanal_id"),
                    "closed_at": closed_at.isoformat(),
                }
                return data
            await self.ratings_store.update(add_pending)
            view = TicketRatingView(ticket_id)
            self.bot.add_view(view)
            try:
                await ticket_owner.send(
                    "Wie zufrieden warst du mit deinem Ticket-Support?", view=view
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

        # ── Store bereinigen & Kanal löschen ─────────────────────────────────
        def mutate(data):
            data.pop(str(channel.id), None)
            return data

        await self.open_store.update(mutate)
        await self._refresh_metrics()
        asyncio.create_task(self._delete_channel_after(channel, interaction.user))

    async def save_rating(self, interaction: discord.Interaction, ticket_id: str, stars: int, feedback: str):
        data = await self.ratings_store.read()
        pending = data.get("pending", {}).get(ticket_id)
        if pending is None:
            await interaction.response.send_message("Diese Bewertung wurde bereits verarbeitet oder ist nicht mehr gueltig.", ephemeral=True)
            return
        if interaction.user.id != pending.get("user_id"):
            await interaction.response.send_message("Diese Bewertung gehoert nicht zu dir.", ephemeral=True)
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        rating = {**pending, "stars": stars, "feedback": feedback.strip(), "rated_at": now.isoformat()}

        def persist(current):
            current.setdefault("ratings", []).append(rating)
            current.setdefault("pending", {}).pop(ticket_id, None)
            return current
        await self.ratings_store.update(persist)
        await self._refresh_metrics()
        log_channel = interaction.guild.get_channel(pending["log_kanal_id"]) if interaction.guild else None
        if log_channel is None:
            # DMs haben keine Guild; der Kanal ist ueber die gecachten Guilds aufloesbar.
            log_channel = next((g.get_channel(pending["log_kanal_id"]) for g in self.bot.guilds if g.get_channel(pending["log_kanal_id"])), None)
        if log_channel:
            embed = discord.Embed(title="Ticketbewertung", color=discord.Color.gold(), timestamp=now)
            embed.add_field(name="Ticket-ID", value=ticket_id, inline=True)
            embed.add_field(name="Bewertung", value=f"{'★' * stars} ({stars}/5)", inline=True)
            embed.add_field(name="Ersteller", value=f"<@{pending['user_id']}>", inline=True)
            embed.add_field(name="Supporter", value=f"<@{pending['supporter_id']}>", inline=True)
            embed.add_field(name="Feedback", value=feedback.strip() or "Kein Feedback hinterlassen.", inline=False)
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass
        await interaction.response.send_message("Danke fuer deine Bewertung!", ephemeral=True)

    async def _delete_channel_after(self, channel: discord.TextChannel, closer: discord.Member):
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket geschlossen von {closer}")
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
