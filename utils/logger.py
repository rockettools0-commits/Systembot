"""
Zentrales Logging-Modul für AVOKE.

Erstellt separate RotatingFileHandler für jede Log-Kategorie:
  • logs/bot.log        — allgemeine Bot-Ereignisse (INFO+)
  • logs/error.log      — nur Fehler (ERROR+)
  • logs/command.log    — Slash- und Prefix-Command-Nutzung
  • logs/moderation.log — Moderationsaktionen (Ban, Kick, Mute, ...)
  • logs/system.log     — Systemaktionen (Restart, Shutdown, Backup, ...)
  • logs/startup.log    — Startup/Shutdown-Ereignisse

Jede Datei rotiert ab 10 MB, maximal 5 Backups.

Verwendung in anderen Modulen:
    from utils.logger import get_logger
    log = get_logger("moderation")   # → schreibt in logs/moderation.log + logs/bot.log
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

# Sicherstellen dass der logs/ Ordner existiert
os.makedirs("logs", exist_ok=True)

# ── Konfiguration ──────────────────────────────────────────────────────────────

MAX_BYTES   = 10 * 1024 * 1024   # 10 MB pro Datei
BACKUP_COUNT = 5
FMT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"

# ── Log-Kanal-Definitionen ─────────────────────────────────────────────────────
# name → (dateiname, min_level)
_CHANNELS: dict[str, tuple[str, int]] = {
    "bot":        ("logs/bot.log",        logging.INFO),
    "error":      ("logs/error.log",      logging.ERROR),
    "command":    ("logs/command.log",    logging.INFO),
    "moderation": ("logs/moderation.log", logging.INFO),
    "system":     ("logs/system.log",     logging.INFO),
    "startup":    ("logs/startup.log",    logging.INFO),
}

# Interne Handler-Registry damit handler nicht doppelt erzeugt werden
_handlers: dict[str, RotatingFileHandler] = {}
# Root-AVOKE-Logger (verankert alle benannten Logger)
_root_logger: logging.Logger | None = None


def _make_handler(name: str) -> RotatingFileHandler:
    """Erstellt oder gibt einen existierenden RotatingFileHandler zurück."""
    if name in _handlers:
        return _handlers[name]
    path, level = _CHANNELS[name]
    handler = RotatingFileHandler(
        path,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FMT, datefmt=DATE_FMT))
    _handlers[name] = handler
    return handler


def setup_logging(deque_handler: logging.Handler | None = None) -> logging.Logger:
    """
    Richtet das gesamte AVOKE-Logging ein.  Sollte einmal in main.py aufgerufen
    werden bevor der Bot startet.

    :param deque_handler: Optionaler zusätzlicher Handler (z.B. DequeErrorHandler)
                          für das CMD-Live-Dashboard.
    :returns: Root-Logger ``avoke``
    """
    global _root_logger
    if _root_logger is not None:
        return _root_logger

    formatter = logging.Formatter(FMT, datefmt=DATE_FMT)

    # ── Root-Logger "avoke" ───────────────────────────────────────────────────
    root = logging.getLogger("avoke")
    root.setLevel(logging.DEBUG)

    # Console-Handler (INFO+)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Haupt-Log-Datei (INFO+)
    root.addHandler(_make_handler("bot"))
    # Error-Log-Datei (ERROR+) auf alle avoke-Logger
    root.addHandler(_make_handler("error"))

    # Optionaler Deque-Handler für CMD-Dashboard
    if deque_handler is not None:
        deque_handler.setFormatter(formatter)
        root.addHandler(deque_handler)

    # discord.py Bibliotheks-Logger auf WARNING begrenzen → weniger Rauschen
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)

    _root_logger = root
    return root


def get_logger(channel: str) -> logging.Logger:
    """
    Gibt einen benannten Logger zurück der zusätzlich in die passende
    Kategorie-Datei schreibt.

    Beispiele:
        get_logger("command")    → avoke.command  (+ logs/command.log)
        get_logger("moderation") → avoke.moderation (+ logs/moderation.log)
        get_logger("system")     → avoke.system (+ logs/system.log)
        get_logger("startup")    → avoke.startup (+ logs/startup.log)

    Unbekannte Namen fallen auf den Root-Logger "avoke" zurück.
    """
    if _root_logger is None:
        # Fallback: logger ohne Datei-Handler (sollte nicht vorkommen)
        return logging.getLogger(f"avoke.{channel}")

    logger = logging.getLogger(f"avoke.{channel}")
    # Nur einmalig Handler hinzufügen
    if not logger.handlers and channel in _CHANNELS:
        logger.addHandler(_make_handler(channel))
        logger.propagate = True  # auch in bot.log + error.log + console
    return logger
