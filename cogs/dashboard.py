"""
CMD-Live-Dashboard — visuell überarbeitetes Konsolen-Dashboard.

Neue Features:
  • ANSI-Farben (funktioniert auf Windows 10+ und allen UNIX-Terminals)
  • Farbige Fortschrittsbalken für CPU und RAM
  • Status-Ampel (grün / gelb / rot) für Ping, Fehler, Wartung
  • Animierter Spinner im Header
  • Kompakter 2-Spalten-Layout für Community-Metriken
  • Farbige Cog-Tags (grouped per Zeile)
  • Letzter Fehler farblich hervorgehoben
  • Saubere Unicode-Boxen mit doppelten/einfachen Rahmen
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

# ── ANSI Farb-Codes ────────────────────────────────────────────────────────────
# Werden deaktiviert wenn das Terminal keine Farben unterstützt (TERM=dumb etc.)
_USE_COLOR = (os.getenv("TERM") != "dumb") and (
    platform.system() != "Windows"
    or os.getenv("ANSICON") is not None
    or "WT_SESSION" in os.environ       # Windows Terminal
    or "COLORTERM" in os.environ
    or os.getenv("TERM_PROGRAM") in ("vscode", "hyper")
)

class C:
    """ANSI-Farbkonstanten."""
    RESET    = "\033[0m"    if _USE_COLOR else ""
    BOLD     = "\033[1m"    if _USE_COLOR else ""
    DIM      = "\033[2m"    if _USE_COLOR else ""

    # Vordergrundfarben
    BLACK    = "\033[30m"   if _USE_COLOR else ""
    RED      = "\033[31m"   if _USE_COLOR else ""
    GREEN    = "\033[32m"   if _USE_COLOR else ""
    YELLOW   = "\033[33m"   if _USE_COLOR else ""
    BLUE     = "\033[34m"   if _USE_COLOR else ""
    MAGENTA  = "\033[35m"   if _USE_COLOR else ""
    CYAN     = "\033[36m"   if _USE_COLOR else ""
    WHITE    = "\033[37m"   if _USE_COLOR else ""

    # Helle Varianten
    BRIGHT_RED    = "\033[91m" if _USE_COLOR else ""
    BRIGHT_GREEN  = "\033[92m" if _USE_COLOR else ""
    BRIGHT_YELLOW = "\033[93m" if _USE_COLOR else ""
    BRIGHT_BLUE   = "\033[94m" if _USE_COLOR else ""
    BRIGHT_CYAN   = "\033[96m" if _USE_COLOR else ""
    BRIGHT_WHITE  = "\033[97m" if _USE_COLOR else ""

    # Hintergrundfarben
    BG_RED    = "\033[41m"  if _USE_COLOR else ""
    BG_GREEN  = "\033[42m"  if _USE_COLOR else ""
    BG_BLUE   = "\033[44m"  if _USE_COLOR else ""
    BG_DARK   = "\033[40m"  if _USE_COLOR else ""


# ── Box-Geometrie ──────────────────────────────────────────────────────────────
W      = 72           # Gesamtbreite (inkl. Rahmen-Zeichen)
INNER  = W - 4        # Nutzbare Innenbreite (nach "║ " und " ║")

_TOP    = f"{C.DIM}╔{'═' * (W - 2)}╗{C.RESET}"
_BOT    = f"{C.DIM}╚{'═' * (W - 2)}╝{C.RESET}"
_DIV_H  = f"{C.DIM}╠{'═' * (W - 2)}╣{C.RESET}"
_DIV_S  = f"{C.DIM}║{'─' * (W - 2)}║{C.RESET}"
_PIPE   = f"{C.DIM}║{C.RESET}"

# Spinner-Frames für den Header
_SPINNER = ["◐", "◓", "◑", "◒"]
_spin_idx = 0


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _visible_len(s: str) -> int:
    """Gibt die sichtbare Länge eines ANSI-gefärbten Strings zurück."""
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def _row(left: str, right: str = "", *, width: int = INNER) -> str:
    """
    Erzeugt eine Box-Zeile mit optionalem rechts-bündigem Wert.
    Berücksichtigt ANSI-Escape-Sequenzen korrekt bei der Ausrichtung.
    """
    vis_left  = _visible_len(left)
    vis_right = _visible_len(right)
    pad = width - vis_left - vis_right
    if pad < 1:
        pad = 1
    return f"{_PIPE} {left}{' ' * pad}{right} {_PIPE}"


def _center_row(text: str) -> str:
    """Zentriert einen (ggf. ANSI-gefärbten) Text in der Box."""
    vis = _visible_len(text)
    total_pad = W - 2 - vis
    lpad = total_pad // 2
    rpad = total_pad - lpad
    return f"{_PIPE}{' ' * lpad}{text}{' ' * rpad}{_PIPE}"


def _section_header(icon: str, title: str, color: str = C.BRIGHT_CYAN) -> str:
    """Abschnitts-Header mit Icon und Farbe."""
    label = f"{C.BOLD}{color}{icon} {title}{C.RESET}"
    vis   = _visible_len(label)
    pad   = INNER - vis
    return f"{_PIPE} {label}{' ' * pad} {_PIPE}"


def _progress_bar(
    value:    float,
    max_val:  float = 100.0,
    width:    int   = 20,
    *,
    warn:     float = 70.0,
    crit:     float = 90.0,
) -> str:
    """
    Erzeugt einen farbigen Fortschrittsbalken.
    ▓ = gefüllt, ░ = leer
    Farben: grün → gelb → rot nach Schwellwerten.
    """
    pct   = min(value / max_val, 1.0) if max_val > 0 else 0.0
    filled = int(pct * width)
    bar    = "▓" * filled + "░" * (width - filled)
    pct_s  = f"{value:5.1f}%"

    if value >= crit:
        color = C.BRIGHT_RED
    elif value >= warn:
        color = C.BRIGHT_YELLOW
    else:
        color = C.BRIGHT_GREEN

    return f"{color}[{bar}]{C.RESET} {C.BOLD}{color}{pct_s}{C.RESET}"


def _ping_indicator(ping_ms: int) -> str:
    """Farbiger Ping-Indikator mit Qualitätsbeschreibung."""
    if ping_ms == 0:
        return f"{C.DIM}— nicht bereit{C.RESET}"
    if ping_ms < 80:
        return f"{C.BRIGHT_GREEN}{ping_ms} ms  ●●●  Excellent{C.RESET}"
    if ping_ms < 150:
        return f"{C.BRIGHT_GREEN}{ping_ms} ms  ●●○  Good{C.RESET}"
    if ping_ms < 300:
        return f"{C.BRIGHT_YELLOW}{ping_ms} ms  ●○○  Fair{C.RESET}"
    return f"{C.BRIGHT_RED}{ping_ms} ms  ○○○  Poor{C.RESET}"


def _status_dot(online: bool) -> str:
    if online:
        return f"{C.BG_GREEN}{C.BLACK} ● ONLINE  {C.RESET}"
    return f"{C.BG_RED}{C.WHITE} ● OFFLINE {C.RESET}"


def _format_uptime(delta: datetime.timedelta) -> str:
    total = int(delta.total_seconds())
    d, r  = divmod(total, 86400)
    h, r  = divmod(r, 3600)
    m, s  = divmod(r, 60)
    parts = []
    if d: parts.append(f"{C.BOLD}{d}{C.RESET}d")
    if h: parts.append(f"{C.BOLD}{h}{C.RESET}h")
    if m: parts.append(f"{C.BOLD}{m}{C.RESET}m")
    parts.append(f"{C.BOLD}{s}{C.RESET}s")
    return " ".join(parts)


def _two_col(
    label1: str, val1: str,
    label2: str, val2: str,
    *,
    col_w: int = INNER // 2,
) -> str:
    """Zwei Schlüssel-Wert-Paare nebeneinander in einer Zeile."""
    left_label  = f"{C.DIM}{label1}{C.RESET}"
    right_label = f"{C.DIM}{label2}{C.RESET}"
    left_full   = f"{left_label} {C.BOLD}{C.BRIGHT_WHITE}{val1}{C.RESET}"
    right_full  = f"{right_label} {C.BOLD}{C.BRIGHT_WHITE}{val2}{C.RESET}"

    vis_left  = _visible_len(left_full)
    vis_right = _visible_len(right_full)
    pad_mid   = INNER - vis_left - vis_right
    if pad_mid < 2:
        pad_mid = 2
    return f"{_PIPE} {left_full}{' ' * pad_mid}{right_full} {_PIPE}"


# ── Cog ───────────────────────────────────────────────────────────────────────

class Dashboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        self._process = psutil.Process(os.getpid()) if psutil else None
        self._tick    = 0   # Für Spinner-Animation

    async def cog_load(self) -> None:
        self.refresh_dashboard.start()

    def cog_unload(self) -> None:
        self.refresh_dashboard.cancel()

    # ── Haupt-Render-Task ──────────────────────────────────────────────────────

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def refresh_dashboard(self) -> None:
        bot    = self.bot
        now    = datetime.datetime.now(datetime.timezone.utc)
        online = bot.is_ready() and not bot.is_closed()

        # ── Metriken sammeln ──────────────────────────────────────────────────
        ping_ms     = round(bot.latency * 1000) if math.isfinite(bot.latency) else 0
        guild_count = len(bot.guilds)
        user_count  = sum(g.member_count or 0 for g in bot.guilds)
        chan_count   = sum(len(g.channels) for g in bot.guilds)
        cmd_count   = len(bot.tree.get_commands())
        cog_count   = len(bot.cogs)

        if self._process is not None:
            ram_mb      = self._process.memory_info().rss / (1024 * 1024)
            cpu_percent = self._process.cpu_percent(interval=None)
        else:
            ram_mb, cpu_percent = 0.0, 0.0

        try:
            open_tickets = await _open_tickets_store.read()
            ticket_count = len(open_tickets)
        except Exception:
            ticket_count = 0

        try:
            rating_stats = await compute_rating_stats()
            avg_rating   = rating_stats.get("average", 0.0)
            rating_count = rating_stats.get("count", 0)
        except Exception:
            avg_rating, rating_count = 0.0, 0

        try:
            maintenance = (await get_maintenance_state())["maintenance"]
        except Exception:
            maintenance = False

        launch_time = getattr(bot, "launch_time", None)
        uptime_str  = _format_uptime(now - launch_time) if launch_time else "—"

        loaded_cogs = sorted(ext.split(".")[-1] for ext in bot.extensions.keys())
        py_ver      = platform.python_version()
        os_name     = f"{platform.system()} {platform.release()}"

        # Spinner animieren
        self._tick = (self._tick + 1) % len(_SPINNER)
        spinner    = _SPINNER[self._tick]

        # ── Box zusammenbauen ─────────────────────────────────────────────────
        now_str = now.strftime("%Y-%m-%d  %H:%M:%S UTC")

        lines: list[str] = [""]

        # ── Header ────────────────────────────────────────────────────────────
        lines.append(_TOP)

        # Titel-Zeile
        title = (
            f"{C.BOLD}{C.BRIGHT_CYAN}⚡  AVOKE  {C.RESET}"
            f"{C.DIM}│{C.RESET}"
            f"{C.BOLD}{C.WHITE}  System Dashboard  {C.RESET}"
            f"{C.DIM}│{C.RESET}"
            f"  {C.DIM}{now_str}{C.RESET}"
            f"  {C.BRIGHT_YELLOW}{spinner}{C.RESET}"
        )
        lines.append(_center_row(title))
        lines.append(_DIV_H)

        # ── Sektion: Status ────────────────────────────────────────────────────
        lines.append(_section_header("◈", "Status & Uptime", C.BRIGHT_CYAN))
        lines.append(_DIV_S)

        lines.append(_row(
            f"  {C.DIM}Status{C.RESET}",
            _status_dot(online),
        ))
        lines.append(_row(
            f"  {C.DIM}Uptime{C.RESET}",
            uptime_str,
        ))
        lines.append(_row(
            f"  {C.DIM}Ping  {C.RESET}",
            _ping_indicator(ping_ms),
        ))
        maint_str = (
            f"{C.BG_RED}{C.WHITE} ⚠ WARTUNG AKTIV {C.RESET}" if maintenance
            else f"{C.DIM}○ Inaktiv{C.RESET}"
        )
        lines.append(_row(f"  {C.DIM}Wartung{C.RESET}", maint_str))
        lines.append(_DIV_S)

        # ── Sektion: Ressourcen ────────────────────────────────────────────────
        lines.append(_section_header("◈", "Ressourcen", C.BRIGHT_MAGENTA))
        lines.append(_DIV_S)

        cpu_bar = _progress_bar(cpu_percent, 100.0, 18)
        ram_bar = _progress_bar(ram_mb, 512.0,  18, warn=300.0, crit=450.0)

        lines.append(_row(f"  {C.DIM}CPU  {C.RESET}", cpu_bar))
        lines.append(_row(f"  {C.DIM}RAM  {C.RESET}", ram_bar))
        lines.append(_row(
            f"  {C.DIM}Python{C.RESET}",
            f"{C.BRIGHT_GREEN}{py_ver}{C.RESET}",
        ))
        lines.append(_row(
            f"  {C.DIM}discord.py{C.RESET}",
            f"{C.BRIGHT_BLUE}{DISCORD_VERSION}{C.RESET}",
        ))
        lines.append(_row(
            f"  {C.DIM}System{C.RESET}",
            f"{C.DIM}{os_name}{C.RESET}",
        ))
        lines.append(_DIV_S)

        # ── Sektion: Community ─────────────────────────────────────────────────
        lines.append(_section_header("◈", "Community", C.BRIGHT_YELLOW))
        lines.append(_DIV_S)

        lines.append(_two_col(
            "  Server  ", str(guild_count),
            "User  ",     str(user_count),
        ))
        lines.append(_two_col(
            "  Kanäle  ", str(chan_count),
            "Cogs  ",     str(cog_count),
        ))
        lines.append(_two_col(
            "  Commands", str(cmd_count),
            "Tickets ",   str(ticket_count),
        ))

        # Ø-Bewertung mit Sternen
        stars      = "★" * round(avg_rating) + "☆" * (5 - round(avg_rating))
        rating_col = (
            f"{C.BRIGHT_YELLOW}{stars}{C.RESET} "
            f"{C.BOLD}{avg_rating:.2f}{C.RESET}"
            f"{C.DIM}/5  ({rating_count}x){C.RESET}"
        )
        lines.append(_row(f"  {C.DIM}Ø Bewertung{C.RESET}", rating_col))
        lines.append(_DIV_S)

        # ── Sektion: Cogs ──────────────────────────────────────────────────────
        lines.append(_section_header("◈", f"Geladene Cogs  ({cog_count})", C.BRIGHT_GREEN))
        lines.append(_DIV_S)

        # Cogs in Gruppen à 4 pro Zeile aufteilen
        row_size = 4
        for i in range(0, len(loaded_cogs), row_size):
            chunk = loaded_cogs[i : i + row_size]
            tags  = "  ".join(
                f"{C.BG_DARK}{C.BRIGHT_CYAN} {name} {C.RESET}"
                for name in chunk
            )
            lines.append(_row(f"  {tags}"))
        lines.append(_DIV_S)

        # ── Sektion: Letzte Fehler ─────────────────────────────────────────────
        lines.append(_section_header("◈", "Letzte Fehler", C.BRIGHT_RED))
        lines.append(_DIV_S)

        err_list = list(recent_errors)
        if not err_list:
            lines.append(_row(
                f"  {C.BRIGHT_GREEN}✔  Keine Fehler bisher — alles läuft stabil.{C.RESET}",
            ))
        else:
            for err in err_list[-4:]:
                # ANSI-sauber kürzen auf sichtbare Zeichenbreite
                short = err[-(INNER - 4):] if len(err) > INNER - 4 else err
                lines.append(_row(
                    f"  {C.BRIGHT_RED}✖{C.RESET}  {C.DIM}{short}{C.RESET}",
                ))

        lines.append(_BOT)
        lines.append("")

        # ── Ausgabe ────────────────────────────────────────────────────────────
        os.system("cls" if platform.system() == "Windows" else "clear")
        print("\n".join(lines))

    @refresh_dashboard.before_loop
    async def before_refresh(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dashboard(bot))
