"""Sichtbarer Überblick über die aktuellen System-Erweiterungen."""

import discord
from discord.ext import commands
from utils.theme import FOOTER_TEXT, get_footer_text


class Changelog(commands.Cog):
    """Stellt den aktuellen Funktionsstand direkt in Discord bereit."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="changelog")
    async def changelog(self, ctx: commands.Context):
        """Zeigt die wichtigsten Neuerungen des Bot-Systems an."""
        guild_name = ctx.guild.name if ctx.guild else "AVOKE"
        embed = discord.Embed(
            title=f"{guild_name} | System — Changelog",
            description="Die neuesten Erweiterungen auf einen Blick.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🎫 Ticket-System",
            value=(
                "- Ticketbewertung mit 1 bis 5 Sternen und optionalem Feedback\n"
                "- Bewertungslog mit Ticket, Ersteller, Supporter und Zeitpunkt\n"
                "- `!ratingstats` für Durchschnitt und Sternverteilung\n"
                "- Erweiterte Ticketpanel- und Log-Konfiguration"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ Ticket-Verwaltung",
            value=(
                "- Moderne Ticket-GUI mit Buttons und Auswahlmenüs (`/ticket-gui`)\n"
                "- Panels erstellen, bearbeiten und löschen\n"
                "- Kategorien, Rollen, Supportrollen und Log-Kanäle verwalten"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ Owner & Monitoring",
            value=(
                "[+] `!system stats` zeigt jetzt auch offene Tickets & Ø-Bewertung\n"
                "[+] Logging für `!system restart`, `!system shutdown`, alle `!system`-Aktionen\n"
                "[/] Alle System-Commands unter `!system` zusammengefasst\n"
                "  `!system restart` · `!system shutdown` · `!system reload`\n"
                "  `!system stats` · `!system backup` · `!system health`\n"
                "[/] Erweiterte Slash-Sync-Optionen: `/sync` (global/guild/clear)\n"
                "[-] `!restart`, `!shutdown`, `!reload`, `!reloadall`, `!sync`, `!stats` entfernt — alle unter `!system`\n"
                "[-] `!ticketgui` entfernt — nur noch `/ticket-gui`\n"
                "[-] `!updates` Alias entfernt"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚠️ Slash-Commands",
            value=(
                "[+] `GUILD_ID` in `.env` hinzugefügt — Guild-Sync statt Global-Sync\n"
                "[/] Kein globales 100-Command-Limit mehr — unbegrenzt auf dem eigenen Server\n"
                "[/] Commands erscheinen sofort (kein 1h-Delay mehr)\n"
                "  → `GUILD_ID=DEINE_SERVER_ID` in `.env` eintragen!"
            ),
            inline=False,
        )
        embed.add_field(
            name="🚀 Starter & Restart-System",
            value=(
                "[+] `start.cmd` — Wrapper-Skript das den Bot automatisch neu startet\n"
                "[+] `!system restart` sendet Exit-Code `42` → `start.cmd` startet in 2s neu\n"
                "[+] `!system shutdown` sendet Exit-Code `0` → sauberes Beenden\n"
                "[/] Restart funktioniert jetzt zu 100% auf Windows (kein `os.execv` mehr)"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎉 Giveaway-System",
            value=(
                "[+] Dauer in Stunden (`dauer_stunden: float`) — Dezimalwerte erlaubt (z. B. `1.5`)\n"
                "[+] Live-Teilnehmerzähler auf dem Giveaway-Embed, wird bei jedem Beitritt aktualisiert\n"
                "[+] Teilnehmerzahl in der ephemerischen Beitrittsbestätigung angezeigt\n"
                "[+] Deaktivierter End-Button nach Giveaway-Abschluss\n"
                "[/] Embed-Design komplett überarbeitet — einheitliche Felder, Trennlinie, Theme-Farben\n"
                "[/] Beendetes Giveaway ersetzt die Originalnachricht (edit statt neuem send)\n"
                "[/] Gewinner-Ping nutzt `AllowedMentions` für korrekte Mention-Kontrolle\n"
                "[-] Parameter `dauer_minuten` entfernt — ersetzt durch `dauer_stunden`"
            ),
            inline=False,
        )
        embed.add_field(
            name="💰 Economy-System",
            value=(
                "[+] `/rob` — Bestehle einen anderen User (45% Erfolg, 90 Min. Cooldown)\n"
                "[+] `/gamble` — Setze Coins auf Glück (50/50, 30s Cooldown)\n"
                "[+] `/give` — Schenke Coins mit persönlicher Nachricht\n"
                "[/] Alle Cooldowns laufen atomar in `mutate()` — kein Race-Condition-Bug mehr"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎮 Fun-System",
            value=(
                "[+] `/ship` — Liebes-Kompatibilität zweier User berechnen\n"
                "[+] `/would-you-rather` — Would-You-Rather mit Live-Voting-Buttons\n"
                "[+] `/truth-or-dare` — Truth oder Dare (zufällig oder per Wahl)\n"
                "[+] `/trivia` — Trivia-Frage mit interaktiven Button-Antworten (30s)\n"
                "[/] Mehr Witze, mehr Meme-Formate, Fakten bereinigt"
            ),
            inline=False,
        )
        embed.add_field(
            name="🗂️ Logging & Dashboard",
            value=(
                "[+] `utils/logger.py` — Zentrales Multi-File-Logging mit `RotatingFileHandler` (max 10 MB/Datei)\n"
                "  `logs/bot.log` · `logs/error.log` · `logs/command.log`\n"
                "  `logs/moderation.log` · `logs/system.log` · `logs/startup.log`\n"
                "[+] `cogs/dashboard.py` — Komplett überarbeitetes professionelles ASCII-Dashboard\n"
                "  ↳ Neue Sektionen: Ressourcen, Community, Bot-System, Cog-Liste, Letzte Fehler\n"
                "  ↳ Neue Werte: discord.py-Version, Python-Version, OS, Kanäle, Slash-Commands, Wartungsmodus\n"
                "  ↳ Ping-Qualitäts-Indikator (●●● Excellent / ●●○ Good / …)\n"
                "  ↳ Vollständige Box-Zeichnung mit `╔══╗ / ╠══╣ / ╚══╝`\n"
                "[+] Startup-Banner in `logs/startup.log` — Bot, Server, User, Sync-Mode, Cogs\n"
                "[/] `main.py` — `logging.basicConfig()` ersetzt durch `setup_logging()` aus `utils/logger.py`\n"
                "[/] Command-Fehler loggen jetzt in `logs/command.log` statt root-Logger"
            ),
            inline=False,
        )
        embed.add_field(
            name="⭐ XP & Rang",
            value=(
                "[+] Level-Up Benachrichtigung im Channel (verschwindet nach 15s)\n"
                "[/] Level-Up zeigt direkt das nächste XP-Ziel an"
            ),
            inline=False,
        )
        embed.add_field(
            name="⏰ Utility",
            value=(
                "[+] `/remind` — Jetzt mit separaten Stunden + Minuten Feldern (max. 48h)\n"
                "[+] Zeigt Discord-Timestamp (<t:…:R>) wann die Erinnerung kommt\n"
                "[-] Altes `minuten`-Parameter entfernt — ersetzt durch `stunden` + `minuten`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔧 Owner-Admin-Erweiterungen",
            value=(
                "[+] `/owner owners`          — Alle konfigurierten Bot-Owner anzeigen\n"
                "[+] `/owneradmin eval`        — Python-Ausdruck direkt auswerten\n"
                "[+] `/owneradmin purgelogs`   — Alle Log-Dateien leeren\n"
                "[+] `/owneradmin serverleave` — Bot verlässt einen Server per ID\n"
                "[+] `/owneradmin userinfo`    — Detaillierte User-Infos (auch ohne Servermitglied)\n"
                "[+] `/owneradmin say`         — Bot sendet Nachricht im aktuellen Kanal\n"
                "[+] `/owneradmin activity`    — Bot-Aktivität live ändern (Playing/Watching/…)\n"
                "[+] `/owneradmin status`      — Bot-Online-Status ändern (Online/Idle/DND/Invisible)\n"
                "[+] `/owneradmin slowmode`    — Slowmode in beliebigem Kanal setzen\n"
                "[+] `/owneradmin nickname`    — Bot-Nickname auf dem Server ändern\n"
                "[+] `/owneradmin massrole`    — Rolle an alle Mitglieder vergeben/entfernen\n"
                "[+] `/owneradmin dmowners`    — DM an alle konfigurierten Bot-Owner\n"
                "[+] `/owneradmin serverinfo`  — Detaillierte Server-Informationen\n"
                "[+] `/owneradmin clearcache`  — JSONStore TTL-Cache invalidieren\n"
                "[/] `owner_admin.py` — !tickets + !dm-all auf Multi-Owner + utils/logger umgestellt"
            ),
            inline=False,
        )
        embed.add_field(
            name="👑 Owner-Panel",
            value=(
                "[+] `cogs/owner_panel.py` — 25 neue `/owner-*` Slash-Commands (alle ephemeral, Owner-Only)\n"
                "  `/owner-stats`        — Live-Statistiken (RAM, CPU, Ping, Uptime, Cogs, …)\n"
                "  `/owner-health`       — Ticket-Konfiguration & Bot-Berechtigungen prüfen\n"
                "  `/owner-ping`         — Aktuelle WebSocket-Latenz\n"
                "  `/owner-uptime`       — Uptime mit Discord-Timestamp\n"
                "  `/owner-reload`       — Einzelnen Cog neu laden\n"
                "  `/owner-reloadall`    — Alle Cogs neu laden\n"
                "  `/owner-coglist`      — Geladene Cogs als nummerierte Liste\n"
                "  `/owner-sync`         — guild / global / clear Sync\n"
                "  `/owner-logs`         — Letzte 20 Zeilen einer Log-Datei\n"
                "  `/owner-errors`       — Fehler-Ringpuffer anzeigen\n"
                "  `/owner-memory`       — Detaillierter RAM/CPU-Bericht\n"
                "  `/owner-diagnostics`  — Vollständiger Systemdiagnose-Report\n"
                "  `/owner-backup`       — data/ als ZIP sichern + hochladen\n"
                "  `/owner-backuplist`   — Alle vorhandenen Backups auflisten\n"
                "  `/owner-clearbackups` — Alte Backups aufräumen (behalte 5)\n"
                "  `/owner-maintenance`  — Wartungsmodus on/off mit Nachricht\n"
                "  `/owner-announce`     — Embed-Ankündigung in beliebigen Kanal\n"
                "  `/owner-botmessage`   — Textnachricht als Bot senden\n"
                "  `/owner-dm`           — DM an einen User\n"
                "  `/owner-guilds`       — Alle Server mit Mitgliederanzahl\n"
                "  `/owner-cache`        — JSONStore-Locks & Discord-Cache\n"
                "  `/owner-clearerrors`  — Fehler-Ringpuffer leeren\n"
                "  `/owner-restart`      — Neustart (Exit-Code 42 → start.cmd)\n"
                "  `/owner-shutdown`     — Sauberes Herunterfahren (Exit-Code 0)\n"
                "[/] Alle `!system`-Funktionen jetzt auch als Slash-Commands verfügbar\n"
                "[/] `cogs/system_tools.py` — Log-Aufrufe auf `utils/logger.get_logger('system')` umgestellt"
            ),
            inline=False,
        )
        embed.set_footer(text=get_footer_text(ctx.guild))
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Changelog(bot))
