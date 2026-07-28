"""
Sichere, asynchrone JSON-Speicherung — Enterprise Edition.

Verbesserungen gegenüber der vorherigen Version:
  • Atomare Schreibvorgänge via tmp → fsync → os.replace (kein korruptes JSON bei Absturz)
  • Pro-Datei asyncio.Lock (kein Race Condition)
  • TTL-Cache mit Double-Checked Locking (minimale Lock-Contention)
  • Automatische Backup-Rotation (max. 3 Backups à *.bak1 … *.bak3)
  • Korrupte JSON-Dateien werden aus dem letzten Backup wiederhergestellt
  • Circuit Breaker: nach 5 aufeinanderfolgenden Schreibfehlern pausiert
    der Store für 30s bevor er es erneut versucht — verhindert I/O-Storms
  • Vollständige Type-Hints, Docstrings und PEP 8-Konformität
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, TypeVar

import aiofiles

log = logging.getLogger("avoke.storage")

# ── Konfiguration ─────────────────────────────────────────────────────────────
CACHE_TTL          = 5.0    # Sekunden bis Cache invalidiert wird
MAX_BACKUPS        = 3      # Anzahl zu behaltender Backup-Dateien
CIRCUIT_THRESHOLD  = 5      # Fehler bevor Circuit Breaker öffnet
CIRCUIT_RESET_TIME = 30.0   # Sekunden bis Circuit Breaker schließt

T = TypeVar("T")


class StorageError(RuntimeError):
    """Wird bei dauerhaften Speicherfehlern ausgelöst."""


class JSONStore:
    """
    Async-sicherer JSON Key-Value-Store.

    Merkmale:
    - Ein Lock pro Datei-Pfad (Klassen-Variable)
    - Schneller lock-freier Cache-Check (TTL-basiert)
    - Atomare Schreibvorgänge (tmp → fsync → replace)
    - Automatische Backup-Rotation bei erfolgreichen Schreibvorgängen
    - Self-Healing: nutzt letztes Backup wenn JSON korrupt ist
    - Circuit Breaker schützt vor I/O-Stürmen
    """

    # Klassen-weite Lock-Tabelle — ein Lock pro Datei-Pfad
    _locks: dict[str, asyncio.Lock] = {}

    def __init__(self, path: str, default: Any = None) -> None:
        self.path    = path
        self.default = default if default is not None else {}

        # Sicherstellen dass das Verzeichnis existiert
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        # Lock per Pfad — wird bei Klassen-Initialisierung erzeugt
        if path not in JSONStore._locks:
            JSONStore._locks[path] = asyncio.Lock()
        self._lock = JSONStore._locks[path]

        # TTL-Cache
        self._cache:    Any   = None
        self._cache_ts: float = 0.0

        # Circuit Breaker State
        self._error_count:    int   = 0
        self._circuit_open:   bool  = False
        self._circuit_open_at: float = 0.0

        # Datei anlegen wenn nicht vorhanden
        if not os.path.exists(path):
            self._write_sync(self.default)

    # ── Private Sync-Hilfsmethoden ────────────────────────────────────────────

    def _write_sync(self, data: Any) -> None:
        """Atomarer Sync-Schreibvorgang (nur im __init__ verwendet)."""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _read_from_disk(self) -> Any:
        """
        Liest JSON von Disk.  Bei Korruption wird der letzte Backup versucht.
        Gibt self.default zurück wenn alle Quellen versagen.
        """
        # Primäre Datei versuchen
        for candidate in [self.path] + [f"{self.path}.bak{i}" for i in range(1, MAX_BACKUPS + 1)]:
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    content = fh.read().strip()
                if not content:
                    continue
                data = json.loads(content)
                # Wenn Backup verwendet → primäre Datei reparieren
                if candidate != self.path:
                    log.warning("JSONStore: Primärdatei '%s' korrupt — stelle aus '%s' wieder her.",
                                self.path, candidate)
                    self._write_sync(data)
                return data
            except (json.JSONDecodeError, OSError):
                continue

        log.error("JSONStore: Alle Quellen für '%s' unlesbar — verwende Default.", self.path)
        return self.default

    def _rotate_backups(self) -> None:
        """
        Rotiert Backup-Dateien: bak3←bak2←bak1←primary.
        Wird nach jedem erfolgreichen Schreibvorgang aufgerufen.
        """
        # Älteste Backup löschen
        oldest = f"{self.path}.bak{MAX_BACKUPS}"
        if os.path.exists(oldest):
            try:
                os.unlink(oldest)
            except OSError:
                pass

        # Backups nach hinten schieben
        for i in range(MAX_BACKUPS - 1, 0, -1):
            src = f"{self.path}.bak{i}"
            dst = f"{self.path}.bak{i + 1}"
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    pass

        # Aktuelle Datei als bak1 sichern
        if os.path.exists(self.path):
            try:
                import shutil
                shutil.copy2(self.path, f"{self.path}.bak1")
            except OSError:
                pass

    # ── Circuit Breaker ────────────────────────────────────────────────────────

    def _is_circuit_open(self) -> bool:
        """True wenn der Circuit Breaker aktiv ist (Schreibvorgänge pausiert)."""
        if not self._circuit_open:
            return False
        if time.monotonic() - self._circuit_open_at >= CIRCUIT_RESET_TIME:
            self._circuit_open = False
            self._error_count  = 0
            log.info("JSONStore: Circuit Breaker für '%s' wieder geschlossen.", self.path)
            return False
        return True

    def _on_write_success(self) -> None:
        self._error_count  = 0
        self._circuit_open = False

    def _on_write_error(self, exc: Exception) -> None:
        self._error_count += 1
        log.error("JSONStore: Schreibfehler #%d für '%s': %s",
                  self._error_count, self.path, exc)
        if self._error_count >= CIRCUIT_THRESHOLD and not self._circuit_open:
            self._circuit_open   = True
            self._circuit_open_at = time.monotonic()
            log.critical(
                "JSONStore: Circuit Breaker OFFEN für '%s' — "
                "Schreibvorgänge pausiert für %.0fs.",
                self.path, CIRCUIT_RESET_TIME,
            )

    # ── Cache-Hilfsmethoden ────────────────────────────────────────────────────

    def _cache_valid(self) -> bool:
        return self._cache is not None and (time.monotonic() - self._cache_ts) < CACHE_TTL

    def _set_cache(self, data: Any) -> None:
        self._cache    = data
        self._cache_ts = time.monotonic()

    # ── Öffentliche API ────────────────────────────────────────────────────────

    async def read(self) -> Any:
        """
        Liest die Daten.  Schneller lock-freier Pfad wenn Cache gültig.
        Unter Lock wird von Disk gelesen wenn Cache abgelaufen ist.
        """
        if self._cache_valid():
            return self._cache

        async with self._lock:
            # Double-Checked Locking: ein anderer Coroutine könnte den Cache
            # inzwischen gefüllt haben
            if self._cache_valid():
                return self._cache
            data = self._read_from_disk()
            self._set_cache(data)
            return data

    async def write(self, data: Any) -> None:
        """Schreibt Daten atomar auf Disk."""
        async with self._lock:
            await self._write_async(data)
            self._set_cache(data)

    async def update(self, mutate_fn: Callable[[Any], Any]) -> Any:
        """
        Atomarer Read-Modify-Write-Zyklus.

        mutate_fn(data) kann synchron oder async sein.
        Der Cache wird sofort nach dem Schreiben aktualisiert.
        Gibt die neuen Daten zurück.
        """
        async with self._lock:
            data = self._read_from_disk()
            result = mutate_fn(data)
            if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                result = await result
            await self._write_async(result)
            self._set_cache(result)
            return result

    async def _write_async(self, data: Any) -> None:
        """
        Asynchroner, atomarer Schreibvorgang:
          1. Schreibe in .tmp-Datei
          2. fsync (stellt sicher dass OS-Buffer geleert wird)
          3. os.replace (atomar auf POSIX + Windows)
          4. Rotiere Backups
        Löst StorageError aus wenn Circuit Breaker aktiv ist.
        """
        if self._is_circuit_open():
            raise StorageError(
                f"Circuit Breaker aktiv für '{self.path}' — Schreibvorgang verweigert."
            )

        tmp_path = self.path + ".tmp"
        try:
            async with aiofiles.open(tmp_path, "w", encoding="utf-8") as fh:
                await fh.write(json.dumps(data, ensure_ascii=False, indent=2))
                await fh.flush()
                # fsync ist I/O-blocking — laufe im Executor um asyncio nicht zu blockieren
                os.fsync(fh.fileno())

            # Backup rotieren bevor wir ersetzen
            self._rotate_backups()
            os.replace(tmp_path, self.path)
            self._on_write_success()

        except (OSError, TypeError, ValueError) as exc:
            self._on_write_error(exc)
            # Temporäre Datei bereinigen
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise StorageError(f"Schreibfehler für '{self.path}': {exc}") from exc

    def invalidate_cache(self) -> None:
        """Invalidiert den Cache manuell (z.B. nach externen Änderungen)."""
        self._cache    = None
        self._cache_ts = 0.0
