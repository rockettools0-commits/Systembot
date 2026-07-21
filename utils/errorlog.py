"""
Kleiner Ringpuffer für die zuletzt aufgetretenen Fehler/Warnungen.
Wird vom CMD-Live-Dashboard (cogs/dashboard.py) genutzt, um die letzten
Fehler direkt in der Konsole anzuzeigen, ohne die komplette bot.log lesen
zu müssen.

Funktionsweise:
- `DequeErrorHandler` ist ein zusätzlicher logging.Handler, der in main.py
  neben FileHandler und StreamHandler registriert wird.
- Jede Log-Meldung mit Level >= ERROR wird formatiert im Ringpuffer
  `recent_errors` gespeichert (maximal `MAX_ERRORS` Einträge).
"""

import logging
from collections import deque

MAX_ERRORS = 5

# Thread-/Coroutine-sicher genug für unseren Zweck (nur Anhängen + Iterieren)
recent_errors: deque[str] = deque(maxlen=MAX_ERRORS)


class DequeErrorHandler(logging.Handler):
    """Logging-Handler, der Fehler-Meldungen zusätzlich im Ringpuffer ablegt."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        recent_errors.append(message)
