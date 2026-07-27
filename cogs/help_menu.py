"""
Dropdown-Hilfe-Menü: /help mit Select-Menü für alle Befehlskategorien.
Dark-Mode Design: pro Kategorie eigene Farbe und übersichtliche Embed-Seiten.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.theme import FOOTER_TEXT, COLOR_INFO, COLOR_GOLD, COLOR_PRIMARY, COLOR_PURPLE, COLOR_CYAN, COLOR_BLURPLE, COLOR_WARNING, get_footer_text

# Kategorien: (Label, Emoji, Farbe, Beschreibung, [(command, beschreibung)])
HELP_CATEGORIES = [
    (
        "Economy",
        "💰",
        COLOR_GOLD,
        "Verwalte deine Coins, kaufe Items und erklimme die Rangliste.",
        [
            ("/balance [user]",           "Konto, Bank & Gesamtvermögen anzeigen."),
            ("/daily",                    "Täglicher Bonus — alle 20h."),
            ("/work",                     "Für Coins arbeiten — alle 30 Min."),
            ("/crime",                    "Verbrechen — riskant, aber lukrativ (60 Min.)."),
            ("/slut",                     "Zweifelhafte Dienste — alle 45 Min."),
            ("/pay <user> <betrag>",      "Coins an einen User überweisen."),
            ("/deposit <betrag|all>",     "Coins auf die Bank einzahlen."),
            ("/withdraw <betrag|all>",    "Coins von der Bank abheben."),
            ("/shop",                     "Coin-Shop anzeigen."),
            ("/buy <item_id>",            "Item aus dem Shop kaufen."),
            ("/inventory [user]",         "Inventar anzeigen."),
            ("/leaderboard",              "Top-10 der reichsten Spieler."),
        ],
    ),
    (
        "Moderation",
        "🛡️",
        discord.Color.from_rgb(235, 77, 75),
        "Moderations-Werkzeuge für das Team.",
        [
            ("/ban <user> [grund]",             "User dauerhaft bannen."),
            ("/tempban <user> <std> [grund]",   "Temporärer Bann (1–720 Std.)."),
            ("/unban <user_id> [grund]",        "User entbannen."),
            ("/kick <user> [grund]",            "User vom Server kicken."),
            ("/mute <user> [min] [grund]",      "User muten."),
            ("/unmute <user>",                  "Mute aufheben."),
            ("/timeout <user> <min> [grund]",   "Discord-Timeout setzen."),
            ("/untimeout <user>",               "Timeout aufheben."),
            ("/warn <user> <grund>",            "User verwarnen."),
            ("/warn-remove <user> <nr>",        "Einzelne Verwarnung entfernen."),
            ("/warn-clear <user>",              "Alle Verwarnungen löschen."),
            ("/warnings <user>",                "Alle Verwarnungen anzeigen."),
            ("/lock [kanal]",                   "Kanal für @everyone sperren."),
            ("/unlock [kanal]",                 "Kanal entsperren."),
            ("/lockdown <aktiv>",               "Server-Lockdown aktivieren/deaktivieren."),
            ("/say <nachricht> [kanal]",        "Bot sendet eine Nachricht."),
            ("/announce <kanal> <titel> <txt>", "Offizielle Ankündigung senden."),
            ("/resetnick <user>",               "Nickname zurücksetzen."),
        ],
    ),
    (
        "Clan-Log",
        "📋",
        COLOR_PURPLE,
        "Clan-Aktionen protokollieren, loggen und archivieren.",
        [
            ("/clanlog-setup <kanal>",        "Clan-Log-Kanal festlegen (Admin)."),
            ("/uprank <member> <alt> <neu>",  "Mitglied hochstufen + loggen."),
            ("/derank <member> <alt> <neu>",  "Mitglied herabstufen + loggen."),
            ("/clan-kick <member> [grund]",   "Clan-Kick mit optionalem Server-Kick."),
            ("Auto-Log",                      "Ban, Mute, Join, Leave, Rollen — alles automatisch."),
        ],
    ),
    (
        "Tickets",
        "🎫",
        COLOR_BLURPLE,
        "Support-Ticket-System für den Server.",
        [
            ("/ticket-setup",  "Neues Ticket-Panel erstellen (Admin)."),
            ("/ticket-gui",    "Moderne GUI zur Verwaltung aller Panels (Admin)."),
            ("!ticketgui",     "Alternative Prefix-Variante der Ticket-GUI (Admin)."),
            ("🎫 Button",      "Ticket über ein Panel öffnen."),
            ("🔒 Button",      "Ticket schließen → Transkript wird erstellt."),
            ("⭐ DM-Bewertung", "Nach dem Schließen: Sterne + optionales Feedback per DM."),
            ("!ratingstats",   "Durchschnitt, Anzahl & Verteilung aller Ticketbewertungen."),
        ],
    ),
    (
        "Rang & XP",
        "⭐",
        COLOR_PURPLE,
        "XP-System und grafische Rank-Cards.",
        [
            ("/rank [user]",             "Rank-Card mit Level und XP anzeigen."),
            ("/xp-leaderboard",          "Top-10 nach Level/XP."),
            ("/addxp <user> <menge>",    "[Admin] XP hinzufügen."),
            ("/removexp <user> <menge>", "[Admin] XP entfernen."),
            ("/setxp <user> <menge>",    "[Admin] XP auf Wert setzen."),
            ("/resetxp <user>",          "[Admin] XP auf 0 zurücksetzen."),
            ("XP Auto",                  "5 XP pro Nachricht, vollautomatisch."),
        ],
    ),
    (
        "Server-Tools",
        "🔧",
        COLOR_INFO,
        "Server-Informationen und Admin-Werkzeuge.",
        [
            ("/setup",       "Geführtes Server-Setup für alle Module (Admin)."),
            ("/serverinfo",  "Detaillierte Server-Informationen."),
            ("/botinfo",     "Bot-Infos: Ping, Uptime, Stats."),
            ("/backup",      "Alle Daten als ZIP exportieren (Admin)."),
        ],
    ),
    (
        "Verifizierung",
        "🔐",
        COLOR_PURPLE,
        "Rollen-Vergabe per Button-Panel für neue Mitglieder.",
        [
            ("/verify-setup",  "Verifizierungs-Panel einrichten (Admin)."),
            ("✅ Button",       "Klicken → Verifizierungs-Rolle erhalten."),
        ],
    ),
    (
        "Welcome / Leave",
        "👋",
        COLOR_PRIMARY,
        "Begrüßungs- und Abschiedsnachrichten mit Banner.",
        [
            ("/welcome-setup",  "Welcome- und Leave-Kanal konfigurieren (Admin)."),
            ("Auto Join",       "Grafisches Banner bei Server-Beitritt."),
            ("Auto Leave",      "Embed bei Server-Verlassen."),
        ],
    ),
    (
        "Giveaways",
        "🎉",
        COLOR_GOLD,
        "Gewinnspiele erstellen, beenden und neu auslosen.",
        [
            ("/giveaway-start <preis> <dauer> [gewinner]", "Neues Giveaway starten."),
            ("/giveaway-end <nachrichten_id>",             "Giveaway sofort beenden."),
            ("/giveaway-reroll <nachrichten_id>",          "Neuen Gewinner auslosen."),
        ],
    ),
    (
        "Streamer",
        "📡",
        discord.Color.from_rgb(130, 80, 255),
        "Go-Live-Ankündigungen für Twitch-Streamer.",
        [
            ("/stream-setup <kanal> <rolle>", "Kanal + Ping-Rolle konfigurieren (Admin)."),
            ("/stream-add <mitglied>",         "Mitglied zur Whitelist hinzufügen."),
            ("/stream-remove <mitglied>",      "Mitglied von der Whitelist entfernen."),
            ("/stream-list",                   "Whitelist + letzte Live-Zeiten."),
            ("/stream-all <aktiv>",            "Alle Mitglieder ankündigen (kein Whitelist-Modus)."),
            ("/bot-status <typ> <text>",       "Bot-Presence ändern (Admin)."),
        ],
    ),
    (
        "Koordinaten",
        "🗺️",
        COLOR_CYAN,
        "Koordinaten-Verwaltung für den SMP.",
        [
            ("/coords-add",    "Koordinaten speichern."),
            ("/coords-get",    "Gespeicherte Koordinaten abrufen."),
            ("/coords-remove", "Koordinateneintrag löschen."),
        ],
    ),
    (
        "Utility",
        "🛠️",
        COLOR_INFO,
        "Allgemein-Commands: Info, Tools, Unterhaltung.",
        [
            ("/avatar [user]",            "Avatar in voller Größe anzeigen."),
            ("/userinfo [user]",           "Detaillierte User-Informationen."),
            ("/roleinfo <rolle>",           "Informationen über eine Rolle."),
            ("/poll <frage> <opt1> <opt2>", "Abstimmung mit Reaktionen erstellen."),
            ("/remind <min> <nachricht>",  "Persönliche Erinnerung (max. 24h)."),
            ("/snipe",                     "Zuletzt gelöschte Nachricht anzeigen."),
            ("/clear <anzahl> [user]",     "Nachrichten löschen (Mod)."),
            ("/slowmode <sek>",            "Kanal-Slowmode setzen (Mod)."),
            ("/nick <user> [name]",        "Nickname ändern (Mod)."),
            ("/coinflip",                  "Münzwurf — Kopf oder Zahl?"),
            ("/8ball <frage>",             "Magische 8-Ball Antwort."),
            ("/afk [grund]",               "AFK-Status setzen."),
            ("/membercount",               "Aktuelle Mitgliederzahl."),
        ],
    ),
    (
        "Promotion",
        "⬆️",
        COLOR_PRIMARY,
        "Beförderungen und Herabstufungen mit Rang-Hierarchie.",
        [
            ("/rank-setup <rang1> <rang2> …", "Rang-Hierarchie festlegen (Admin)."),
            ("/rank-list",                     "Aktuelle Rang-Hierarchie anzeigen."),
            ("/promote <member> [grund]",       "Mitglied auf nächsten Rang befördern."),
            ("/demote <member> [grund]",        "Mitglied auf vorigen Rang herabstufen."),
        ],
    ),
    (
        "Fun",
        "🎲",
        COLOR_WARNING,
        "Spaß und Entertainment für den Server.",
        [
            ("/joke",           "Zufälliger Witz."),
            ("/meme",           "Zufälliges Meme-Format."),
            ("/hug <user>",     "Umarme ein Mitglied."),
            ("/kiss <user>",    "Küsse ein Mitglied."),
            ("/slap <user>",    "Schlage ein Mitglied."),
            ("/pat <user>",     "Täschle ein Mitglied."),
            ("/rps <wahl>",     "Schere-Stein-Papier gegen den Bot."),
            ("/cat",            "Zufälliger Katzen-Fakt."),
            ("/dog",            "Zufälliger Hunde-Fakt."),
            ("/fox",            "Zufälliger Fuchs-Fakt."),
        ],
    ),
    (
        "Owner",
        "👑",
        discord.Color.from_rgb(44, 47, 51),
        "Verwaltungscommands — nur für den Bot-Owner nutzbar.",
        [
            ("!restart",          "Startet den Bot sauber neu."),
            ("!shutdown",         "Beendet den Bot sauber."),
            ("!reload <cog>",     "Lädt einen einzelnen Cog neu."),
            ("!reloadall",        "Lädt alle Cogs neu."),
            ("!sync",             "Synchronisiert alle Slash-Commands."),
            ("!stats",            "Bot-Statistik: Ping, RAM, CPU, Server, Uptime, uvm."),
            ("!tickets",          "Übersicht aller aktuell offenen Tickets."),
        ],
    ),
]


def _build_category_embed(category: tuple, guild=None) -> discord.Embed:
    label, emoji, color, desc, cmds = category
    embed = discord.Embed(
        title=f"{emoji}  {label}",
        description=f"*{desc}*",
        color=color,
    )
    # Discord erlaubt höchstens 1.024 Zeichen pro Feld. Kategorien wie
    # Moderation enthalten deutlich mehr Befehle, daher sauber aufteilen.
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for cmd, cdesc in cmds:
        line = f"╴ `{cmd}`\n  ↳ {cdesc}"
        if current and current_length + len(line) + 1 > 1000:
            chunks.append("\n".join(current))
            current, current_length = [], 0
        current.append(line)
        current_length += len(line) + 1
    if current:
        chunks.append("\n".join(current))

    for index, chunk in enumerate(chunks, start=1):
        suffix = f" ({index}/{len(chunks)})" if len(chunks) > 1 else ""
        embed.add_field(name=f"📌 Befehle{suffix}", value=chunk, inline=False)
    embed.set_footer(text=get_footer_text(guild))
    return embed


def _build_home_embed(bot_user: discord.ClientUser, guild=None) -> discord.Embed:
    """Landing-Embed wenn /help aufgerufen wird."""
    cat_lines = "  ".join(
        f"{emoji} **{label}**" for label, emoji, *_ in HELP_CATEGORIES
    )
    server_name = guild.name if guild else "Bot"
    embed = discord.Embed(
        title=f"📖  {server_name} — Hilfe",
        description=(
            "Wähle eine Kategorie aus dem Dropdown-Menü um alle verfügbaren Befehle zu sehen.\n\n"
            f"{cat_lines}"
        ),
        color=discord.Color.from_rgb(88, 101, 242),
    )
    embed.set_thumbnail(url=bot_user.display_avatar.url)
    embed.add_field(
        name="💡 Tipp",
        value="Alle Slash-Commands beginnen mit `/` — tippe `/` im Chat um die Liste zu sehen.",
        inline=False,
    )
    embed.set_footer(text=get_footer_text(guild))
    return embed


class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, description=desc[:100], emoji=emoji)
            for label, emoji, _, desc, _ in HELP_CATEGORIES
        ]
        super().__init__(
            placeholder="📖  Kategorie auswählen …",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="help:select",
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        for cat in HELP_CATEGORIES:
            if cat[0] == chosen:
                embed = _build_category_embed(cat, interaction.guild)
                await interaction.response.edit_message(embed=embed, view=self.view)
                return


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.message: discord.InteractionMessage | None = None
        self.add_item(HelpSelect())

    @discord.ui.button(label="Startseite", emoji="🏠", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = _build_home_embed(interaction.client.user, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class HelpMenu(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Öffnet das interaktive Hilfe-Menü.")
    async def help_cmd(self, interaction: discord.Interaction):
        embed = _build_home_embed(self.bot.user, interaction.guild)
        view = HelpView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpMenu(bot))
