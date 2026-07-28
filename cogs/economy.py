"""
Economy-System: Balance, Daily, Work, Crime, Slut, Pay, Rob, Gamble, Give,
Deposit/Withdraw, Shop, Buy, Inventory, Leaderboard.

Neu in v3:
  /rob     — Bestehle einen anderen User (mit Risiko)
  /gamble  — Setze Coins auf Glück (50% Chance, doppelter Gewinn)
  /give    — Schenke Coins an einen anderen User (wie /pay aber mit Stil)
"""

import datetime
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils.storage import JSONStore
from utils.theme import gold_embed, error_embed, info_embed, success_embed, warning_embed, COLOR_GOLD

ECONOMY_PATH = "data/economy.json"
DAILY_COOLDOWN_HOURS   = 24
DAILY_REWARD_MIN       = 1500
DAILY_REWARD_MAX       = 3500
WORK_COOLDOWN_MINUTES  = 30
WORK_REWARD_MIN        = 500
WORK_REWARD_MAX        = 1800
CRIME_COOLDOWN_MINUTES = 60
CRIME_REWARD_MIN       = 800
CRIME_REWARD_MAX       = 4000
CRIME_FINE_MIN         = 500
CRIME_FINE_MAX         = 2000
CRIME_SUCCESS_CHANCE   = 0.75
SLUT_COOLDOWN_MINUTES  = 45
SLUT_REWARD_MIN        = 400
SLUT_REWARD_MAX        = 1500
ROB_COOLDOWN_MINUTES   = 90
ROB_SUCCESS_CHANCE     = 0.55
ROB_MAX_STEAL_PCT      = 0.55   # max 25% des Opfer-Guthabens
ROB_FINE_MIN           = 1000
ROB_FINE_MAX           = 3000
GAMBLE_COOLDOWN_SECONDS = 30

WORK_MESSAGES = [
    "Du hast Diamanten für den AVOKE abgebaut",
    "Du hast als Händler Trades abgewickelt",
    "Du hast als Wächter Patrouille gemacht",
    "Du hast die Farmfelder bewässert",
    "Du hast Redstone-Schemata gebaut",
    "Du hast Angriffe auf den Server abgewehrt",
    "Du hast als Architekt eine neue Basis geplant",
]
CRIME_SUCCESS_MESSAGES = [
    "Du hast eine Schatzkiste auf dem Server bestohlen",
    "Du hast illegale Diamanten verkauft",
    "Du hast eine Redstone-Farm sabotiert und Beute gemacht",
    "Du hast einen Händler ausgeraubt",
    "Du hast wertvolle Items aus einem fremden Lager gestohlen",
]
CRIME_FAIL_MESSAGES = [
    "Du wurdest erwischt und musstest eine Strafe zahlen",
    "Dein Plan ist gescheitert und du hast Coins verloren",
    "Die Wachen haben dich geschnappt",
]
SLUT_MESSAGES = [
    "Du hast Werbung für dubiose Server gemacht",
    "Du hast Items für andere gefarmt",
    "Du hast als Laufjunge Nachrichten überbracht",
    "Du hast Spieler für eine Quest angeheuert",
]
ROB_SUCCESS_MESSAGES = [
    "Du hast den Rucksack deines Opfers durchwühlt",
    "Du hast beim Tausch heimlich was mitgenommen",
    "Du hast unbemerkt die Kasse geleert",
    "Blitzschnell — dein Opfer hat gar nichts gemerkt",
]
ROB_FAIL_MESSAGES = [
    "Du wurdest auf frischer Tat ertappt und musstest zahlen",
    "Dein Opfer hatte genug Schutz — jetzt bist du ärmer",
    "Der Plan ist schiefgegangen, die Wächter haben dich erwischt",
]
GAMBLE_WIN_MESSAGES  = ["Du hast das Glück auf deiner Seite!", "Jackpot! Doppelter Gewinn!", "Die Würfel lagen perfekt!"]
GAMBLE_LOSE_MESSAGES = ["Das Haus gewinnt immer ...", "Kein Glück diesmal.", "Die Odds waren gegen dich."]

COIN_EMOJI = "🪙"

SHOP_ITEMS: dict[str, dict] = {
    "vip_tag":     {"name": "🏅 VIP-Tag",         "price": 5000,  "description": "Exklusiver VIP-Status im Inventar"},
    "lucky_charm": {"name": "🍀 Glücksbringer",   "price": 1500,  "description": "+10% Crime-Erfolgsbonus für 24h"},
    "xp_boost":    {"name": "⚡ XP-Boost",         "price": 2000,  "description": "Doppelte XP für 1 Stunde"},
    "coin_bag":    {"name": "💰 Coin-Beutel",      "price": 500,   "description": "Zufällige Coins (200–800)"},
    "mystery_box": {"name": "🎁 Mystery Box",      "price": 3000,  "description": "Überraschungsinhalt"},
}


def _user_data(data: dict, guild_id: str, user_id: str) -> dict:
    return data.setdefault(guild_id, {}).setdefault(
        user_id, {"coins": 0, "bank": 0,
                  "last_daily": None, "last_work": None,
                  "last_crime": None, "last_slut": None,
                  "last_rob": None,   "last_gamble": None,
                  "inventory": []},
    )


class Economy(commands.Cog):
    eco = app_commands.Group(name="eco", description="Wirtschaft, Shop und Coins.")
    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self.store = JSONStore(ECONOMY_PATH, {})
        self._lb_cache: dict[str, list] = {}

    def _invalidate_lb(self, guild_id: str) -> None:
        self._lb_cache.pop(guild_id, None)

    # ── /balance ──────────────────────────────────────────────────────────────

    @eco.command(name="balance", description="Zeigt dein oder ein fremdes Konto-Guthaben an.")
    @app_commands.describe(user="User, dessen Kontostand du sehen möchtest")
    async def balance(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        data   = await self.store.read()
        ud     = _user_data(data, str(interaction.guild.id), str(target.id))
        coins, bank = ud["coins"], ud.get("bank", 0)
        total = coins + bank

        embed = gold_embed(
            f"{COIN_EMOJI} Konto von {target.display_name}",
            f"**Bar:** {coins:,} {COIN_EMOJI}\n"
            f"**Bank:** {bank:,} {COIN_EMOJI}\n"
            f"**Gesamt:** {total:,} {COIN_EMOJI}",
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /daily ────────────────────────────────────────────────────────────────

    @eco.command(name="daily", description="Täglicher Bonus — alle 20 Stunden einlösbar.")
    async def daily(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        user_id  = str(interaction.user.id)
        now      = datetime.datetime.now(datetime.timezone.utc)
        reward   = random.randint(DAILY_REWARD_MIN, DAILY_REWARD_MAX)
        holder: dict = {}

        def mutate(d):
            e = _user_data(d, guild_id, user_id)
            last = e.get("last_daily")
            if last:
                rem = datetime.timedelta(hours=DAILY_COOLDOWN_HOURS) - (now - datetime.datetime.fromisoformat(last))
                if rem.total_seconds() > 0:
                    holder["rem"] = rem; return d
            e["coins"] += reward; e["last_daily"] = now.isoformat()
            holder["bal"] = e["coins"]; return d

        await self.store.update(mutate)
        if "rem" in holder:
            h, r = divmod(int(holder["rem"].total_seconds()), 3600)
            await interaction.response.send_message(
                embed=error_embed("⏳ Daily bereits abgeholt", f"Nächster Daily in **{h}h {r//60}m**."), ephemeral=True)
            return
        self._invalidate_lb(guild_id)
        embed = gold_embed(f"{COIN_EMOJI} Daily-Bonus erhalten!",
                           f"**+{reward:,} {COIN_EMOJI}**  ·  Guthaben: **{holder['bal']:,} {COIN_EMOJI}**")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /work ─────────────────────────────────────────────────────────────────

    @eco.command(name="work", description="Arbeite für Coins — alle 30 Minuten nutzbar.")
    async def work(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id); user_id = str(interaction.user.id)
        now = datetime.datetime.now(datetime.timezone.utc)
        reward = random.randint(WORK_REWARD_MIN, WORK_REWARD_MAX)
        action = random.choice(WORK_MESSAGES); holder: dict = {}

        def mutate(d):
            e = _user_data(d, guild_id, user_id)
            last = e.get("last_work")
            if last:
                rem = datetime.timedelta(minutes=WORK_COOLDOWN_MINUTES) - (now - datetime.datetime.fromisoformat(last))
                if rem.total_seconds() > 0:
                    holder["rem"] = rem; return d
            e["coins"] += reward; e["last_work"] = now.isoformat()
            holder["bal"] = e["coins"]; return d

        await self.store.update(mutate)
        if "rem" in holder:
            s = int(holder["rem"].total_seconds())
            await interaction.response.send_message(
                embed=error_embed("⏳ Noch nicht verfügbar", f"Wieder in **{s//60}m {s%60}s** arbeiten."), ephemeral=True)
            return
        self._invalidate_lb(guild_id)
        embed = gold_embed("💼 Arbeit erledigt!",
                           f"{action} — **+{reward:,} {COIN_EMOJI}**\nGuthaben: **{holder['bal']:,} {COIN_EMOJI}**")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /crime ────────────────────────────────────────────────────────────────

    @eco.command(name="crime", description="Begehe ein Verbrechen — riskant aber lukrativ (60 Min.).")
    async def crime(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id); user_id = str(interaction.user.id)
        now = datetime.datetime.now(datetime.timezone.utc); holder: dict = {}

        def mutate(d):
            e = _user_data(d, guild_id, user_id)
            last = e.get("last_crime")
            if last:
                rem = datetime.timedelta(minutes=CRIME_COOLDOWN_MINUTES) - (now - datetime.datetime.fromisoformat(last))
                if rem.total_seconds() > 0:
                    holder["rem"] = rem; return d
            if random.random() < CRIME_SUCCESS_CHANCE:
                r = random.randint(CRIME_REWARD_MIN, CRIME_REWARD_MAX)
                e["coins"] += r; holder["reward"] = r; holder["msg"] = random.choice(CRIME_SUCCESS_MESSAGES)
            else:
                f = random.randint(CRIME_FINE_MIN, min(CRIME_FINE_MAX, max(e["coins"], 1)))
                e["coins"] = max(0, e["coins"] - f); holder["fine"] = f; holder["msg"] = random.choice(CRIME_FAIL_MESSAGES)
            e["last_crime"] = now.isoformat(); holder["bal"] = e["coins"]; return d

        await self.store.update(mutate)
        if "rem" in holder:
            s = int(holder["rem"].total_seconds())
            await interaction.response.send_message(
                embed=error_embed("⏳ Zu früh", f"In **{s//60}m {s%60}s** wieder möglich."), ephemeral=True)
            return
        self._invalidate_lb(guild_id)
        if "reward" in holder:
            embed = success_embed("🦹 Crime erfolgreich!",
                                  f"{holder['msg']}.\n**+{holder['reward']:,} {COIN_EMOJI}**  ·  "
                                  f"Guthaben: **{holder['bal']:,} {COIN_EMOJI}**")
        else:
            embed = error_embed("🚔 Erwischt!",
                                f"{holder['msg']}.\n**-{holder['fine']:,} {COIN_EMOJI}**  ·  "
                                f"Guthaben: **{holder['bal']:,} {COIN_EMOJI}**")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /slut ─────────────────────────────────────────────────────────────────

    @eco.command(name="slut", description="Verdiene Coins durch zweifelhafte Dienste (45 Min.).")
    async def slut(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id); user_id = str(interaction.user.id)
        now = datetime.datetime.now(datetime.timezone.utc)
        reward = random.randint(SLUT_REWARD_MIN, SLUT_REWARD_MAX)
        action = random.choice(SLUT_MESSAGES); holder: dict = {}

        def mutate(d):
            e = _user_data(d, guild_id, user_id)
            last = e.get("last_slut")
            if last:
                rem = datetime.timedelta(minutes=SLUT_COOLDOWN_MINUTES) - (now - datetime.datetime.fromisoformat(last))
                if rem.total_seconds() > 0:
                    holder["rem"] = rem; return d
            e["coins"] += reward; e["last_slut"] = now.isoformat()
            holder["bal"] = e["coins"]; return d

        await self.store.update(mutate)
        if "rem" in holder:
            s = int(holder["rem"].total_seconds())
            await interaction.response.send_message(
                embed=error_embed("⏳ Noch nicht verfügbar", f"In **{s//60}m {s%60}s** wieder möglich."), ephemeral=True)
            return
        self._invalidate_lb(guild_id)
        embed = gold_embed("💋 Verdient!", f"{action} — **+{reward:,} {COIN_EMOJI}**\nGuthaben: **{holder['bal']:,} {COIN_EMOJI}**")
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /rob ──────────────────────────────────────────────────────────────────

    @eco.command(name="rob", description="Bestehle einen anderen User (45% Erfolg, alle 90 Min.).")
    @app_commands.describe(opfer="Das Mitglied, das du bestehlen möchtest")
    async def rob(self, interaction: discord.Interaction, opfer: discord.Member):
        if opfer.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("❌ Selbst-Raub", "Du kannst dich nicht selbst bestehlen."), ephemeral=True)
            return
        if opfer.bot:
            await interaction.response.send_message(
                embed=error_embed("❌ Kein Ziel", "Du kannst keinen Bot bestehlen."), ephemeral=True)
            return

        guild_id  = str(interaction.guild.id)
        robber_id = str(interaction.user.id)
        victim_id = str(opfer.id)
        now       = datetime.datetime.now(datetime.timezone.utc)
        holder: dict = {}

        def mutate(d):
            robber = _user_data(d, guild_id, robber_id)
            victim = _user_data(d, guild_id, victim_id)

            # Cooldown prüfen
            last = robber.get("last_rob")
            if last:
                rem = datetime.timedelta(minutes=ROB_COOLDOWN_MINUTES) - (now - datetime.datetime.fromisoformat(last))
                if rem.total_seconds() > 0:
                    holder["rem"] = rem; return d

            # Opfer hat nichts zu holen?
            if victim["coins"] < 50:
                holder["broke"] = True; return d

            robber["last_rob"] = now.isoformat()
            if random.random() < ROB_SUCCESS_CHANCE:
                stolen = random.randint(50, max(50, int(victim["coins"] * ROB_MAX_STEAL_PCT)))
                victim["coins"]  = max(0, victim["coins"] - stolen)
                robber["coins"] += stolen
                holder["stolen"] = stolen; holder["msg"] = random.choice(ROB_SUCCESS_MESSAGES)
                holder["bal"]    = robber["coins"]
            else:
                fine = random.randint(ROB_FINE_MIN, min(ROB_FINE_MAX, max(robber["coins"], 1)))
                robber["coins"] = max(0, robber["coins"] - fine)
                holder["fine"]  = fine; holder["msg"] = random.choice(ROB_FAIL_MESSAGES)
                holder["bal"]   = robber["coins"]
            return d

        await self.store.update(mutate)

        if "rem" in holder:
            s = int(holder["rem"].total_seconds())
            await interaction.response.send_message(
                embed=error_embed("⏳ Cooldown", f"In **{s//60}m {s%60}s** wieder möglich."), ephemeral=True)
            return
        if "broke" in holder:
            await interaction.response.send_message(
                embed=warning_embed("💸 Nichts zu holen", f"{opfer.mention} hat weniger als **50 {COIN_EMOJI}** — lohnt sich nicht."),
                ephemeral=True)
            return

        self._invalidate_lb(guild_id)
        if "stolen" in holder:
            embed = success_embed("🦝 Raub erfolgreich!",
                                  f"{holder['msg']}.\n"
                                  f"Du hast {opfer.mention} **{holder['stolen']:,} {COIN_EMOJI}** geklaut!\n"
                                  f"Dein Guthaben: **{holder['bal']:,} {COIN_EMOJI}**")
        else:
            embed = error_embed("🚨 Raub gescheitert!",
                                f"{holder['msg']}.\n"
                                f"**-{holder['fine']:,} {COIN_EMOJI}** Strafe.\n"
                                f"Dein Guthaben: **{holder['bal']:,} {COIN_EMOJI}**")
        embed.set_thumbnail(url=opfer.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /gamble ───────────────────────────────────────────────────────────────

    @eco.command(name="gamble", description="Setze Coins — 50% Chance auf doppelten Gewinn (30s Cooldown).")
    @app_commands.describe(einsatz="Anzahl Coins die du setzen möchtest (oder 'all')")
    async def gamble(self, interaction: discord.Interaction, einsatz: str):
        guild_id = str(interaction.guild.id)
        user_id  = str(interaction.user.id)
        now      = datetime.datetime.now(datetime.timezone.utc)
        holder: dict = {}

        def mutate(d):
            e = _user_data(d, guild_id, user_id)

            # Cooldown
            last = e.get("last_gamble")
            if last:
                rem = datetime.timedelta(seconds=GAMBLE_COOLDOWN_SECONDS) - (now - datetime.datetime.fromisoformat(last))
                if rem.total_seconds() > 0:
                    holder["rem"] = rem; return d

            # Einsatz bestimmen
            amount = e["coins"] if einsatz.lower() == "all" else None
            if amount is None:
                try:
                    amount = int(einsatz)
                except ValueError:
                    holder["error"] = "Ungültiger Einsatz. Nutze eine Zahl oder 'all'."; return d
            if amount <= 0:
                holder["error"] = "Einsatz muss größer als 0 sein."; return d
            if amount > e["coins"]:
                holder["error"] = f"Du hast nur **{e['coins']:,} {COIN_EMOJI}**."; return d

            e["last_gamble"] = now.isoformat()
            if random.random() < 0.5:
                e["coins"] += amount
                holder["won"] = amount; holder["msg"] = random.choice(GAMBLE_WIN_MESSAGES)
            else:
                e["coins"] -= amount
                holder["lost"] = amount; holder["msg"] = random.choice(GAMBLE_LOSE_MESSAGES)
            holder["bal"] = e["coins"]
            holder["einsatz"] = amount
            return d

        await self.store.update(mutate)

        if "rem" in holder:
            s = int(holder["rem"].total_seconds())
            await interaction.response.send_message(
                embed=error_embed("⏳ Cooldown", f"Wieder in **{s}s** möglich."), ephemeral=True)
            return
        if "error" in holder:
            await interaction.response.send_message(
                embed=error_embed("❌ Fehler", holder["error"]), ephemeral=True)
            return

        self._invalidate_lb(guild_id)
        e_str = f"{holder['einsatz']:,} {COIN_EMOJI}"
        if "won" in holder:
            embed = discord.Embed(
                title="🎰 Gewonnen!",
                description=f"{holder['msg']}\n\n**Einsatz:** {e_str}\n**Gewinn:** +{holder['won']:,} {COIN_EMOJI}\n"
                            f"**Guthaben:** {holder['bal']:,} {COIN_EMOJI}",
                color=discord.Color.from_rgb(88, 214, 141),
                timestamp=discord.utils.utcnow(),
            )
        else:
            embed = discord.Embed(
                title="🎰 Verloren!",
                description=f"{holder['msg']}\n\n**Einsatz:** {e_str}\n**Verlust:** -{holder['lost']:,} {COIN_EMOJI}\n"
                            f"**Guthaben:** {holder['bal']:,} {COIN_EMOJI}",
                color=discord.Color.from_rgb(235, 77, 75),
                timestamp=discord.utils.utcnow(),
            )
        from utils.theme import get_footer_text
        embed.set_footer(text=get_footer_text(interaction))
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ── /pay ──────────────────────────────────────────────────────────────────

    @eco.command(name="pay", description="Überweise Coins an einen anderen User.")
    @app_commands.describe(empfaenger="Empfänger", betrag="Anzahl Coins")
    async def pay(self, interaction: discord.Interaction, empfaenger: discord.Member, betrag: int):
        if betrag <= 0:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültiger Betrag", "Betrag muss > 0 sein."), ephemeral=True); return
        if empfaenger.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("❌ Selbst-Überweisung", "Du kannst dir selbst keine Coins senden."), ephemeral=True); return

        guild_id = str(interaction.guild.id)
        holder: dict = {}

        def mutate(d):
            s = _user_data(d, guild_id, str(interaction.user.id))
            if s["coins"] < betrag:
                holder["short"] = s["coins"]; return d
            s["coins"] -= betrag
            _user_data(d, guild_id, str(empfaenger.id))["coins"] += betrag
            holder["ok"] = True; return d

        await self.store.update(mutate)
        if "short" in holder:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht genug", f"Du hast nur **{holder['short']:,} {COIN_EMOJI}**."), ephemeral=True); return
        self._invalidate_lb(guild_id)
        await interaction.response.send_message(
            embed=success_embed(f"{COIN_EMOJI} Überweisung",
                                f"**{interaction.user.mention}** → **{empfaenger.mention}**\n"
                                f"**{betrag:,} {COIN_EMOJI}**"))

    # ── /give ─────────────────────────────────────────────────────────────────

    @eco.command(name="give", description="Schenke jemandem Coins mit einer persönlichen Nachricht.")
    @app_commands.describe(empfaenger="Empfänger", betrag="Anzahl Coins", nachricht="Persönliche Nachricht (optional)")
    async def give(self, interaction: discord.Interaction, empfaenger: discord.Member,
                   betrag: int, nachricht: str = None):
        if betrag <= 0:
            await interaction.response.send_message(
                embed=error_embed("❌ Ungültiger Betrag", "Betrag muss > 0 sein."), ephemeral=True); return
        if empfaenger.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht möglich", "Du kannst dir selbst nichts schenken."), ephemeral=True); return

        guild_id = str(interaction.guild.id); holder: dict = {}

        def mutate(d):
            s = _user_data(d, guild_id, str(interaction.user.id))
            if s["coins"] < betrag:
                holder["short"] = s["coins"]; return d
            s["coins"] -= betrag
            _user_data(d, guild_id, str(empfaenger.id))["coins"] += betrag
            holder["ok"] = True; return d

        await self.store.update(mutate)
        if "short" in holder:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht genug", f"Du hast nur **{holder['short']:,} {COIN_EMOJI}**."), ephemeral=True); return
        self._invalidate_lb(guild_id)
        embed = discord.Embed(
            title=f"🎁 Geschenk von {interaction.user.display_name}!",
            description=f"{empfaenger.mention} hat **{betrag:,} {COIN_EMOJI}** erhalten!",
            color=discord.Color.from_rgb(212, 172, 13),
            timestamp=discord.utils.utcnow(),
        )
        if nachricht:
            embed.add_field(name="💬 Nachricht", value=f"> {nachricht}", inline=False)
        embed.set_thumbnail(url=empfaenger.display_avatar.url)
        from utils.theme import get_footer_text
        embed.set_footer(text=f"Gesendet von {interaction.user}  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed)

    # ── /deposit ──────────────────────────────────────────────────────────────

    @eco.command(name="deposit", description="Zahle Coins auf dein Bankkonto ein.")
    @app_commands.describe(betrag="Anzahl Coins (oder 'all')")
    async def deposit(self, interaction: discord.Interaction, betrag: str):
        guild_id = str(interaction.guild.id); user_id = str(interaction.user.id); holder: dict = {}

        def mutate(d):
            e = _user_data(d, guild_id, user_id)
            amount = e["coins"] if betrag.lower() == "all" else None
            if amount is None:
                try: amount = int(betrag)
                except ValueError: holder["error"] = "Ungültiger Betrag."; return d
            if amount <= 0: holder["error"] = "Betrag muss > 0 sein."; return d
            if amount > e["coins"]: holder["error"] = f"Nur **{e['coins']:,} {COIN_EMOJI}** in bar."; return d
            e["coins"] -= amount; e["bank"] += amount
            holder["amount"] = amount; holder["coins"] = e["coins"]; holder["bank"] = e["bank"]; return d

        await self.store.update(mutate)
        if "error" in holder:
            await interaction.response.send_message(embed=error_embed("❌ Fehler", holder["error"]), ephemeral=True); return
        await interaction.response.send_message(
            embed=success_embed(f"🏦 {holder['amount']:,} {COIN_EMOJI} eingezahlt",
                                f"Bar: {holder['coins']:,} {COIN_EMOJI}  ·  Bank: {holder['bank']:,} {COIN_EMOJI}"))

    # ── /withdraw ─────────────────────────────────────────────────────────────

    @eco.command(name="withdraw", description="Hebe Coins von deinem Bankkonto ab.")
    @app_commands.describe(betrag="Anzahl Coins (oder 'all')")
    async def withdraw(self, interaction: discord.Interaction, betrag: str):
        guild_id = str(interaction.guild.id); user_id = str(interaction.user.id); holder: dict = {}

        def mutate(d):
            e = _user_data(d, guild_id, user_id)
            amount = e["bank"] if betrag.lower() == "all" else None
            if amount is None:
                try: amount = int(betrag)
                except ValueError: holder["error"] = "Ungültiger Betrag."; return d
            if amount <= 0: holder["error"] = "Betrag muss > 0 sein."; return d
            if amount > e["bank"]: holder["error"] = f"Nur **{e['bank']:,} {COIN_EMOJI}** auf der Bank."; return d
            e["bank"] -= amount; e["coins"] += amount
            holder["amount"] = amount; holder["coins"] = e["coins"]; holder["bank"] = e["bank"]; return d

        await self.store.update(mutate)
        if "error" in holder:
            await interaction.response.send_message(embed=error_embed("❌ Fehler", holder["error"]), ephemeral=True); return
        await interaction.response.send_message(
            embed=success_embed(f"🏦 {holder['amount']:,} {COIN_EMOJI} abgehoben",
                                f"Bar: {holder['coins']:,} {COIN_EMOJI}  ·  Bank: {holder['bank']:,} {COIN_EMOJI}"))

    # ── /shop ─────────────────────────────────────────────────────────────────

    @eco.command(name="shop", description="Zeigt den Coin-Shop an.")
    async def shop(self, interaction: discord.Interaction):
        embed = gold_embed("🛒 Coin-Shop", "Kaufe Items mit deinen Coins!")
        for item_id, item in SHOP_ITEMS.items():
            embed.add_field(name=f"{item['name']} — {item['price']:,} {COIN_EMOJI}",
                            value=f"`{item_id}` — {item['description']}", inline=False)
        embed.set_footer(text="Nutze /buy <item_id> um ein Item zu kaufen")
        await interaction.response.send_message(embed=embed)

    # ── /buy ──────────────────────────────────────────────────────────────────

    @eco.command(name="buy", description="Kaufe ein Item aus dem Shop.")
    @app_commands.describe(item_id="Die Item-ID aus /shop")
    async def buy(self, interaction: discord.Interaction, item_id: str):
        item = SHOP_ITEMS.get(item_id.lower())
        if not item:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht gefunden", f"`{item_id}` existiert nicht."), ephemeral=True); return

        guild_id = str(interaction.guild.id); user_id = str(interaction.user.id); holder: dict = {}

        def mutate(d):
            e = _user_data(d, guild_id, user_id)
            if e["coins"] < item["price"]:
                holder["short"] = e["coins"]; return d
            e["coins"] -= item["price"]; e.setdefault("inventory", []).append(item_id)
            holder["bal"] = e["coins"]; return d

        await self.store.update(mutate)
        if "short" in holder:
            await interaction.response.send_message(
                embed=error_embed("❌ Nicht genug", f"Brauchst **{item['price']:,}**, hast **{holder['short']:,} {COIN_EMOJI}**."),
                ephemeral=True); return
        self._invalidate_lb(guild_id)
        await interaction.response.send_message(
            embed=success_embed(f"✅ {item['name']} gekauft!",
                                f"**{item['price']:,} {COIN_EMOJI}** bezahlt.  Guthaben: **{holder['bal']:,} {COIN_EMOJI}**"))

    # ── /inventory ────────────────────────────────────────────────────────────

    @eco.command(name="inventory", description="Zeigt dein Inventar an.")
    @app_commands.describe(user="Inventar eines anderen Users (optional)")
    async def inventory(self, interaction: discord.Interaction, user: discord.Member = None):
        from collections import Counter
        target  = user or interaction.user
        data    = await self.store.read()
        ud      = data.get(str(interaction.guild.id), {}).get(str(target.id), {})
        inv     = ud.get("inventory", [])
        embed   = gold_embed(f"🎒 Inventar von {target.display_name}",
                             f"Bar: {ud.get('coins',0):,} {COIN_EMOJI}  ·  Bank: {ud.get('bank',0):,} {COIN_EMOJI}")
        embed.set_thumbnail(url=target.display_avatar.url)
        if not inv:
            embed.add_field(name="📦 Items", value="Keine Items vorhanden.", inline=False)
        else:
            lines = [f"{SHOP_ITEMS[i]['name'] if i in SHOP_ITEMS else i} ×{c}" for i, c in Counter(inv).items()]
            embed.add_field(name=f"📦 Items ({len(inv)})", value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed)

    # ── /leaderboard ──────────────────────────────────────────────────────────

    @eco.command(name="leaderboard", description="Zeigt die Top-10 der reichsten User.")
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild; guild_id = str(guild.id)
        if guild_id not in self._lb_cache:
            data = await self.store.read()
            self._lb_cache[guild_id] = sorted(
                data.get(guild_id, {}).items(),
                key=lambda x: x[1].get("coins", 0) + x[1].get("bank", 0), reverse=True)[:10]

        top = self._lb_cache[guild_id]
        if not top:
            await interaction.followup.send(embed=info_embed("📊 Leaderboard", "Noch keine Daten.")); return

        icons = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        max_t = (top[0][1].get("coins",0) + top[0][1].get("bank",0)) or 1
        lines = []
        for i, (uid, ud) in enumerate(top):
            m     = guild.get_member(int(uid))
            name  = m.display_name if m else f"Unbekannt"
            total = ud.get("coins",0) + ud.get("bank",0)
            bar   = "█" * round(total/max_t*10) + "░" * (10 - round(total/max_t*10))
            lines.append(f"{icons[i]} **{name}**\n  `{bar}` {total:,} {COIN_EMOJI}")

        embed = discord.Embed(title="💰  Coin-Leaderboard", description="\n\n".join(lines),
                              color=discord.Color.from_rgb(212,172,13), timestamp=discord.utils.utcnow())
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        from utils.theme import get_footer_text
        embed.set_footer(text=f"{get_footer_text(interaction)}  ·  Top {len(top)} nach Gesamtvermögen")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
