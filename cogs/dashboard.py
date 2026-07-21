"""
CMD-Live-Dashboard — professionelles ASCII-Dashboard für die Konsole.

Zeigt beim Start und fortlaufend (alle 5 s) einen vollständigen Systemüberblick:
  ┌ Status / Ping / Uptime
  ├ CPU / RAM / Python-Version / discord.py-Version
  ├ Server / User / Kanäle
  ├ Offene Tickets / Ø-Bewertung
  ├ Slash-Commands / Cogs
  ├ Economy / Wartungsmodus / Cache
  └ Letzte Fehler

Das Dashboard ersetzt NICHT das Datei-Logging — beide laufen parallel.
"""

from __future__ import annotations

import os
import math
import platform
import datetime

import discord
from discord import __version__ as DISCORD_VERSION
from discord.ext import commands, tasks

try:
    import psutil
except ImportError:
    psutil = None

from utils.storage import JSONStore
from utils.errorlog import recent_errors
from utils.system_state import get_maintenance_state

from cogs.tickets import OPEN_TICKETS_PATH
from cogs.ratings import compute_rating_stats

_open_tickets_store = JSONStore(OPEN_TICKETS_PATH, {})

REFRESH_SECONDS = 5

# ── Box-Geometrie ──────────────────────────────────────────────────────────────
W = 68         # Gesamtbreite der Box (inkl. ║ links und ║ rechts)
INNER = W - 4  # Nutzbare Innenbreite (nach "║ " und " ║")

_TOP    = "╔" + "═" * (W - 2) + "╗"
_BOTTOM = "╚" + "═" * (W - 2) + "╝"
_DIV_H  = "╠" + "═" * (W - 2) + "╣"
_DIV_S  = "║" + "─" * (W - 2) + "║"


def _bar(label: str, value: str) -> str:
    """Eine beschriftete Zeile innerhalb der Box."""
    pad = INNER - len(label) - len(value)
    if pad < 1:
        pad = 1
    return f"║ {label}{' ' * pad}{value} ║"


def _center(text: str) -> str:
    """Zentriert einen Text in der Box."""
    return f"║{text:^{W - 2}}║"


def _left(text: str) -> str:
    """Linksbündig mit Padding."""
    return f"║ {text:<{INNER}} ║"


def _section(title: str) -> str:
    """Abschnitts-Kopf mit Trennlinie."""
    return f"║  ▸ {title:<{INNER - 3}} ║"


def _format_uptime(delta: datetime.timedelta) -> str:
    total = int(delta.total_seconds())
    d, r  = divmod(total, 86400)
    h, r  = divmod(r, 3600)
    m, s  = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ── Cog ───────────────────────────────────────────────────────────────────────

class Dashboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        self._process = psutil.Process(os.getpid()) if psutil else None

    async def cog_load(self):
        self.refresh_dashboard.start()

    def cog_unload(self):
        self.refresh_dashboard.cancel()

    # ── Task ──────────────────────────────────────────────────────────────────

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def refresh_dashboard(self):
        bot    = self.bot
        now    = datetime.datetime.now(datetime.timezone.utc)
        online = bot.is_ready() and not bot.is_closed()

        # ── Metriken sammeln ──────────────────────────────────────────────────
        ping_ms     = round(bot.latency * 1000) if math.isfinite(bot.latency) else 0
        guild_count = len(bot.guilds)
        user_count  = sum(g.member_count or 0 for g in bot.guilds)
        chan_count   = sum(len(g.channels) for g in bot.guilds)
        cmd_count   = len([c for c in bot.tree.get_commands()])
        cog_count   = len(bot.cogs)

        if self._process is not None:
            ram_mb      = self._process.memory_info().rss / (1024 * 1024)
            cpu_percent = self._process.cpu_percent(interval=None)
        else:
            ram_mb      = 0.0
            cpu_percent = 0.0

        open_tickets = await _open_tickets_store.read()
        rating_stats = await compute_rating_stats()
        maintenance  = (await get_maintenance_state())["maintenance"]

        launch_time = getattr(bot, "launch_time", None)
        uptime_str  = _format_uptime(now - launch_time) if launch_time else "—"

        loaded_cogs  = sorted(ext.split(".")[-1] for ext in bot.extensions.keys())
        cogs_preview = ", ".join(loaded_cogs)

        py_ver = platform.python_version()
        os_ver = f"{platform.system()} {platform.release()}"

        # Ping-Qualität-Indikator
        if   ping_ms == 0:        ping_q = "—"
        elif ping_ms < 80:        ping_q = "●●● Excellent"
        elif ping_ms < 150:       ping_q = "●●○ Good"
        elif ping_ms < 300:       ping_q = "●○○ Fair"
        else:                     ping_q = "○○○ Poor"

        status_icon = "● ONLINE " if online else "● OFFLINE"

        # ── Box rendern ───────────────────────────────────────────────────────
        now_str = now.strftime("%Y-%m-%d  %H:%M:%S UTC")
        lines = [
            "",
            _TOP,
            _center(f"  AVOKE  │  System Dashboard  │  {now_str}  "),
            _DIV_H,
            # Status & Uptime
            _section("Status & Uptime"),
            _bar("  Status",  status_icon),
            _bar("  Uptime",  uptime_str),
            _bar("  Ping",    f"{ping_ms} ms  {ping_q}"),
            _DIV_S,
            # Ressourcen
            _section("Ressourcen"),
            _bar("  CPU",         f"{cpu_percent:.1f} %"),
            _bar("  RAM",         f"{ram_mb:.1f} MB"),
            _bar("  Python",      py_ver),
            _bar("  discord.py",  DISCORD_VERSION),
            _bar("  System",      os_ver),
            _DIV_S,
            # Community
            _section("Community"),
            _bar("  Server",   str(guild_count)),
            _bar("  User",     str(user_count)),
            _bar("  Kanäle",   str(chan_count)),
            _DIV_S,
            # Bot-System
            _section("Bot-System"),
            _bar("  Slash-Commands",  str(cmd_count)),
            _bar("  Cogs geladen",    f"{cog_count}"),
            _bar("  Offene Tickets",  str(len(open_tickets))),
            _bar("  Ø Bewertung",     f"{rating_stats['average']}/5  ({rating_stats['count']}x)"),
            _bar("  Wartungsmodus",   "⚠ AKTIV" if maintenance else "○ Inaktiv"),
            _DIV_S,
            # Cog-Liste
            _section(f"Geladene Cogs  ({cog_count})"),
        ]

        # Cog-Namen umbrechen
        for i in range(0, max(len(cogs_preview), 1), INNER - 2):
            chunk = cogs_preview[i : i + INNER - 2]
            if chunk:
                lines.append(_left(f"  {chunk}"))

        lines.append(_DIV_S)
        lines.append(_section("Letzte Fehler"))

        if recent_errors:
            for err in list(recent_errors)[-4:]:
                # Auf maximal INNER Zeichen begrenzen
                short = err[-(INNER - 2):] if len(err) > INNER - 2 else err
                lines.append(_left(f"  {short}"))
        else:
            lines.append(_bar("  ", "✔ Keine Fehler bisher"))

        lines.append(_BOTTOM)
        lines.append("")

        # Konsole leeren und rendern
        os.system("cls" if platform.system() == "Windows" else "clear")
        print("\n".join(lines))

    @refresh_dashboard.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Dashboard(bot))
