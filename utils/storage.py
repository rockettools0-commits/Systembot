"""
Sichere, asynchrone JSON-Speicherung mit asyncio.Lock() und TTL-Cache.
- Ein Lock pro Datei serialisiert gleichzeitige Schreibzugriffe.
- Schneller lock-freier Cache-Check: read() belegt den Lock nur wenn der Cache
  abgelaufen ist — spart erheblich Contention bei häufigen Event-Listenern.
- update() invalidiert den Cache sofort, damit nachfolgende reads() fresh sind.
"""

import json
import os
import time
import asyncio
import inspect
from typing import Any

import aiofiles

CACHE_TTL = 5.0  # Sekunden


class JSONStore:
    """Async-sicherer JSON Key-Value-Store mit TTL-Cache, ein Lock pro Datei."""

    _locks: dict[str, asyncio.Lock] = {}

    def __init__(self, path: str, default: Any = None):
        self.path    = path
        self.default = default if default is not None else {}
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None

        if path not in JSONStore._locks:
            JSONStore._locks[path] = asyncio.Lock()
        self._lock = JSONStore._locks[path]

        # TTL-Cache
        self._cache: Any      = None
        self._cache_ts: float = 0.0

        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.default, f, ensure_ascii=False, indent=2)

    def _cache_valid(self) -> bool:
        return self._cache is not None and (time.monotonic() - self._cache_ts) < CACHE_TTL

    async def read(self) -> Any:
        # Schneller lock-freier Pfad: Cache noch gültig → sofort zurückgeben
        if self._cache_valid():
            return self._cache
        # Cache abgelaufen → Lock holen und von Disk lesen
        async with self._lock:
            # Nochmal prüfen: ein anderer Coroutine könnte den Cache inzwischen gefüllt haben
            if self._cache_valid():
                return self._cache
            data           = self._read_nolock()
            self._cache    = data
            self._cache_ts = time.monotonic()
            return data

    def _read_nolock(self) -> Any:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return self.default
                return json.loads(content)
        except (FileNotFoundError, json.JSONDecodeError):
            return self.default

    async def write(self, data: Any) -> None:
        async with self._lock:
            await self._write_nolock(data)
            self._cache    = data
            self._cache_ts = time.monotonic()

    async def _write_nolock(self, data: Any) -> None:
        tmp_path = self.path + ".tmp"
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(tmp_path, self.path)

    async def update(self, mutate_fn) -> Any:
        """
        Atomarer Read-Modify-Write-Zyklus.
        mutate_fn(data) kann die (ggf. veränderte) data direkt zurückgeben
        oder ein awaitbares Ergebnis liefern. Dadurch bleiben ältere Cogs mit
        ``async def mutate`` kompatibel.
        Cache wird nach dem Write sofort aktualisiert.
        """
        async with self._lock:
            data   = self._read_nolock()
            result = mutate_fn(data)
            if inspect.isawaitable(result):
                result = await result
            await self._write_nolock(result)
            self._cache    = result
            self._cache_ts = time.monotonic()
            return result
