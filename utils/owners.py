"""
Zentrale Multi-Owner-Verwaltung für AVOKE.

Zwei Quellen werden zusammengeführt:

1. .env  (statisch, geladen beim Start):
       OWNER_ID=123
       OWNER_IDS=123,456,789

2. data/trusted_owners.json  (dynamisch, zur Laufzeit änderbar):
       Über /owneradmin trustuser / untrustuser

is_owner(user_id) → True wenn der User in EINER der beiden Quellen ist.
"""

from __future__ import annotations

import os

from utils.storage import JSONStore

# ── Statische Owner aus .env ───────────────────────────────────────────────────

def _load_env_owner_ids() -> frozenset[int]:
    ids: set[int] = set()
    for part in os.getenv("OWNER_IDS", "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    raw = os.getenv("OWNER_ID", "").strip()
    if raw.isdigit():
        ids.add(int(raw))
    return frozenset(ids)


# Einmalig beim Import geladen — unveränderlich
OWNER_IDS: frozenset[int] = _load_env_owner_ids()

# ── Dynamische Trusted-Owner (JSON, Laufzeit-änderbar) ────────────────────────

TRUSTED_PATH = "data/trusted_owners.json"
_trusted_store = JSONStore(TRUSTED_PATH, {"trusted": []})


async def get_trusted_ids() -> set[int]:
    """Gibt die aktuell gespeicherten Trusted-Owner-IDs zurück."""
    data = await _trusted_store.read()
    return set(data.get("trusted", []))


async def add_trusted(user_id: int) -> bool:
    """
    Fügt user_id zur Trusted-Liste hinzu.
    Gibt False zurück wenn die ID bereits drin war.
    """
    result = {"added": False}

    def mutate(data: dict) -> dict:
        lst = data.setdefault("trusted", [])
        if user_id not in lst:
            lst.append(user_id)
            result["added"] = True
        return data

    await _trusted_store.update(mutate)
    return result["added"]


async def remove_trusted(user_id: int) -> bool:
    """
    Entfernt user_id aus der Trusted-Liste.
    Gibt False zurück wenn die ID nicht vorhanden war.
    """
    result = {"removed": False}

    def mutate(data: dict) -> dict:
        lst = data.setdefault("trusted", [])
        if user_id in lst:
            lst.remove(user_id)
            result["removed"] = True
        return data

    await _trusted_store.update(mutate)
    return result["removed"]


# ── Kombinierte Prüfung ────────────────────────────────────────────────────────

async def is_owner_async(user_id: int) -> bool:
    """
    Async-Version: prüft .env-IDs UND Trusted-JSON.
    In Slash-Commands nutzen — korrekte vollständige Prüfung.
    """
    if user_id in OWNER_IDS:
        return True
    trusted = await get_trusted_ids()
    return user_id in trusted


def is_owner(user_id: int) -> bool:
    """
    Sync-Version: prüft NUR die statischen .env-IDs.
    Wird in owner_panel.py durch _owner_only_async() ersetzt.
    Bleibt für Präfix-Commands erhalten wo kein await möglich ist.
    """
    return user_id in OWNER_IDS
