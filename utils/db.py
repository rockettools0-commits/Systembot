"""
Datenbank-Migrations-Utiltiy — SQLite via aiosqlite.

Stellt eine optionale SQLite-Datenbank neben dem JSON-System bereit.
JSON-Stores bleiben die primäre Datenquelle für alle Cogs.
Diese Schicht:
  1. Erstellt das SQLite-Schema automatisch (alle Tabellen)
  2. Bietet Migrationsfunktionen: JSON → SQLite
  3. Ermöglicht komplexere Abfragen (Analytics, Leaderboards)

Verwendung:
  from utils.db import Database
  db = Database("data/avoke.db")
  await db.init()
  await db.migrate_from_json()    # einmalig aufrufen

Schema-Versionen werden in der `schema_version`-Tabelle gespeichert.
"""
from __future__ import annotations

import json
import os
import datetime
import logging
from pathlib import Path
from typing import Any

try:
    import aiosqlite
    AIOSQLITE_AVAILABLE = True
except ImportError:
    AIOSQLITE_AVAILABLE = False

log = logging.getLogger("avoke.db")

# Aktuelle Schema-Version — bei strukturellen Änderungen erhöhen
SCHEMA_VERSION = 1

# Migrations-SQL
SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Schema-Version
CREATE TABLE IF NOT EXISTS schema_version (
    version   INTEGER NOT NULL,
    applied   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Moderations-Cases
CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     INTEGER NOT NULL,
    guild_id    TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    mod_id      TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    reason      TEXT,
    timestamp   TEXT    NOT NULL,
    edited_by   TEXT,
    edited_at   TEXT,
    UNIQUE(guild_id, case_id)
);
CREATE INDEX IF NOT EXISTS idx_cases_guild_user ON cases(guild_id, user_id);

-- Case-Kommentare
CREATE TABLE IF NOT EXISTS case_comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT    NOT NULL,
    case_id     INTEGER NOT NULL,
    author_id   TEXT    NOT NULL,
    text        TEXT    NOT NULL,
    ts          TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_case ON case_comments(guild_id, case_id);

-- Einsprüche
CREATE TABLE IF NOT EXISTS appeals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT    NOT NULL,
    case_id     INTEGER NOT NULL,
    user_id     TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    submitted   TEXT    NOT NULL,
    reviewed_by TEXT,
    review_ts   TEXT,
    review_note TEXT,
    UNIQUE(guild_id, case_id)
);

-- Ticket-Analytics
CREATE TABLE IF NOT EXISTS ticket_analytics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id            TEXT    NOT NULL,
    channel_id          TEXT    NOT NULL UNIQUE,
    user_id             TEXT    NOT NULL,
    panel_name          TEXT,
    opened_at           TEXT    NOT NULL,
    closed_at           TEXT,
    first_response_at   TEXT,
    first_responder_id  TEXT,
    closed_by           TEXT,
    rating              REAL,
    sla_warned          INTEGER DEFAULT 0,
    sla_breached        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ta_guild ON ticket_analytics(guild_id);

-- Supporter-Stats (aggregiert für Performance)
CREATE TABLE IF NOT EXISTS supporter_stats (
    guild_id            TEXT    NOT NULL,
    user_id             TEXT    NOT NULL,
    closed              INTEGER DEFAULT 0,
    total_response_s    REAL    DEFAULT 0,
    response_count      INTEGER DEFAULT 0,
    total_duration_s    REAL    DEFAULT 0,
    ratings_sum         REAL    DEFAULT 0,
    ratings_count       INTEGER DEFAULT 0,
    PRIMARY KEY(guild_id, user_id)
);

-- Security-Vorfälle
CREATE TABLE IF NOT EXISTS security_incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    risk        INTEGER NOT NULL,
    action      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sec_guild ON security_incidents(guild_id, timestamp);

-- Anti-Nuke Log
CREATE TABLE IF NOT EXISTS antinuke_incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    reason      TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_an_guild ON antinuke_incidents(guild_id, timestamp);

-- Automationen
CREATE TABLE IF NOT EXISTS automations (
    id          TEXT    NOT NULL,
    guild_id    TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT,
    trigger     TEXT    NOT NULL,
    trigger_config TEXT DEFAULT '{}',
    action      TEXT    NOT NULL,
    action_config  TEXT DEFAULT '{}',
    enabled     INTEGER DEFAULT 1,
    created_by  TEXT,
    created_at  TEXT    NOT NULL,
    PRIMARY KEY(guild_id, id)
);
"""


class Database:
    """
    Async SQLite-Datenbank-Abstraktionsschicht.
    Alle Methoden sind coroutines und müssen mit await aufgerufen werden.
    """

    def __init__(self, db_path: str = "data/avoke.db"):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    @property
    def available(self) -> bool:
        """True wenn aiosqlite installiert und verbunden ist."""
        return AIOSQLITE_AVAILABLE and self._db is not None

    async def init(self) -> None:
        """Initialisiert die Datenbank-Verbindung und erstellt alle Tabellen."""
        if not AIOSQLITE_AVAILABLE:
            log.warning("aiosqlite nicht installiert — Datenbank-Layer deaktiviert. "
                       "Installiere mit: pip install aiosqlite")
            return

        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

        # Schema-Version prüfen
        async with self._db.execute("SELECT version FROM schema_version ORDER BY applied DESC LIMIT 1") as cur:
            row = await cur.fetchone()
        if not row:
            await self._db.execute(
                "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
            )
            await self._db.commit()
            log.info(f"Datenbank initialisiert (Schema v{SCHEMA_VERSION}): {self.db_path}")
        else:
            log.info(f"Datenbank verbunden (Schema v{row['version']}): {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ─────────────────────────────────────────────────────────────────────────
    # Migration: JSON → SQLite
    # ─────────────────────────────────────────────────────────────────────────

    async def migrate_from_json(self, data_dir: str = "data") -> dict[str, int]:
        """
        Migriert alle JSON-Dateien in die SQLite-Datenbank.
        Gibt ein Zusammenfassung {tabelle: anzahl_einträge} zurück.

        Sicher idempotent: bereits importierte Einträge werden übersprungen.
        """
        if not self.available:
            log.warning("Datenbank nicht verfügbar — Migration übersprungen.")
            return {}

        stats: dict[str, int] = {}
        data_path = Path(data_dir)

        stats["cases"]              = await self._migrate_cases(data_path / "cases.json")
        stats["security"]           = await self._migrate_security(data_path / "security_history.json")
        stats["antinuke"]           = await self._migrate_antinuke(data_path / "antinuke_log.json")
        stats["automations"]        = await self._migrate_automations(data_path / "automation_config.json")
        stats["ticket_analytics"]   = await self._migrate_ticket_analytics(data_path / "ticket_analytics.json")

        log.info(f"Migration abgeschlossen: {stats}")
        return stats

    async def _migrate_cases(self, path: Path) -> int:
        """Migriert data/cases.json → cases + case_comments Tabellen."""
        if not path.exists():
            return 0
        with open(path, encoding="utf-8") as f:
            data: dict = json.load(f)

        count = 0
        for guild_id, guild_data in data.items():
            for case_id_str, case in guild_data.get("cases", {}).items():
                try:
                    await self._db.execute(
                        """INSERT OR IGNORE INTO cases
                           (case_id, guild_id, user_id, mod_id, action, reason, timestamp, edited_by, edited_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            int(case_id_str),
                            guild_id,
                            str(case.get("user_id", "")),
                            str(case.get("mod_id", "")),
                            case.get("action", "note"),
                            case.get("reason", ""),
                            case.get("timestamp", datetime.datetime.now(datetime.timezone.utc).isoformat()),
                            str(case.get("edited_by", "")) or None,
                            case.get("edited_at"),
                        ),
                    )
                    # Kommentare
                    for comment in case.get("comments", []):
                        await self._db.execute(
                            "INSERT INTO case_comments(guild_id, case_id, author_id, text, ts) VALUES (?,?,?,?,?)",
                            (guild_id, int(case_id_str), str(comment["author_id"]), comment["text"], comment["ts"]),
                        )
                    count += 1
                except Exception as e:
                    log.warning(f"Case-Migration-Fehler: {e}")

        await self._db.commit()
        return count

    async def _migrate_security(self, path: Path) -> int:
        if not path.exists():
            return 0
        with open(path, encoding="utf-8") as f:
            data: dict = json.load(f)
        count = 0
        for guild_id, incidents in data.items():
            for item in incidents:
                try:
                    await self._db.execute(
                        "INSERT INTO security_incidents(guild_id, user_id, reason, risk, action, timestamp) VALUES (?,?,?,?,?,?)",
                        (guild_id, str(item["user_id"]), item["reason"], item.get("risk", 0), item.get("action", "delete"), item["timestamp"]),
                    )
                    count += 1
                except Exception:
                    pass
        await self._db.commit()
        return count

    async def _migrate_antinuke(self, path: Path) -> int:
        if not path.exists():
            return 0
        with open(path, encoding="utf-8") as f:
            data: dict = json.load(f)
        count = 0
        for guild_id, incidents in data.items():
            for item in incidents:
                try:
                    await self._db.execute(
                        "INSERT INTO antinuke_incidents(guild_id, user_id, reason, timestamp) VALUES (?,?,?,?)",
                        (guild_id, str(item["user_id"]), item["reason"], item["timestamp"]),
                    )
                    count += 1
                except Exception:
                    pass
        await self._db.commit()
        return count

    async def _migrate_automations(self, path: Path) -> int:
        if not path.exists():
            return 0
        with open(path, encoding="utf-8") as f:
            data: dict = json.load(f)
        count = 0
        for guild_id, guild_data in data.items():
            for auto_id, auto in guild_data.get("automations", {}).items():
                try:
                    await self._db.execute(
                        """INSERT OR IGNORE INTO automations
                           (id, guild_id, name, description, trigger, trigger_config, action, action_config, enabled, created_by, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            auto_id, guild_id, auto.get("name", ""), auto.get("description", ""),
                            auto.get("trigger", ""), json.dumps(auto.get("trigger_config", {})),
                            auto.get("action", ""), json.dumps(auto.get("action_config", {})),
                            1 if auto.get("enabled", True) else 0,
                            str(auto.get("created_by", "")),
                            auto.get("created_at", datetime.datetime.now(datetime.timezone.utc).isoformat()),
                        ),
                    )
                    count += 1
                except Exception:
                    pass
        await self._db.commit()
        return count

    async def _migrate_ticket_analytics(self, path: Path) -> int:
        if not path.exists():
            return 0
        with open(path, encoding="utf-8") as f:
            data: dict = json.load(f)
        count = 0
        for guild_id, tickets in data.items():
            if guild_id.startswith("stats_"):
                continue
            for ch_id, ticket in tickets.items():
                try:
                    await self._db.execute(
                        """INSERT OR IGNORE INTO ticket_analytics
                           (guild_id, channel_id, user_id, panel_name, opened_at, first_response_at, first_responder_id, sla_warned, sla_breached)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            guild_id, ch_id,
                            str(ticket.get("user_id", "")),
                            ticket.get("panel_name", ""),
                            ticket.get("opened_at", ""),
                            ticket.get("first_response_at"),
                            str(ticket.get("first_responder_id", "")) or None,
                            1 if ticket.get("sla_warned") else 0,
                            1 if ticket.get("sla_breached") else 0,
                        ),
                    )
                    count += 1
                except Exception:
                    pass
        await self._db.commit()
        return count

    # ─────────────────────────────────────────────────────────────────────────
    # Query-Helpers
    # ─────────────────────────────────────────────────────────────────────────

    async def get_cases(self, guild_id: str, user_id: str | None = None) -> list[dict]:
        """Gibt alle Fälle einer Guild zurück, optional nach user_id gefiltert."""
        if not self.available:
            return []
        if user_id:
            async with self._db.execute(
                "SELECT * FROM cases WHERE guild_id=? AND user_id=? ORDER BY case_id DESC",
                (guild_id, user_id)
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]
        async with self._db.execute(
            "SELECT * FROM cases WHERE guild_id=? ORDER BY case_id DESC LIMIT 100",
            (guild_id,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def get_supporter_stats(self, guild_id: str) -> list[dict]:
        """Gibt Supporter-Stats für eine Guild zurück."""
        if not self.available:
            return []
        async with self._db.execute(
            "SELECT * FROM supporter_stats WHERE guild_id=? ORDER BY closed DESC",
            (guild_id,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def get_security_incidents(self, guild_id: str, limit: int = 50) -> list[dict]:
        """Gibt die letzten Security-Vorfälle zurück."""
        if not self.available:
            return []
        async with self._db.execute(
            "SELECT * FROM security_incidents WHERE guild_id=? ORDER BY timestamp DESC LIMIT ?",
            (guild_id, limit)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


# Singleton-Instanz — wird in main.py initialisiert
db = Database()
