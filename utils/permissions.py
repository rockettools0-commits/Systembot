"""
Flexibles Rollen-Berechtigungs-System — Enterprise Edition.

Architektur:
  • Prioritäten:  Server-Owner > Administrator > Moderations-Rolle > Normale Rolle
  • Whitelist:    Nutzer-IDs, die immer erlaubt sind (unabhängig von Rollen)
  • Blacklist:    Nutzer-IDs, die IMMER verweigert werden (höchste Priorität)
  • Gruppen:      Logische Bündelung von Commands (moderation, utility, promotion, clan)
  • Ausnahmen:    Pro-Server konfigurierbar — welche Rollen welche Gruppen nutzen dürfen
  • Cooldowns:    Optionale Rate-Limits pro Nutzer und Gruppe (in-memory)

Prüfungsreihenfolge (fail-fast):
  1. Blacklist          → sofort False
  2. Server-Owner       → sofort True
  3. Administrator-Recht → sofort True
  4. Globale Whitelist  → True
  5. Gruppen-Whitelist  → True wenn Rolle enthalten
  6. Kein Eintrag       → True (offen per Default, kann via require_config geändert werden)

Verwendung:
    from utils.permissions import PermissionSystem
    perm = PermissionSystem()

    # In einem Slash-Command:
    if not await perm.check(interaction, "moderation"):
        return await interaction.response.send_message(embed=no_perm_embed, ephemeral=True)
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Final

import discord

from utils.storage import JSONStore

# ── Konstanten ────────────────────────────────────────────────────────────────
PERM_CONFIG_PATH: Final[str] = "data/permissions_config.json"

# Gültige Gruppen-Namen mit kurzer Beschreibung
COMMAND_GROUPS: Final[dict[str, str]] = {
    "moderation": "Ban, Kick, Mute, Timeout, Warn, Lockdown, Automod",
    "utility":    "Clear, Slowmode, Nick, XP-Management",
    "promotion":  "Promote, Demote",
    "clan":       "Uprank, Derank, Clan-Kick",
    "tickets":    "Ticket-Management, Panels, Claim",
    "security":   "Security-Regeln, Trusted, Emergency",
    "economy":    "Coins, Marktplatz, Transfers",
    "admin":      "Setup, Konfiguration, Vollzugriff",
}

# Standardmäßig offen (True) oder geschlossen (False) wenn keine Konfiguration vorhanden
_DEFAULT_OPEN: Final[bool] = True

# ── In-Memory Cooldown-Tracker ─────────────────────────────────────────────────
# (guild_id, user_id, group) → letzte Nutzungszeit
_cooldown_tracker: dict[tuple[int, int, str], float] = defaultdict(float)


class PermissionDeniedReason:
    """Enum-ähnliche Klasse für Ablehnungsgründe (keine echte Enum für Erweiterbarkeit)."""
    BLACKLISTED   = "blacklisted"
    NOT_IN_GROUP  = "not_in_group"
    COOLDOWN      = "cooldown"
    NOT_IN_GUILD  = "not_in_guild"


class PermissionResult:
    """Ergebnisobjekt einer Berechtigungsprüfung."""

    __slots__ = ("allowed", "reason", "cooldown_remaining")

    def __init__(
        self,
        allowed:            bool,
        reason:             str  | None = None,
        cooldown_remaining: float       = 0.0,
    ) -> None:
        self.allowed            = allowed
        self.reason             = reason
        self.cooldown_remaining = cooldown_remaining

    def __bool__(self) -> bool:
        return self.allowed


class PermissionSystem:
    """
    Zentrales Berechtigungs-System.

    Eine Instanz pro Anwendung — als Singleton oder Cog-Attribut verwenden.
    """

    def __init__(self) -> None:
        self._store = JSONStore(PERM_CONFIG_PATH, {})

    # ── Interne Hilfsmethoden ─────────────────────────────────────────────────

    async def _guild_config(self, guild_id: int) -> dict:
        """Gibt die Konfiguration für eine Guild zurück — mit leeren Defaults."""
        data = await self._store.read()
        return data.get(str(guild_id), {})

    @staticmethod
    def _member_role_ids(member: discord.Member) -> frozenset[int]:
        return frozenset(r.id for r in member.roles)

    # ── Öffentliche API ────────────────────────────────────────────────────────

    async def check(
        self,
        interaction:    discord.Interaction,
        group:          str,
        *,
        cooldown_secs:  float = 0.0,
    ) -> PermissionResult:
        """
        Prüft ob der Nutzer die Gruppe verwenden darf.

        Args:
            interaction:   Die Discord-Interaction.
            group:         Gruppen-Name aus COMMAND_GROUPS.
            cooldown_secs: Optionaler Cooldown in Sekunden pro Nutzer.

        Returns:
            PermissionResult (bool-kompatibel).
        """
        member = interaction.user
        if not isinstance(member, discord.Member):
            return PermissionResult(False, PermissionDeniedReason.NOT_IN_GUILD)

        guild_id = interaction.guild_id
        cfg      = await self._guild_config(guild_id)

        # ── 1. Blacklist ──────────────────────────────────────────────────────
        blacklist: list[int] = cfg.get("blacklist", [])
        if member.id in blacklist:
            return PermissionResult(False, PermissionDeniedReason.BLACKLISTED)

        # ── 2. Server-Owner ───────────────────────────────────────────────────
        if member.id == interaction.guild.owner_id:
            return PermissionResult(True)

        # ── 3. Administrator ──────────────────────────────────────────────────
        if member.guild_permissions.administrator:
            return PermissionResult(True)

        # ── 4. Globale Whitelist (Nutzer-IDs) ─────────────────────────────────
        whitelist: list[int] = cfg.get("whitelist", [])
        if member.id in whitelist:
            return self._apply_cooldown(guild_id, member.id, group, cooldown_secs)

        # ── 5. Gruppen-Rollen-Whitelist ───────────────────────────────────────
        allowed_role_ids: list[int] = cfg.get("groups", {}).get(group, [])
        if allowed_role_ids:
            role_ids = self._member_role_ids(member)
            if role_ids & set(allowed_role_ids):
                return self._apply_cooldown(guild_id, member.id, group, cooldown_secs)
            return PermissionResult(False, PermissionDeniedReason.NOT_IN_GROUP)

        # ── 6. Kein Eintrag → Default ─────────────────────────────────────────
        if _DEFAULT_OPEN:
            return self._apply_cooldown(guild_id, member.id, group, cooldown_secs)
        return PermissionResult(False, PermissionDeniedReason.NOT_IN_GROUP)

    def _apply_cooldown(
        self,
        guild_id:      int,
        user_id:       int,
        group:         str,
        cooldown_secs: float,
    ) -> PermissionResult:
        """Prüft und setzt Cooldown. Gibt PermissionResult zurück."""
        if cooldown_secs <= 0.0:
            return PermissionResult(True)

        key      = (guild_id, user_id, group)
        last_use = _cooldown_tracker[key]
        now      = time.monotonic()
        remaining = cooldown_secs - (now - last_use)

        if remaining > 0.0:
            return PermissionResult(False, PermissionDeniedReason.COOLDOWN, remaining)

        _cooldown_tracker[key] = now
        return PermissionResult(True)

    # ── Konfiguration ──────────────────────────────────────────────────────────

    async def set_group_roles(
        self,
        guild_id: int,
        group:    str,
        role_ids: list[int],
    ) -> None:
        """Legt die erlaubten Rollen für eine Gruppe fest."""
        gid = str(guild_id)

        def mutate(data: dict) -> dict:
            data.setdefault(gid, {}).setdefault("groups", {})[group] = role_ids
            return data

        await self._store.update(mutate)

    async def add_to_whitelist(self, guild_id: int, user_id: int) -> bool:
        """Fügt einen Nutzer zur Whitelist hinzu. Gibt True bei Erfolg zurück."""
        result: dict = {}

        def mutate(data: dict) -> dict:
            wl = data.setdefault(str(guild_id), {}).setdefault("whitelist", [])
            if user_id not in wl:
                wl.append(user_id)
                result["ok"] = True
            return data

        await self._store.update(mutate)
        return result.get("ok", False)

    async def remove_from_whitelist(self, guild_id: int, user_id: int) -> bool:
        """Entfernt einen Nutzer aus der Whitelist."""
        result: dict = {}

        def mutate(data: dict) -> dict:
            wl = data.setdefault(str(guild_id), {}).setdefault("whitelist", [])
            if user_id in wl:
                wl.remove(user_id)
                result["ok"] = True
            return data

        await self._store.update(mutate)
        return result.get("ok", False)

    async def add_to_blacklist(self, guild_id: int, user_id: int) -> bool:
        """Fügt einen Nutzer zur Blacklist hinzu (wird immer verweigert)."""
        result: dict = {}

        def mutate(data: dict) -> dict:
            bl = data.setdefault(str(guild_id), {}).setdefault("blacklist", [])
            if user_id not in bl:
                bl.append(user_id)
                result["ok"] = True
            return data

        await self._store.update(mutate)
        return result.get("ok", False)

    async def remove_from_blacklist(self, guild_id: int, user_id: int) -> bool:
        """Entfernt einen Nutzer aus der Blacklist."""
        result: dict = {}

        def mutate(data: dict) -> dict:
            bl = data.setdefault(str(guild_id), {}).setdefault("blacklist", [])
            if user_id in bl:
                bl.remove(user_id)
                result["ok"] = True
            return data

        await self._store.update(mutate)
        return result.get("ok", False)

    async def get_config(self, guild_id: int) -> dict:
        """Gibt die vollständige Konfiguration einer Guild zurück."""
        return await self._guild_config(guild_id)

    async def reset_config(self, guild_id: int) -> None:
        """Setzt die Konfiguration einer Guild komplett zurück."""
        def mutate(data: dict) -> dict:
            data.pop(str(guild_id), None)
            return data
        await self._store.update(mutate)


# ── Modul-Level Singleton ──────────────────────────────────────────────────────
# Wird in allen Cogs importiert und genutzt
_system: PermissionSystem | None = None


def get_permission_system() -> PermissionSystem:
    """Gibt die Singleton-Instanz des PermissionSystem zurück."""
    global _system
    if _system is None:
        _system = PermissionSystem()
    return _system


# ── Backward-Compat Shim ──────────────────────────────────────────────────────
# Alte cogs nutzen check_role_permission(interaction, gruppe) → bool
# Diese Funktion bleibt erhalten um breaking changes zu vermeiden

async def check_role_permission(interaction: discord.Interaction, gruppe: str) -> bool:
    """
    Backward-compatible wrapper.
    Alte Cogs, die check_role_permission() importieren, funktionieren weiterhin.
    Neue Cogs sollten get_permission_system().check() direkt verwenden.
    """
    result = await get_permission_system().check(interaction, gruppe)
    return bool(result)


async def save_group_roles(guild_id: int, gruppe: str, role_ids: list[int]) -> None:
    """Backward-compatible wrapper für save_group_roles."""
    await get_permission_system().set_group_roles(guild_id, gruppe, role_ids)
