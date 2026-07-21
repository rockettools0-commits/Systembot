"""
Welcome / Leave — mit Pillow-generiertem Banner.
Optimierungen:
- Shared aiohttp.ClientSession (kein neues Session-Objekt pro Join)
- Pillow läuft in run_in_executor (non-blocking)
"""

import asyncio
import io
import datetime
from functools import partial

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

from utils.storage import JSONStore
from utils.theme import success_embed, error_embed, COLOR_ERROR, COLOR_PRIMARY, FOOTER_TEXT, BG_DARK, ACCENT, get_footer_text

WELCOME_CONFIG_PATH = "data/welcome_config.json"
FONT_BOLD    = "fonts/Roboto-Bold.ttf"
FONT_REGULAR = "fonts/Roboto-Regular.ttf"


def default_config() -> dict:
    return {}


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        return ImageFont.load_default()


def _sync_generate_banner(
    display_name: str, username: str, avatar_bytes: bytes, member_count: int, guild_name: str = ""
) -> bytes:
    """Synchrone Pillow-Arbeit — läuft im ThreadPoolExecutor. Dark-Mode Design."""
    W, H  = 960, 320
    img   = Image.new("RGBA", (W, H), BG_DARK)
    draw  = ImageDraw.Draw(img)

    # Subtiler Hintergrund-Gradient (horizontal)
    for x in range(W):
        t = x / W
        r = int(18 + t * 12)
        g = int(18 + t * 8)
        b = int(22 + t * 30)
        draw.line([(x, 0), (x, H)], fill=(r, g, b, 255))

    # Accent-Rahmen oben + unten
    draw.rectangle([0, 0, W, 5],       fill=ACCENT)
    draw.rectangle([0, H - 5, W, H],   fill=ACCENT)

    # Dezente Card-Box hinter Text
    card_x = 260
    draw.rounded_rectangle([card_x - 10, 20, W - 20, H - 20], radius=16, fill=(26, 27, 35, 200))

    # Avatar
    av_size    = 200
    avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar_img = ImageOps.fit(avatar_img, (av_size, av_size))
    mask       = Image.new("L", (av_size, av_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, av_size, av_size), fill=255)
    avatar_img.putalpha(mask)

    av_x = 35
    av_y = (H - av_size) // 2
    img.paste(avatar_img, (av_x, av_y), avatar_img)

    # Avatar-Ring (zweifarbig: Accent + dunkel)
    draw.ellipse([av_x-6, av_y-6, av_x+av_size+6, av_y+av_size+6], outline=ACCENT,          width=4)
    draw.ellipse([av_x-10, av_y-10, av_x+av_size+10, av_y+av_size+10], outline=(44,47,51,255), width=3)

    font_big = _load_font(FONT_BOLD,    56)
    font_mid = _load_font(FONT_REGULAR, 28)
    font_sml = _load_font(FONT_REGULAR, 20)

    tx = card_x + 8
    draw.text((tx, 48),  "✦  Willkommen auf dem Server!",   font=font_sml, fill=(140, 140, 155, 255))
    draw.text((tx, 82),  display_name,                       font=font_big, fill=(245, 246, 250, 255))
    draw.text((tx, 160), f"@{username}",                     font=font_mid, fill=(120, 122, 140, 255))

    # Member-Badge
    badge_txt = f"  #{member_count:,}  "
    bbox = draw.textbbox((0, 0), badge_txt, font=font_mid)
    bw   = bbox[2] - bbox[0] + 16
    bh   = bbox[3] - bbox[1] + 8
    bx   = tx
    by   = 210
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, fill=(88, 214, 141, 40))
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=8, outline=ACCENT, width=1)
    draw.text((bx + 8, by + 4), badge_txt.strip(), font=font_mid, fill=ACCENT)

    footer_label = f"{guild_name} • System" if guild_name else FOOTER_TEXT
    draw.text((W - 180, H - 26), footer_label, font=font_sml, fill=(65, 68, 80, 255))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self.store   = JSONStore(WELCOME_CONFIG_PATH, default_config())
        self.session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    # ── /welcome-setup ────────────────────────────────────────────────────────

    @app_commands.command(name="welcome-setup", description="Konfiguriert Welcome- und Leave-Kanal.")
    @app_commands.describe(
        welcome_kanal="Kanal für Willkommensnachrichten",
        leave_kanal="Kanal für Abschiedsnachrichten (optional)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def welcome_setup(self, interaction: discord.Interaction,
                            welcome_kanal: discord.TextChannel,
                            leave_kanal: discord.TextChannel = None):
        def mutate(data):
            data[str(interaction.guild.id)] = {
                "channel_id":       welcome_kanal.id,
                "leave_channel_id": (leave_kanal or welcome_kanal).id,
            }
            return data

        await self.store.update(mutate)
        await interaction.response.send_message(
            embed=success_embed(
                "✅ Welcome konfiguriert",
                f"Welcome → {welcome_kanal.mention}\n"
                f"Leave → {(leave_kanal or welcome_kanal).mention}",
            ),
            ephemeral=True,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = (error_embed("❌ Keine Berechtigung", "Du benötigst Administrator-Rechte.")
               if isinstance(error, app_commands.MissingPermissions)
               else error_embed("❌ Fehler", str(error)))
        if interaction.response.is_done():
            await interaction.followup.send(embed=msg, ephemeral=True)
        else:
            await interaction.response.send_message(embed=msg, ephemeral=True)

    # ── Listener ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        data   = await self.store.read()
        config = data.get(str(member.guild.id))
        if not config:
            return
        channel = member.guild.get_channel(config["channel_id"])
        if channel is None:
            return

        banner_bytes = None
        try:
            avatar_url = member.display_avatar.replace(size=256, format="png").url
            async with self.session.get(str(avatar_url)) as resp:
                avatar_raw = await resp.read()
            loop         = asyncio.get_running_loop()
            banner_bytes = await loop.run_in_executor(
                None,
                partial(_sync_generate_banner,
                        member.display_name, member.name,
                        avatar_raw, member.guild.member_count, member.guild.name)
            )
        except Exception:
            pass

        embed = discord.Embed(
            title=f"✦  Willkommen, {member.display_name}!",
            description=(
                f"{member.mention} ist dem Server beigetreten.\n"
                f"Du bist Mitglied **#{member.guild.member_count:,}**."
            ),
            color=discord.Color.from_rgb(88, 214, 141),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=get_footer_text(member.guild))

        try:
            if banner_bytes:
                f = discord.File(io.BytesIO(banner_bytes), filename="welcome.png")
                embed.set_image(url="attachment://welcome.png")
                await channel.send(content=member.mention, embed=embed, file=f)
            else:
                await channel.send(content=member.mention, embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        data   = await self.store.read()
        config = data.get(str(member.guild.id))
        if not config:
            return
        channel = member.guild.get_channel(config.get("leave_channel_id", config["channel_id"]))
        if channel is None:
            return

        embed = discord.Embed(
            title=f"👋  {member.display_name} hat den Server verlassen",
            description=(
                f"**{member}** ist nicht mehr dabei.\n"
                f"Noch **{member.guild.member_count:,}** Mitglieder übrig."
            ),
            color=discord.Color.from_rgb(235, 77, 75),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=get_footer_text(member.guild))
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
