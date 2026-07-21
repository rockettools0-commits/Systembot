"""
Einheitliches Embed-Theme für alle AVOKE-Cogs.
Dark-Mode Palette: tiefe Hintergründe, kontrastreiche Akzentfarben,
klare Typografie-Hierarchie in jedem Embed.
"""
# re-export get_footer_text so cogs can import it from here

import datetime
import discord

# ── Dark-Mode Farb-Palette ────────────────────────────────────────────────────
# Primär-Akzente
COLOR_PRIMARY   = discord.Color.from_rgb(88, 214, 141)    # Smaragd-Grün — Erfolg
COLOR_ERROR     = discord.Color.from_rgb(235, 77,  75)    # Kräftiges Rot — Fehler / Ban
COLOR_WARNING   = discord.Color.from_rgb(243, 156, 18)    # Bernstein — Warnung
COLOR_INFO      = discord.Color.from_rgb(84,  153, 199)   # Stahl-Blau — Info / Neutral
COLOR_GOLD      = discord.Color.from_rgb(212, 172, 13)    # Dunkles Gold — Economy
COLOR_DARK      = discord.Color.from_rgb(44,  47,  51)    # Dunkelgrau — Logs / System
COLOR_PURPLE    = discord.Color.from_rgb(130, 80,  255)   # Violett — Rang / Premium
COLOR_CYAN      = discord.Color.from_rgb(26,  188, 156)   # Türkis — Koordinaten / Info
COLOR_BLURPLE   = discord.Color.from_rgb(88,  101, 242)   # Discord-Blurple — Tickets

# Hintergrund-Referenz (für Pillow-Bilder)
BG_DARK  = (18, 18, 22, 255)
BG_CARD  = (26, 27, 35, 255)
ACCENT   = (88, 214, 141, 255)

FOOTER_TEXT  = "AVOKE │ System"
FOOTER_ICON  = None   # Icon-URL optional

# Trennlinie die als visuelles Spacing-Element dient
DIVIDER = "╌" * 32


def get_footer_text(guild_or_interaction=None) -> str:
    """Gibt '{Servername} • System' zurück, oder FOOTER_TEXT als Fallback."""
    if guild_or_interaction is None:
        return FOOTER_TEXT
    # discord.Interaction
    if hasattr(guild_or_interaction, "guild") and guild_or_interaction.guild is not None:
        return f"{guild_or_interaction.guild.name} • System"
    # discord.Guild
    if hasattr(guild_or_interaction, "name"):
        return f"{guild_or_interaction.name} • System"
    return FOOTER_TEXT


def _footer(embed: discord.Embed) -> discord.Embed:
    embed.set_footer(text=FOOTER_TEXT, icon_url=FOOTER_ICON)
    return embed


def base_embed(
    title: str = discord.utils.MISSING,
    description: str = discord.utils.MISSING,
    color: discord.Color = COLOR_PRIMARY,
    *,
    timestamp: bool = True,
) -> discord.Embed:
    """Erstellt ein Basis-Embed mit konsistentem Footer und optionalem Timestamp."""
    kwargs: dict = {"color": color}
    if title is not discord.utils.MISSING:
        kwargs["title"] = title
    if description is not discord.utils.MISSING:
        kwargs["description"] = description
    if timestamp:
        kwargs["timestamp"] = datetime.datetime.now(datetime.timezone.utc)

    embed = discord.Embed(**kwargs)
    return _footer(embed)


# ── Typed Embed Factories ─────────────────────────────────────────────────────

def success_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Grünes Erfolgs-Embed mit ✅-Stil."""
    return base_embed(title, description, COLOR_PRIMARY)


def error_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Rotes Fehler-Embed."""
    return base_embed(title, description, COLOR_ERROR)


def warning_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Bernstein-farbenes Warn-Embed."""
    return base_embed(title, description, COLOR_WARNING)


def info_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Blau-graues Info-Embed."""
    return base_embed(title, description, COLOR_INFO)


def gold_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Gold-farbenes Economy-Embed."""
    return base_embed(title, description, COLOR_GOLD)


def dark_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Dunkelgraues System/Log-Embed."""
    return base_embed(title, description, COLOR_DARK)


def purple_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Violettes Premium/Rang-Embed."""
    return base_embed(title, description, COLOR_PURPLE)


def cyan_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Türkis-farbenes Info-Embed."""
    return base_embed(title, description, COLOR_CYAN)


def blurple_embed(title: str, description: str = discord.utils.MISSING) -> discord.Embed:
    """Discord-Blurple Embed für Tickets."""
    return base_embed(title, description, COLOR_BLURPLE)


# ── Spezialisierte Embed-Builder ──────────────────────────────────────────────

def action_embed(
    action_type: str,
    target: str,
    moderator: str,
    reason: str,
    *,
    color: discord.Color = COLOR_ERROR,
    extra_fields: list[tuple[str, str, bool]] | None = None,
) -> discord.Embed:
    """
    Einheitliches Aktions-Embed für Moderation/Clan-Aktionen.
    action_type: z.B. 'Ban', 'Kick', 'Mute', 'Uprank'
    """
    embed = discord.Embed(color=color, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="👤 Ziel",       value=target,    inline=True)
    embed.add_field(name="🛡️ Moderator", value=moderator, inline=True)
    embed.add_field(name="\u200b",        value="\u200b",  inline=True)
    embed.add_field(name="📝 Grund",      value=reason or "Kein Grund angegeben", inline=False)
    if extra_fields:
        for name, value, inline in extra_fields:
            embed.add_field(name=name, value=value, inline=inline)
    return _footer(embed)


def stat_embed(
    title: str,
    lines: list[str],
    *,
    color: discord.Color = COLOR_INFO,
    thumbnail_url: str | None = None,
) -> discord.Embed:
    """Leaderboard/Stats-Embed mit nummerierter Liste."""
    embed = discord.Embed(
        title=title,
        description="\n".join(lines) if lines else "*Keine Daten vorhanden.*",
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    return _footer(embed)
