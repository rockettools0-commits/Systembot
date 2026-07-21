"""
Graphical Stats: /rank generiert eine Rank-Card mit Avatar, Level und XP-Verwaltung.
Optimierungen:
- Shared aiohttp.ClientSession als Cog-Attribut (kein Session-Overhead pro Nachricht)
- XP Write-Behind-Cache: XP wird in-memory akkumuliert, alle 30 Sek. in die Datei geflusht
- Pillow-Generierung läuft in run_in_executor (non-blocking, event-loop frei)
"""

import asyncio
import io
import os
from functools import partial

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageOps

from utils.storage import JSONStore
from utils.theme import error_embed, success_embed, info_embed, COLOR_PURPLE, FOOTER_TEXT as _FT, get_footer_text
from utils.permissions import check_role_permission

XP_PATH          = "data/xp.json"
FONT_PATH_BOLD   = "fonts/Roboto-Bold.ttf"
FONT_PATH_REGULAR= "fonts/Roboto-Regular.ttf"


def default_xp():
    return {}


def xp_for_level(level: int) -> int:
    return 5 * (level ** 2) + 50 * level + 100


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        return ImageFont.load_default()


# Rank-Card Farben (Dark-Mode)
_RC_BG       = (14, 15, 20, 255)
_RC_CARD     = (22, 23, 30, 255)
_RC_ACCENT   = (88, 214, 141, 255)
_RC_ACCENT2  = (130, 80, 255, 255)
_RC_TEXT     = (240, 241, 246, 255)
_RC_MUTED    = (110, 112, 130, 255)
_RC_BAR_BG   = (40, 42, 55, 255)


def _sync_generate_rank_card(
    display_name: str, username: str, avatar_bytes: bytes, level: int, xp: int
) -> bytes:
    """Synchrone Pillow-Arbeit — Dark-Mode Design."""
    W, H = 980, 300
    card = Image.new("RGBA", (W, H), _RC_BG)
    draw = ImageDraw.Draw(card)

    # Hintergrund-Gradient (links nach rechts)
    for x in range(W):
        t = x / W
        r = int(14 + t * 18)
        g = int(15 + t * 10)
        b = int(20 + t * 35)
        draw.line([(x, 0), (x, H)], fill=(r, g, b, 255))

    # Dekorativer Accent-Balken links
    draw.rectangle([0, 0, 6, H], fill=_RC_ACCENT)

    # Card-Hintergrund hinter dem Textbereich
    card_x = 270
    draw.rounded_rectangle([card_x - 12, 16, W - 16, H - 16], radius=18, fill=_RC_CARD)

    # Avatar
    av = 200
    try:
        av_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
        av_img = ImageOps.fit(av_img, (av, av))
        m = Image.new("L", (av, av), 0)
        ImageDraw.Draw(m).ellipse((0, 0, av, av), fill=255)
        av_img.putalpha(m)
        ax = 38
        ay = (H - av) // 2
        card.paste(av_img, (ax, ay), av_img)
        # Doppelter Ring: Accent + dunkel
        draw.ellipse([ax-5, ay-5, ax+av+5, ay+av+5],   outline=_RC_ACCENT,  width=4)
        draw.ellipse([ax-10, ay-10, ax+av+10, ay+av+10], outline=(30,32,42,255), width=3)
    except Exception:
        pass

    font_name  = _load_font(FONT_PATH_BOLD,    46)
    font_sub   = _load_font(FONT_PATH_REGULAR, 26)
    font_level = _load_font(FONT_PATH_BOLD,    32)
    font_xp    = _load_font(FONT_PATH_REGULAR, 22)

    tx = card_x + 6

    # Rang-Badge (top-right)
    level_str  = f"LVL {level}"
    lbbox = draw.textbbox((0, 0), level_str, font=font_level)
    lw    = lbbox[2] - lbbox[0] + 24
    lh    = lbbox[3] - lbbox[1] + 10
    lx    = W - lw - 24
    ly    = 26
    draw.rounded_rectangle([lx, ly, lx+lw, ly+lh], radius=10, fill=(88, 214, 141, 35))
    draw.rounded_rectangle([lx, ly, lx+lw, ly+lh], radius=10, outline=_RC_ACCENT, width=2)
    draw.text((lx + 12, ly + 5), level_str, font=font_level, fill=_RC_ACCENT)

    # XP-Text
    needed   = xp_for_level(level)
    xp_str   = f"{xp:,} / {needed:,} XP"
    xb       = draw.textbbox((0, 0), xp_str, font=font_xp)
    xw       = xb[2] - xb[0]
    draw.text((W - xw - 26, ly + lh + 8), xp_str, font=font_xp, fill=_RC_MUTED)

    # Name + Username
    draw.text((tx, 42), display_name, font=font_name, fill=_RC_TEXT)
    draw.text((tx, 102), f"@{username}", font=font_sub, fill=_RC_MUTED)

    # XP Progress Bar
    progress_ratio = min(xp / needed, 1.0) if needed > 0 else 0.0
    bar_x  = tx
    bar_y  = 175
    bar_w  = W - tx - 30
    bar_h  = 28

    draw.rounded_rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h], radius=14, fill=_RC_BAR_BG)
    filled = int(bar_w * progress_ratio)
    if filled > 0:
        # Zweifarbiger Gradient-Bar (Accent → Accent2)
        for px in range(max(filled, bar_h)):
            t  = px / max(bar_w, 1)
            cr = int(_RC_ACCENT[0] + t * (_RC_ACCENT2[0] - _RC_ACCENT[0]))
            cg = int(_RC_ACCENT[1] + t * (_RC_ACCENT2[1] - _RC_ACCENT[1]))
            cb = int(_RC_ACCENT[2] + t * (_RC_ACCENT2[2] - _RC_ACCENT[2]))
            draw.line([(bar_x+px, bar_y), (bar_x+px, bar_y+bar_h)], fill=(cr, cg, cb, 255))
        # Abgerundete Endkante über den Balken legen
        draw.rounded_rectangle([bar_x, bar_y, bar_x+max(filled,bar_h), bar_y+bar_h], radius=14, outline=_RC_BG, width=0)

    # Footer
    draw.text((tx, H - 28), _FT, font=font_xp, fill=(55, 58, 72, 255))

    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


class Rank(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self.store   = JSONStore(XP_PATH, default_xp())
        self.session: aiohttp.ClientSession | None = None
        # Write-Behind-Cache: {guild_id: {user_id: {"xp": int, "level": int}}}
        self._xp_pending: dict[str, dict] = {}
        # Einmal geladene Basisdaten (wird beim ersten flush befüllt)
        self._xp_base_loaded: bool = False
        self._xp_base: dict = {}
        self.flush_xp.start()

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        self.flush_xp.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    # ── XP Write-Behind: alle 30 Sek. flushen ────────────────────────────────

    @tasks.loop(seconds=30)
    async def flush_xp(self):
        if not self._xp_pending:
            return
        pending = self._xp_pending.copy()
        self._xp_pending.clear()

        def mutate(data):
            for guild_id, users in pending.items():
                guild_data = data.setdefault(guild_id, {})
                for user_id, delta in users.items():
                    entry = guild_data.setdefault(user_id, {"xp": 0, "level": 0})
                    entry["xp"]    += delta["xp"]
                    entry["level"]  = delta["level"]
            return data

        try:
            await self.store.update(mutate)
        except Exception:
            pass

    @flush_xp.before_loop
    async def before_flush_xp(self):
        await self.bot.wait_until_ready()

    # ── on_message: XP in-memory akkumulieren (KEIN store.read() pro Nachricht) ─

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        guild_id = str(message.guild.id)
        user_id  = str(message.author.id)

        pending = self._xp_pending.setdefault(guild_id, {})

        if user_id not in pending:
            if not self._xp_base_loaded:
                self._xp_base        = await self.store.read()
                self._xp_base_loaded = True
            base_entry = self._xp_base.get(guild_id, {}).get(user_id, {"xp": 0, "level": 0})
            pending[user_id] = {"xp": base_entry["xp"], "level": base_entry["level"]}

        cur       = pending[user_id]
        old_level = cur["level"]
        cur["xp"] += 5

        needed = xp_for_level(cur["level"])
        if cur["xp"] >= needed:
            cur["xp"]    -= needed
            cur["level"] += 1
            # Level-Up Benachrichtigung im Channel
            try:
                lvl_embed = discord.Embed(
                    title="⬆️ Level Up!",
                    description=(
                        f"🎉 **{message.author.display_name}** ist auf **Level {cur['level']}** aufgestiegen!\n"
                        f"Weiter so — nächstes Level in **{xp_for_level(cur['level']):,} XP**."
                    ),
                    color=discord.Color.from_rgb(130, 80, 255),
                )
                lvl_embed.set_thumbnail(url=message.author.display_avatar.url)
                from utils.theme import get_footer_text as _gft
                lvl_embed.set_footer(text=_gft(message.guild))
                await message.channel.send(embed=lvl_embed, delete_after=15)
            except discord.HTTPException:
                pass

    # ── /rank ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="rank", description="Zeigt deine (oder eine fremde) Rank-Card an.")
    @app_commands.describe(user="Der User, dessen Rank angezeigt werden soll")
    async def rank(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        target = user or interaction.user

        # Gecachte Daten + ausstehende pending XP berücksichtigen
        data      = await self.store.read()
        base      = data.get(str(interaction.guild.id), {}).get(str(target.id), {"xp": 0, "level": 0})
        pending   = self._xp_pending.get(str(interaction.guild.id), {}).get(str(target.id), base)
        user_data = pending

        try:
            avatar_url   = target.display_avatar.replace(size=256, format="png").url
            async with self.session.get(str(avatar_url)) as resp:
                avatar_bytes = await resp.read()

            loop         = asyncio.get_running_loop()
            image_bytes  = await loop.run_in_executor(
                None,
                partial(_sync_generate_rank_card,
                        target.display_name, target.name,
                        avatar_bytes, user_data["level"], user_data["xp"])
            )
        except Exception as e:
            await interaction.followup.send(embed=error_embed("❌ Fehler bei der Bildgenerierung", str(e)))
            return

        await interaction.followup.send(file=discord.File(io.BytesIO(image_bytes), filename="rank.png"))

    # ── /addxp ────────────────────────────────────────────────────────────────

    @app_commands.command(name="addxp", description="[Admin] Fügt einem User XP hinzu.")
    @app_commands.describe(user="Der User", menge="Anzahl XP")
    async def addxp(self, interaction: discord.Interaction,
                    user: discord.Member, menge: app_commands.Range[int, 1, 10000000000]):
        if not await check_role_permission(interaction, "utility"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        user_id  = str(user.id)

        # Pending XP direkt modifizieren — wird beim nächsten Flush gespeichert
        pending = self._xp_pending.setdefault(guild_id, {})
        if user_id not in pending:
            data = await self.store.read()
            base = data.get(guild_id, {}).get(user_id, {"xp": 0, "level": 0})
            pending[user_id] = {"xp": base["xp"], "level": base["level"]}

        pending[user_id]["xp"] += menge
        needed = xp_for_level(pending[user_id]["level"])
        while pending[user_id]["xp"] >= needed:
            pending[user_id]["xp"]    -= needed
            pending[user_id]["level"] += 1
            needed = xp_for_level(pending[user_id]["level"])

        await interaction.response.send_message(
            embed=success_embed("✅ XP hinzugefügt",
                                f"**{user.mention}** hat **+{menge:,} XP** erhalten.\n"
                                f"Aktuell: Level **{pending[user_id]['level']}**, {pending[user_id]['xp']:,} XP"),
            ephemeral=True)

    # ── /removexp ─────────────────────────────────────────────────────────────

    @app_commands.command(name="removexp", description="[Admin] Entfernt XP von einem User.")
    @app_commands.describe(user="Der User", menge="Anzahl XP die entfernt werden sollen")
    async def removexp(self, interaction: discord.Interaction,
                       user: discord.Member, menge: app_commands.Range[int, 1, 100000]):
        if not await check_role_permission(interaction, "utility"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        user_id  = str(user.id)

        pending = self._xp_pending.setdefault(guild_id, {})
        if user_id not in pending:
            data = await self.store.read()
            base = data.get(guild_id, {}).get(user_id, {"xp": 0, "level": 0})
            pending[user_id] = {"xp": base["xp"], "level": base["level"]}

        total_xp = pending[user_id]["xp"]
        for lvl in range(pending[user_id]["level"]):
            total_xp += xp_for_level(lvl)
        total_xp = max(0, total_xp - menge)

        # Zurückrechnen auf Level + XP
        level = 0
        while True:
            needed = xp_for_level(level)
            if total_xp < needed:
                break
            total_xp -= needed
            level    += 1

        pending[user_id]["xp"]    = total_xp
        pending[user_id]["level"] = level

        await interaction.response.send_message(
            embed=success_embed("✅ XP entfernt",
                                f"**{user.mention}** verlor **-{menge:,} XP**.\n"
                                f"Aktuell: Level **{level}**, {total_xp:,} XP"),
            ephemeral=True)

    # ── /setxp ────────────────────────────────────────────────────────────────

    @app_commands.command(name="setxp", description="[Admin] Setzt die XP eines Users auf einen bestimmten Wert.")
    @app_commands.describe(user="Der User", menge="Neuer XP-Wert")
    async def setxp(self, interaction: discord.Interaction,
                    user: discord.Member, menge: app_commands.Range[int, 0, 1000000]):
        if not await check_role_permission(interaction, "utility"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        user_id  = str(user.id)

        level = 0
        xp    = menge
        while True:
            needed = xp_for_level(level)
            if xp < needed:
                break
            xp    -= needed
            level += 1

        def mutate(d):
            d.setdefault(guild_id, {})[user_id] = {"xp": xp, "level": level}
            return d

        await self.store.update(mutate)
        # Pending-Cache invalidieren
        self._xp_pending.get(guild_id, {}).pop(user_id, None)

        await interaction.response.send_message(
            embed=success_embed("✅ XP gesetzt",
                                f"**{user.mention}** hat jetzt **{menge:,} Gesamt-XP**.\n"
                                f"Level **{level}**, {xp:,} XP im aktuellen Level"),
            ephemeral=True)

    # ── /resetxp ──────────────────────────────────────────────────────────────

    @app_commands.command(name="resetxp", description="[Admin] Setzt die XP eines Users auf 0 zurück.")
    @app_commands.describe(user="Der User")
    async def resetxp(self, interaction: discord.Interaction, user: discord.Member):
        if not await check_role_permission(interaction, "utility"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        user_id  = str(user.id)

        def mutate(d):
            d.setdefault(guild_id, {})[user_id] = {"xp": 0, "level": 0}
            return d

        await self.store.update(mutate)
        self._xp_pending.get(guild_id, {}).pop(user_id, None)

        await interaction.response.send_message(
            embed=success_embed("✅ XP zurückgesetzt",
                                f"**{user.mention}** wurde auf Level 0 zurückgesetzt."),
            ephemeral=True)

    # ── /xp-leaderboard ───────────────────────────────────────────────────────

    @app_commands.command(name="xp-leaderboard", description="Zeigt die Top-10 User nach XP/Level.")
    async def xp_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild    = interaction.guild
        guild_id = str(guild.id)

        data  = await self.store.read()
        users = data.get(guild_id, {})

        # Pending-XP miteinbeziehen
        pending = self._xp_pending.get(guild_id, {})
        merged  = {}
        for uid, udata in users.items():
            merged[uid] = pending.get(uid, udata)
        for uid, pdata in pending.items():
            if uid not in merged:
                merged[uid] = pdata

        if not merged:
            await interaction.followup.send(
                embed=info_embed("📊 XP-Leaderboard", "Noch keine Daten vorhanden."))
            return

        sorted_users = sorted(
            merged.items(),
            key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)),
            reverse=True,
        )[:10]

        rank_icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        lines  = []
        for i, (uid, udata) in enumerate(sorted_users):
            member = guild.get_member(int(uid))
            name   = member.display_name if member else f"Unbekannt ({uid})"
            lvl    = udata.get("level", 0)
            xp     = udata.get("xp", 0)
            needed = xp_for_level(lvl)
            pct    = int(xp / needed * 10) if needed > 0 else 0
            bar    = "█" * pct + "░" * (10 - pct)
            lines.append(
                f"{rank_icons[i]} **{name}** — Level **{lvl}**\n"
                f"  `{bar}` {xp:,} / {needed:,} XP"
            )

        embed = discord.Embed(
            title="⭐  XP-Leaderboard",
            description="\n\n".join(lines),
            color=discord.Color.from_rgb(130, 80, 255),
            timestamp=discord.utils.utcnow(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text=f"{get_footer_text(interaction)}  ·  Top {len(sorted_users)} nach Level/XP")
        await interaction.followup.send(embed=embed)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = error_embed("❌ Fehler", str(error))
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rank(bot))
