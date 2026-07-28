"""
Utility-Commands — 13 nützliche Befehle für den AVOKE-Server.

/avatar            — Zeigt den Avatar eines Users groß an
/userinfo          — Detaillierte Infos über einen User
/roleinfo          — Infos über eine Rolle
/poll              — Erstellt eine Abstimmung mit bis zu 5 Optionen
/remind            — Setzt eine persönliche Erinnerung (max. 24h)
/snipe             — Zeigt die zuletzt gelöschte Nachricht im Kanal
/clear             — Löscht Nachrichten (Mod)
/slowmode          — Setzt den Slowmode eines Kanals (Mod)
/nick              — Ändert den Nickname eines Users (Mod)
/coinflip          — Münzwurf (Kopf oder Zahl)
/8ball             — Magische 8-Ball Antworten
/afk               — Setzt dich als AFK, Erwähnung → automatische Info
/membercount       — Zeigt die aktuelle Mitgliederzahl übersichtlich an (Embed)
/membercount-setup — Richtet automatisch aktualisierte Zähler-Kanäle ein
/membercount-remove— Entfernt die Zähler-Kanäle und die Konfiguration

Membercount-Kanal-System:
  Erstellt bis zu 4 Voice-Kanäle als Live-Zähler (Gesamt / Menschen / Bots / Online).
  Ein Task-Loop aktualisiert die Kanal-Namen alle 10 Sekunden — Discord-Rate-Limit
  beachtet: Kanalname-Änderungen sind auf 2x pro 10 Minuten limitiert, daher werden
  Änderungen nur durchgeführt wenn sich der Wert tatsächlich geändert hat.
"""

import asyncio
import datetime
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.storage import JSONStore
from utils.theme import (
    success_embed, error_embed, info_embed, warning_embed,
    gold_embed, COLOR_INFO, COLOR_PRIMARY, COLOR_WARNING, FOOTER_TEXT, get_footer_text,
)
from utils.permissions import check_role_permission

AFK_PATH            = "data/afk.json"
MEMBERCOUNT_PATH    = "data/membercount_config.json"

EIGHTBALL_ANSWERS = [
    "🟢 Auf jeden Fall!",
    "🟢 Absolut sicher.",
    "🟢 Ja, ohne Zweifel.",
    "🟢 Die Zeichen stehen gut.",
    "🟡 Frag später nochmal.",
    "🟡 Es ist schwer zu sagen.",
    "🟡 Konzentriere dich und frag nochmal.",
    "🔴 Ich bezweifle das sehr.",
    "🔴 Meine Quellen sagen Nein.",
    "🔴 Sieht nicht gut aus.",
    "🔴 Auf keinen Fall.",
]

COINFLIP_EMOJIS = {"Kopf": "🪙 Kopf", "Zahl": "💠 Zahl"}


def default_afk() -> dict:
    return {}  # guild_id -> {user_id: {"reason": str, "since": iso}}


def default_membercount() -> dict:
    """
    Schema:
    {
      "guild_id": {
        "category_id": int,           # Kategorie-ID (oben angepinnt)
        "channels": {
          "total":   int | null,      # Voice-Kanal-ID für Gesamt
          "humans":  int | null,
          "bots":    int | null,
          "online":  int | null,
        },
        "last_values": {              # vorherige Werte für Change-Detection
          "total": 0, "humans": 0, "bots": 0, "online": 0
        }
      }
    }
    """
    return {}


class Utility(commands.Cog):
    util = app_commands.Group(name="util", description="Allgemeine Tools und Community-Helfer.")
    def __init__(self, bot: commands.Bot):
        self.bot              = bot
        self.afk_store        = JSONStore(AFK_PATH,         default_afk())
        self.mc_store         = JSONStore(MEMBERCOUNT_PATH, default_membercount())
        # In-Memory Snipe-Cache: guild_id -> {channel_id: Message-Daten}
        self._snipe: dict[int, dict[int, dict]] = {}
        # In-Memory Reminder-Tasks
        self._reminders: list[asyncio.Task] = []
        self.update_counters.start()

    def cog_unload(self):
        self.update_counters.cancel()
        for task in self._reminders:
            task.cancel()

    # ─────────────────────────────────────────────────────────────────────────
    # Task-Loop: Zähler-Kanäle alle 10 Sekunden aktualisieren
    # Änderung nur wenn Wert sich geändert hat → Discord-Rate-Limit schonen
    # ─────────────────────────────────────────────────────────────────────────

    @tasks.loop(seconds=10)
    async def update_counters(self):
        data = await self.mc_store.read()
        if not data:
            return

        for guild_id_str, cfg in data.items():
            guild = self.bot.get_guild(int(guild_id_str))
            if not guild:
                continue

            channels_cfg  = cfg.get("channels", {})
            last          = cfg.get("last_values", {})

            total   = guild.member_count
            bots    = sum(1 for m in guild.members if m.bot)
            humans  = total - bots
            online  = sum(
                1 for m in guild.members
                if not m.bot and m.status != discord.Status.offline
            )

            new_values = {"total": total, "humans": humans, "bots": bots, "online": online}
            labels     = {
                "total":  f"👥 Mitglieder: {total}",
                "humans": f"🧑 Menschen: {humans}",
                "bots":   f"🤖 Bots: {bots}",
                "online": f"🟢 Online: {online}",
            }

            changed = False
            for key, new_val in new_values.items():
                if last.get(key) == new_val:
                    continue            # kein Update nötig
                ch_id = channels_cfg.get(key)
                if not ch_id:
                    continue
                ch = guild.get_channel(ch_id)
                if ch is None:
                    continue
                try:
                    await ch.edit(name=labels[key], reason="Membercount-Update")
                    changed = True
                except discord.HTTPException:
                    pass

            if changed:
                def mutate(d: dict) -> dict:
                    d.setdefault(guild_id_str, {})["last_values"] = new_values
                    return d
                await self.mc_store.update(mutate)

    @update_counters.before_loop
    async def before_update_counters(self):
        await self.bot.wait_until_ready()

    # ─────────────────────────────────────────────────────────────────────────
    # /avatar
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="avatar", description="Zeigt den Avatar eines Users in voller Größe.")
    @app_commands.describe(user="Der User dessen Avatar angezeigt werden soll")
    async def avatar(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        av     = target.display_avatar.replace(size=1024, format="png")

        embed = discord.Embed(
            title=f"🖼️ Avatar von {target.display_name}",
            color=COLOR_INFO,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_image(url=av.url)
        embed.add_field(
            name="Links",
            value=f"[PNG]({target.display_avatar.replace(format='png').url}) · "
                  f"[JPG]({target.display_avatar.replace(format='jpg').url}) · "
                  f"[WebP]({target.display_avatar.replace(format='webp').url})",
            inline=False,
        )
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # /userinfo
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="userinfo", description="Zeigt detaillierte Informationen über einen User.")
    @app_commands.describe(user="Der User dessen Infos angezeigt werden sollen")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        badges = []
        if target.bot:
            badges.append("🤖 Bot")
        if target.id == interaction.guild.owner_id:
            badges.append("👑 Owner")
        if target.guild_permissions.administrator:
            badges.append("🛡️ Admin")
        if target.premium_since:
            badges.append("💎 Booster")

        roles = [r.mention for r in reversed(target.roles) if r != interaction.guild.default_role]

        embed = discord.Embed(
            title=f"👤 {target.display_name}",
            color=target.color if target.color.value else COLOR_INFO,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🏷️ Tag",         value=str(target),                                    inline=True)
        embed.add_field(name="🆔 ID",           value=str(target.id),                                 inline=True)
        embed.add_field(name="📛 Badges",       value=" ".join(badges) or "Keine",                    inline=True)
        embed.add_field(name="📅 Account erstellt",
                        value=discord.utils.format_dt(target.created_at, "D"),                        inline=True)
        embed.add_field(name="📥 Beigetreten",
                        value=discord.utils.format_dt(target.joined_at, "D") if target.joined_at else "—",
                        inline=True)
        embed.add_field(name="🎨 Farbe",        value=str(target.color),                              inline=True)
        if roles:
            embed.add_field(
                name=f"🎭 Rollen ({len(roles)})",
                value=", ".join(roles[:10]) + ("…" if len(roles) > 10 else ""),
                inline=False,
            )
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # /roleinfo
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="roleinfo", description="Zeigt Informationen über eine Rolle.")
    @app_commands.describe(rolle="Die Rolle die angezeigt werden soll")
    async def roleinfo(self, interaction: discord.Interaction, rolle: discord.Role):
        perms = []
        if rolle.permissions.administrator:      perms.append("Administrator")
        if rolle.permissions.manage_guild:       perms.append("Server verwalten")
        if rolle.permissions.manage_roles:       perms.append("Rollen verwalten")
        if rolle.permissions.manage_channels:    perms.append("Kanäle verwalten")
        if rolle.permissions.ban_members:        perms.append("Mitglieder bannen")
        if rolle.permissions.kick_members:       perms.append("Mitglieder kicken")
        if rolle.permissions.moderate_members:   perms.append("Mitglieder moderieren")
        if rolle.permissions.manage_messages:    perms.append("Nachrichten verwalten")

        members_with_role = sum(1 for m in interaction.guild.members if rolle in m.roles)

        embed = discord.Embed(
            title=f"🎭 Rolle: {rolle.name}",
            color=rolle.color if rolle.color.value else COLOR_INFO,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="🆔 ID",          value=str(rolle.id),             inline=True)
        embed.add_field(name="🎨 Farbe",        value=str(rolle.color),          inline=True)
        embed.add_field(name="📍 Position",     value=str(rolle.position),       inline=True)
        embed.add_field(name="👥 Mitglieder",   value=str(members_with_role),    inline=True)
        embed.add_field(name="📌 Getrennt",     value="Ja" if rolle.hoist else "Nein", inline=True)
        embed.add_field(name="🔔 Erwähnbar",    value="Ja" if rolle.mentionable else "Nein", inline=True)
        embed.add_field(name="📅 Erstellt",
                        value=discord.utils.format_dt(rolle.created_at, "D"),   inline=True)
        if perms:
            embed.add_field(name="⚡ Berechtigungen",
                            value=", ".join(perms),                              inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # /poll
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="poll", description="Erstellt eine Abstimmung mit bis zu 5 Optionen.")
    @app_commands.describe(
        frage="Die Frage der Abstimmung",
        option1="Option 1", option2="Option 2",
        option3="Option 3 (optional)", option4="Option 4 (optional)", option5="Option 5 (optional)",
    )
    async def poll(
        self,
        interaction: discord.Interaction,
        frage: str,
        option1: str,
        option2: str,
        option3: str = None,
        option4: str = None,
        option5: str = None,
    ):
        options = [o for o in [option1, option2, option3, option4, option5] if o]
        emojis  = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

        desc = "\n".join(f"{emojis[i]}  {opt}" for i, opt in enumerate(options))
        embed = discord.Embed(
            title=f"📊 {frage}",
            description=desc,
            color=COLOR_PRIMARY,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_footer(text=f"Abstimmung von {interaction.user}  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        for i in range(len(options)):
            try:
                await msg.add_reaction(emojis[i])
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # /remind
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="remind", description="Setzt eine Erinnerung (max. 48 Stunden).")
    @app_commands.describe(
        stunden="Stunden (0–48)",
        minuten="Minuten zusätzlich (0–59)",
        nachricht="Was soll erinnert werden?",
    )
    async def remind(self, interaction: discord.Interaction, nachricht: str,
                     stunden: app_commands.Range[int, 0, 48] = 0,
                     minuten: app_commands.Range[int, 0, 59] = 0):
        total_minutes = stunden * 60 + minuten
        if total_minutes < 1:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültig", "Mindestens 1 Minute angeben."), ephemeral=True)
            return

        # Lesbare Zeitangabe
        parts = []
        if stunden: parts.append(f"{stunden}h")
        if minuten: parts.append(f"{minuten}m")
        time_str = " ".join(parts)

        when_ts = int((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=total_minutes)).timestamp())

        await interaction.response.send_message(
            embed=success_embed(
                "⏰ Erinnerung gesetzt!",
                f"Ich erinnere dich in **{time_str}** (<t:{when_ts}:R>) an:\n> {nachricht}",
            ),
            ephemeral=True,
        )

        async def _remind_task():
            await asyncio.sleep(total_minutes * 60)
            try:
                await interaction.user.send(embed=info_embed(
                    "⏰ Erinnerung!",
                    f"Du wolltest erinnert werden:\n> {nachricht}",
                ))
            except discord.Forbidden:
                try:
                    ch = interaction.channel
                    if ch:
                        await ch.send(
                            content=interaction.user.mention,
                            embed=info_embed("⏰ Erinnerung!", f"> {nachricht}\n<t:{when_ts}:R>"),
                        )
                except Exception:
                    pass

        task = asyncio.create_task(_remind_task())
        self._reminders.append(task)
        task.add_done_callback(lambda t: self._reminders.remove(t) if t in self._reminders else None)

    # ─────────────────────────────────────────────────────────────────────────
    # /snipe  — zuletzt gelöschte Nachricht
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="snipe", description="Zeigt die zuletzt gelöschte Nachricht in diesem Kanal.")
    async def snipe(self, interaction: discord.Interaction):
        channel_cache = self._snipe.get(interaction.guild_id, {})
        data = channel_cache.get(interaction.channel_id)
        if not data:
            await interaction.response.send_message(
                embed=info_embed("🔍 Nichts gefunden",
                                 "Keine kürzlich gelöschte Nachricht in diesem Kanal gespeichert."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="👻 Gelöschte Nachricht",
            description=data["content"] or "[kein Textinhalt]",
            color=COLOR_WARNING,
            timestamp=datetime.datetime.fromisoformat(data["deleted_at"]),
        )
        embed.set_author(name=data["author"], icon_url=data.get("avatar_url") or discord.Embed.Empty)
        embed.set_footer(text=f"Gelöscht  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Snipe-Cache befüllen."""
        if message.author.bot or not message.guild:
            return
        self._snipe.setdefault(message.guild.id, {})[message.channel.id] = {
            "content":    message.content,
            "author":     str(message.author),
            "avatar_url": str(message.author.display_avatar.url),
            "deleted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # /clear
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="clear", description="Löscht eine Anzahl Nachrichten im aktuellen Kanal.")
    @app_commands.describe(
        anzahl="Anzahl der zu löschenden Nachrichten (1–100)",
        user="Optional: Nur Nachrichten dieses Users löschen",
    )
    async def clear(
        self,
        interaction: discord.Interaction,
        anzahl: app_commands.Range[int, 1, 100],
        user: discord.Member = None,
    ):
        if not await check_role_permission(interaction, "utility"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        def check(m: discord.Message) -> bool:
            return user is None or m.author.id == user.id

        try:
            deleted = await interaction.channel.purge(limit=anzahl, check=check)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("❌ Keine Berechtigung", "Ich darf hier keine Nachrichten löschen."),
                ephemeral=True,
            )
            return

        who = f" von {user.mention}" if user else ""
        await interaction.followup.send(
            embed=success_embed(
                "🧹 Nachrichten gelöscht",
                f"**{len(deleted)}** Nachricht(en){who} wurden entfernt.",
            ),
            ephemeral=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /slowmode
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="slowmode", description="Setzt den Slowmode im aktuellen Kanal.")
    @app_commands.describe(sekunden="Slowmode in Sekunden (0 = deaktivieren, max. 21600)")
    async def slowmode(
        self,
        interaction: discord.Interaction,
        sekunden: app_commands.Range[int, 0, 21600],
    ):
        if not await check_role_permission(interaction, "utility"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        try:
            await interaction.channel.edit(slowmode_delay=sekunden)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung", "Ich darf den Slowmode nicht ändern."),
                ephemeral=True,
            )
            return

        if sekunden == 0:
            desc = "Slowmode wurde **deaktiviert**."
        elif sekunden < 60:
            desc = f"Slowmode: **{sekunden} Sekunde(n)**."
        else:
            mins = sekunden // 60
            secs = sekunden % 60
            desc = f"Slowmode: **{mins}m {secs}s**."

        await interaction.response.send_message(
            embed=success_embed("🐢 Slowmode gesetzt", desc),
            ephemeral=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /nick
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="nick", description="Ändert den Nickname eines Mitglieds.")
    @app_commands.describe(
        user="Das Mitglied",
        nickname="Neuer Nickname (leer lassen zum Zurücksetzen)",
    )
    async def nick(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        nickname: str = None,
    ):
        if not await check_role_permission(interaction, "utility"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True,
            )
            return

        try:
            await user.edit(nick=nickname, reason=f"Nick-Änderung durch {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Ich darf den Nickname dieses Mitglieds nicht ändern."),
                ephemeral=True,
            )
            return

        if nickname:
            desc = f"{user.mention} heißt jetzt **{nickname}**."
        else:
            desc = f"Nickname von {user.mention} wurde zurückgesetzt."

        await interaction.response.send_message(
            embed=success_embed("✏️ Nickname geändert", desc),
            ephemeral=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /coinflip
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="coinflip", description="Wirft eine Münze — Kopf oder Zahl?")
    async def coinflip(self, interaction: discord.Interaction):
        result = random.choice(["Kopf", "Zahl"])
        embed  = gold_embed(
            f"{COINFLIP_EMOJIS[result]}",
            f"Die Münze landete auf **{result}**!",
        )
        embed.set_footer(text=f"Geworfen von {interaction.user}  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # /8ball
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="8ball", description="Stell der magischen 8-Ball eine Frage.")
    @app_commands.describe(frage="Deine Ja/Nein-Frage")
    async def eightball(self, interaction: discord.Interaction, frage: str):
        answer = random.choice(EIGHTBALL_ANSWERS)
        embed  = discord.Embed(
            title="🎱 Magischer 8-Ball",
            color=discord.Color.from_rgb(30, 30, 35),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="❓ Frage",    value=frage,  inline=False)
        embed.add_field(name="💬 Antwort",  value=answer, inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # /afk
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="afk", description="Setzt dich als AFK. Wirst du erwähnt, wird der Absender informiert.")
    @app_commands.describe(grund="Grund für dein AFK (optional)")
    async def afk(self, interaction: discord.Interaction, grund: str = "AFK"):
        guild_id = str(interaction.guild_id)
        user_id  = str(interaction.user.id)
        now      = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def mutate(data: dict) -> dict:
            data.setdefault(guild_id, {})[user_id] = {"reason": grund, "since": now}
            return data

        await self.afk_store.update(mutate)
        await interaction.response.send_message(
            embed=info_embed(
                "💤 AFK gesetzt",
                f"Du bist jetzt AFK: **{grund}**\nDu wirst automatisch zurückgesetzt wenn du schreibst.",
            ),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild_id = str(message.guild.id)
        data     = await self.afk_store.read()
        guild_afk = data.get(guild_id, {})

        # AFK aufheben wenn der AFK-User selbst schreibt
        author_id = str(message.author.id)
        if author_id in guild_afk:
            afk_entry = guild_afk[author_id]
            since_str = afk_entry.get("since", "")
            def remove(d: dict) -> dict:
                d.get(guild_id, {}).pop(author_id, None)
                return d
            await self.afk_store.update(remove)
            try:
                since = datetime.datetime.fromisoformat(since_str)
                diff  = datetime.datetime.now(datetime.timezone.utc) - since
                mins  = int(diff.total_seconds() // 60)
                await message.channel.send(
                    embed=success_embed(
                        f"👋 Willkommen zurück, {message.author.display_name}!",
                        f"Du warst **{mins} Minute(n)** AFK ({afk_entry.get('reason', 'AFK')}).",
                    ),
                    delete_after=8,
                )
            except Exception:
                pass
            return

        # Erwähnte User auf AFK prüfen
        for mentioned in message.mentions:
            mid = str(mentioned.id)
            if mid in guild_afk:
                entry = guild_afk[mid]
                since_str = entry.get("since", "")
                try:
                    since = datetime.datetime.fromisoformat(since_str)
                    diff  = datetime.datetime.now(datetime.timezone.utc) - since
                    mins  = int(diff.total_seconds() // 60)
                    await message.channel.send(
                        embed=info_embed(
                            f"💤 {mentioned.display_name} ist AFK",
                            f"**Grund:** {entry.get('reason', 'AFK')}\n"
                            f"**Seit:** {mins} Minute(n)",
                        ),
                        delete_after=8,
                    )
                except Exception:
                    pass

    # ─────────────────────────────────────────────────────────────────────────
    # ─────────────────────────────────────────────────────────────────────────
    # /membercount  — Embed-Snapshot
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(name="membercount", description="Zeigt die aktuelle Mitgliederzahl des Servers.")
    async def membercount(self, interaction: discord.Interaction):
        guild   = interaction.guild
        total   = guild.member_count
        bots    = sum(1 for m in guild.members if m.bot)
        humans  = total - bots
        online  = sum(
            1 for m in guild.members
            if not m.bot and m.status != discord.Status.offline
        )

        embed = discord.Embed(
            title=f"👥 Mitglieder — {guild.name}",
            color=COLOR_PRIMARY,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="👥 Gesamt",    value=str(total),           inline=True)
        embed.add_field(name="🧑 Menschen",  value=str(humans),          inline=True)
        embed.add_field(name="🤖 Bots",      value=str(bots),            inline=True)
        embed.add_field(name="🟢 Online",    value=str(online),          inline=True)
        embed.add_field(name="⚫ Offline",    value=str(humans - online), inline=True)

        # Zeige ob Auto-Zähler aktiv ist
        mc_data  = await self.mc_store.read()
        is_setup = str(guild.id) in mc_data
        embed.add_field(
            name="📡 Auto-Zähler",
            value="✅ Aktiv" if is_setup else "❌ Nicht eingerichtet — nutze `/membercount-setup`",
            inline=False,
        )
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # /membercount-setup  — Zähler-Kanäle erstellen
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(
        name="membercount-setup",
        description="Erstellt automatisch aktualisierte Mitglieder-Zähler als Voice-Kanäle.",
    )
    @app_commands.describe(
        zeige_bots="Eigenen Kanal für Bot-Anzahl erstellen?",
        zeige_online="Eigenen Kanal für Online-Anzahl erstellen?",
    )
    async def membercount_setup(
        self,
        interaction: discord.Interaction,
        zeige_bots:   bool = True,
        zeige_online: bool = True,
    ):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild    = interaction.guild
        guild_id = str(guild.id)

        # Bestehende Config prüfen → alte Kanäle erst löschen
        old_data = await self.mc_store.read()
        if guild_id in old_data:
            for ch_id in old_data[guild_id].get("channels", {}).values():
                if ch_id:
                    ch = guild.get_channel(ch_id)
                    if ch:
                        try:
                            await ch.delete(reason="Membercount-Setup: Neueinrichtung")
                        except discord.HTTPException:
                            pass
            old_cat_id = old_data[guild_id].get("category_id")
            if old_cat_id:
                cat = guild.get_channel(old_cat_id)
                if cat:
                    try:
                        await cat.delete(reason="Membercount-Setup: Neueinrichtung")
                    except discord.HTTPException:
                        pass

        # Kategorie ganz oben erstellen
        total  = guild.member_count
        bots   = sum(1 for m in guild.members if m.bot)
        humans = total - bots
        online = sum(
            1 for m in guild.members
            if not m.bot and m.status != discord.Status.offline
        )

        # Keine Schreibrechte für @everyone in der Kategorie
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                connect=False, view_channel=True
            )
        }

        try:
            category = await guild.create_category(
                name="📊 Statistiken",
                position=0,
                overwrites=overwrites,
                reason="Membercount-Setup",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=error_embed("❌ Fehlende Berechtigung",
                                  "Ich kann keine Kategorie erstellen."),
                ephemeral=True,
            )
            return

        async def _make_vc(name: str) -> discord.VoiceChannel | None:
            try:
                return await guild.create_voice_channel(
                    name=name,
                    category=category,
                    overwrites=overwrites,
                    reason="Membercount-Setup",
                )
            except discord.HTTPException:
                return None

        ch_total  = await _make_vc(f"👥 Mitglieder: {total}")
        ch_humans = await _make_vc(f"🧑 Menschen: {humans}")
        ch_bots   = await _make_vc(f"🤖 Bots: {bots}") if zeige_bots else None
        ch_online = await _make_vc(f"🟢 Online: {online}") if zeige_online else None

        # Config speichern
        cfg = {
            "category_id": category.id,
            "channels": {
                "total":  ch_total.id  if ch_total  else None,
                "humans": ch_humans.id if ch_humans else None,
                "bots":   ch_bots.id   if ch_bots   else None,
                "online": ch_online.id if ch_online else None,
            },
            "last_values": {
                "total": total, "humans": humans,
                "bots": bots,   "online": online,
            },
        }

        def mutate(d: dict) -> dict:
            d[guild_id] = cfg
            return d

        await self.mc_store.update(mutate)

        lines = [f"📂 Kategorie: **{category.name}**"]
        if ch_total:  lines.append(f"👥 Gesamt-Kanal erstellt")
        if ch_humans: lines.append(f"🧑 Menschen-Kanal erstellt")
        if ch_bots:   lines.append(f"🤖 Bot-Kanal erstellt")
        if ch_online: lines.append(f"🟢 Online-Kanal erstellt")
        lines.append("\nDie Kanäle werden **alle 10 Sekunden** automatisch aktualisiert.")

        await interaction.followup.send(
            embed=success_embed("✅ Membercount-Zähler eingerichtet", "\n".join(lines)),
            ephemeral=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /membercount-remove  — Zähler-Kanäle & Config löschen
    # ─────────────────────────────────────────────────────────────────────────

    @util.command(
        name="membercount-remove",
        description="Entfernt alle Mitglieder-Zähler-Kanäle und die Konfiguration.",
    )
    async def membercount_remove(self, interaction: discord.Interaction):
        if not await check_role_permission(interaction, "moderation"):
            await interaction.response.send_message(
                embed=error_embed("❌ Keine Berechtigung",
                                  "Deine Rolle darf diesen Command nicht nutzen."),
                ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild    = interaction.guild
        guild_id = str(guild.id)

        data = await self.mc_store.read()
        if guild_id not in data:
            await interaction.followup.send(
                embed=warning_embed("⚠️ Nicht eingerichtet",
                                    "Für diesen Server sind keine Zähler-Kanäle konfiguriert."),
                ephemeral=True,
            )
            return

        cfg     = data[guild_id]
        deleted = 0

        for ch_id in cfg.get("channels", {}).values():
            if ch_id:
                ch = guild.get_channel(ch_id)
                if ch:
                    try:
                        await ch.delete(reason="Membercount-Remove")
                        deleted += 1
                    except discord.HTTPException:
                        pass

        cat_id = cfg.get("category_id")
        if cat_id:
            cat = guild.get_channel(cat_id)
            if cat:
                try:
                    await cat.delete(reason="Membercount-Remove")
                    deleted += 1
                except discord.HTTPException:
                    pass

        def mutate(d: dict) -> dict:
            d.pop(guild_id, None)
            return d

        await self.mc_store.update(mutate)
        await interaction.followup.send(
            embed=success_embed(
                "🗑️ Zähler entfernt",
                f"**{deleted}** Kanal/Kategorie(n) wurden gelöscht und die Konfiguration entfernt.",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
