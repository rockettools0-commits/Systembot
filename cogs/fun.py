"""
Fun-Commands — /fun Subcommand-Gruppe (zählt als 1 Command).

  /fun joke         — Zufälliger Witz
  /fun meme         — Zufälliges Meme-Format
  /fun hug          — Umarme ein Mitglied
  /fun kiss         — Küsse ein Mitglied
  /fun slap         — Schlage ein Mitglied
  /fun pat          — Tätschle ein Mitglied
  /fun rps          — Schere-Stein-Papier
  /fun cat          — Katzen-Fakt
  /fun dog          — Hunde-Fakt
  /fun fox          — Fuchs-Fakt
  /fun ship         — Liebes-Kompatibilität
  /fun wyr          — Would You Rather
  /fun tod          — Truth or Dare
  /fun trivia       — Trivia-Frage
"""

from __future__ import annotations

import random
import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.theme import success_embed, info_embed, gold_embed, error_embed, COLOR_PRIMARY, FOOTER_TEXT, get_footer_text

JOKES = [
    ("Ich bin auf Diät.", "Seitdem schaue ich die Waage schon seit 3 Stunden an und warte, dass sie abnimmt."),
    ("Warum können Geister so schlecht lügen?", "Weil man durch sie hindurchsehen kann."),
    ("Wie nennt man einen Hund ohne Beine?", "Egal, er kommt sowieso nicht, wenn man ihn ruft."),
    ("Warum kann man einem Informatiker Weihnachten und Halloween nicht unterscheiden?", "Weil Oct 31 = Dec 25 ist."),
    ("Was sagt ein Pixel zu einem anderen?", "Wir stehen uns so nah!"),
    ("Wie heißt ein Bär ohne Zähne?", "Ein Gummibär."),
    ("Was ist schwerer: eine Tonne Eisen oder eine Tonne Federn?", "Gleich schwer — aber die Federn nehmen mehr Platz weg."),
    ("Warum nehmen Informatiker immer eine Leiter mit?", "Für die hohen Sprachen."),
    ("Wie nennt man einen Schneemann im Sommer?", "Pfütze."),
    ("Was sagt ein Mathematiker beim Anblick einer leeren Tafel?", "Keine Fragen? Perfekt, dann ist alles gelöst."),
    ("Wie nennt man einen gut gelaunten Informatiker?", "Syntax-Error: unexpected token 'smile'."),
    ("Warum hat der Skelett-Koch aufgehört zu kochen?", "Er hatte kein Herz mehr dabei."),
]
CAT_FACTS = [
    "Katzen schlafen zwischen 12 und 16 Stunden am Tag.",
    "Eine Katze kann bis zu 180° drehen — mehr als jeder andere Säuger.",
    "Katzen können keinen süßen Geschmack wahrnehmen.",
    "Das Schnurren einer Katze kann Knochen heilen.",
    "Katzen haben 32 Muskeln in jedem Ohr.",
    "Eine Gruppe Katzen heißt 'Clowder'.",
    "Hauskatzen verbringen 30–50% des Tages damit, sich zu pflegen.",
    "Katzen können bis zu 5× ihre eigene Körperlänge springen.",
    "Die Schnurrhaare einer Katze sind genau so breit wie ihr Körper.",
]
DOG_FACTS = [
    "Hunde können riechen, ob ein Mensch krank ist.",
    "Hunde haben 300 Millionen Geruchsrezeptoren — Menschen nur 6 Millionen.",
    "Ein Welpe schläft bis zu 20 Stunden am Tag.",
    "Der Dalmatiner ist als Welpe ganz weiß — die Flecken kommen erst mit der Zeit.",
    "Hunde haben drei Augenlider.",
    "Der Basenji ist der einzige Hund, der nicht bellt — er jodelt.",
    "Hunde träumen genau wie Menschen.",
    "Die Nase eines Hundes ist so einzigartig wie ein menschlicher Fingerabdruck.",
]
FOX_FACTS = [
    "Füchse sind die einzigen Hundeartigen, die Bäume klettern können.",
    "Eine Gruppe Füchse heißt 'Skulk' oder 'Earth'.",
    "Füchse kommunizieren mit über 40 verschiedenen Lauten.",
    "Der Polarfuchs kann Temperaturen bis -70°C überleben.",
    "Füchse nutzen das Erdmagnetfeld zur Jagd.",
    "Ein Fuchs kann bis zu 6 Meter weit springen.",
    "Weibliche Füchse heißen 'Vixen'.",
    "Füchse können bis zu 10 Jahre alt werden.",
]
WOULD_YOU_RATHERS = [
    ("Für immer fliegen können", "Für immer unsichtbar sein"),
    ("Immer die Wahrheit sagen müssen", "Immer lügen müssen"),
    ("Kein Internet für 1 Jahr", "Kein Handy für 1 Jahr"),
    ("In der Vergangenheit leben", "In der Zukunft leben"),
    ("Jede Sprache sprechen", "Jedes Instrument spielen"),
    ("10 Minuten in der Vergangenheit reisen", "10 Minuten in die Zukunft sehen"),
    ("Supergeschwindigkeit haben", "Superstärke haben"),
    ("Nie schlafen müssen", "Nie essen müssen"),
    ("Immer Sommer", "Immer Winter"),
    ("Einmal täglich eine Fähigkeit kopieren", "Einmal täglich unsichtbar sein"),
    ("Im Meer schwimmen mit Haien", "Allein im Wald mit Wölfen"),
]
TRUTHS = [
    "Was war dein peinlichster Moment auf Discord?",
    "Wen auf diesem Server findest du am coolsten?",
    "Was ist dein größtes Online-Gaming-Geheimnis?",
    "Hast du schon mal jemanden mit einem Fake-Account bespitzelt?",
    "Was ist das Cringeligste, was du je online geschrieben hast?",
    "Welches Spiel hast du heimlich gemocht, aber nie zugegeben?",
    "Wie lange warst du heute schon online?",
    "Was war dein lustigstes Discord-Missverständnis?",
]
DARES = [
    "Schreib den nächsten 3 Nachrichten in Großbuchstaben.",
    "Ändere deinen Nickname für 10 Minuten zu 'Kartoffelkönig'.",
    "Schreib in 3 Kanälen ein Herz-Emoji.",
    "Sende eine Nachricht nur aus Emojis.",
    "Beschreibe dich selbst in genau 5 Wörtern.",
    "Schreib 'Ich liebe diesen Server' in umgekehrter Reihenfolge.",
    "Stelle jemandem auf dem Server eine ernste Frage.",
    "Schreib für die nächsten 2 Minuten nur auf Englisch.",
]
TRIVIA_QUESTIONS: list[dict] = [
    {"q": "Wie viele Planeten hat unser Sonnensystem?",           "a": "8",      "choices": ["6","7","8","9"]},
    {"q": "Welche Farbe hat reines Gold?",                        "a": "Gelb",   "choices": ["Silber","Gelb","Orange","Weiß"]},
    {"q": "Was ist die Hauptstadt von Japan?",                    "a": "Tokio",  "choices": ["Osaka","Tokio","Kyoto","Hiroshima"]},
    {"q": "Wie viele Seiten hat ein Würfel?",                     "a": "6",      "choices": ["4","5","6","8"]},
    {"q": "Welches ist das schwerste natürliche Element?",        "a": "Osmium", "choices": ["Gold","Blei","Osmium","Uran"]},
    {"q": "In welchem Jahr wurde Minecraft veröffentlicht?",      "a": "2011",   "choices": ["2009","2010","2011","2012"]},
    {"q": "Wie heißt das schnellste Landlebewesen?",              "a": "Gepard", "choices": ["Löwe","Gepard","Greyhound","Pronghorn"]},
    {"q": "Wie viele Bytes hat ein Kilobyte?",                    "a": "1024",   "choices": ["100","1000","1024","2048"]},
    {"q": "Welche Sprache wurde von Guido van Rossum entwickelt?","a": "Python", "choices": ["Java","Python","C++","Ruby"]},
    {"q": "Was ist das größte Organ des menschlichen Körpers?",   "a": "Haut",   "choices": ["Leber","Haut","Herz","Lunge"]},
]
RPS_EMOJIS = {"stein": "🪨", "papier": "📄", "schere": "✂️"}
RPS_WINS   = {"stein": "schere", "papier": "stein", "schere": "papier"}


# ── Views ─────────────────────────────────────────────────────────────────────

class TriviaView(discord.ui.View):
    def __init__(self, question: dict, asker_id: int):
        super().__init__(timeout=30)
        self.question = question
        self.answered: set[int] = set()
        for choice in random.sample(question["choices"], len(question["choices"])):
            btn = discord.ui.Button(label=choice, style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(choice)
            self.add_item(btn)

    def _make_cb(self, choice: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id in self.answered:
                return await interaction.response.send_message("Du hast bereits geantwortet!", ephemeral=True)
            self.answered.add(interaction.user.id)
            correct = choice == self.question["a"]
            msg = "✅ Richtig!" if correct else f"❌ Falsch! Die Antwort war **{self.question['a']}**."
            await interaction.response.send_message(msg, ephemeral=True)
        return cb

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
            if isinstance(item, discord.ui.Button) and item.label == self.question["a"]:
                item.style = discord.ButtonStyle.success


class WouldYouRatherView(discord.ui.View):
    def __init__(self, opt_a: str, opt_b: str):
        super().__init__(timeout=60)
        self.votes   = {"A": set(), "B": set()}
        self._opt_a  = opt_a
        self._opt_b  = opt_b

    @discord.ui.button(label="🅰  Option A", style=discord.ButtonStyle.blurple)
    async def vote_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.votes["B"].discard(uid); self.votes["A"].add(uid)
        a, b = len(self.votes["A"]), len(self.votes["B"])
        await interaction.response.send_message(f"✅ Du hast **{self._opt_a}** gewählt!  ({a} vs {b})", ephemeral=True)

    @discord.ui.button(label="🅱  Option B", style=discord.ButtonStyle.secondary)
    async def vote_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        self.votes["A"].discard(uid); self.votes["B"].add(uid)
        a, b = len(self.votes["A"]), len(self.votes["B"])
        await interaction.response.send_message(f"✅ Du hast **{self._opt_b}** gewählt!  ({a} vs {b})", ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Subcommand-Gruppe ─────────────────────────────────────────────────────────

class FunGroup(app_commands.Group, name="fun", description="Spaß und Entertainment."):
    pass


# ── Cog ───────────────────────────────────────────────────────────────────────

class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    fun = FunGroup()

    @fun.command(name="joke", description="Erhalte einen zufälligen Witz.")
    async def joke(self, interaction: discord.Interaction):
        setup_txt, punchline = random.choice(JOKES)
        embed = discord.Embed(title="😄 Witz", color=COLOR_PRIMARY,
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name=setup_txt, value=f"||{punchline}||", inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    @fun.command(name="meme", description="Zufälliges Meme-Format.")
    async def meme(self, interaction: discord.Interaction):
        FORMATS = ["Drake Pointing","Distracted Boyfriend","Two Buttons","This Is Fine",
                   "Expanding Brain","Surprised Pikachu","Crying Cat","Stonks",
                   "Uno Reverse Card","Galaxy Brain","Change My Mind","Gru's Plan",
                   "Buff Doge vs Cheems","Left Exit 12","Mocking Spongebob","Always Has Been"]
        embed = gold_embed("🐸 Meme-Format des Tages", f"**{random.choice(FORMATS)}**\n\nJetzt weißt du welches Meme du heute posten sollst! 😄")
        await interaction.response.send_message(embed=embed)

    @fun.command(name="hug", description="Umarme ein Mitglied.")
    @app_commands.describe(user="Das Mitglied")
    async def hug(self, interaction: discord.Interaction, user: discord.Member):
        if user.id == interaction.user.id:
            return await interaction.response.send_message(
                embed=info_embed("🤗 Selbstumarmung", "Du hast dich selbst umarmt. Das ist auch okay! 💙"), ephemeral=True)
        embed = success_embed("🤗 Umarmung!", f"**{interaction.user.display_name}** umarmt **{user.display_name}**! 💙")
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @fun.command(name="kiss", description="Küsse ein Mitglied.")
    @app_commands.describe(user="Das Mitglied")
    async def kiss(self, interaction: discord.Interaction, user: discord.Member):
        embed = discord.Embed(title="💋 Kuss!",
                              description=f"**{interaction.user.display_name}** küsst **{user.display_name}**! 💋",
                              color=discord.Color.from_rgb(255,105,180),
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_thumbnail(url=user.display_avatar.url); embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    @fun.command(name="slap", description="Schlage ein Mitglied.")
    @app_commands.describe(user="Das Mitglied")
    async def slap(self, interaction: discord.Interaction, user: discord.Member):
        embed = discord.Embed(title="👋 Klatsch!",
                              description=f"**{interaction.user.display_name}** haut **{user.display_name}**! 💥",
                              color=discord.Color.from_rgb(231,76,60),
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_thumbnail(url=user.display_avatar.url); embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    @fun.command(name="pat", description="Tätschle ein Mitglied.")
    @app_commands.describe(user="Das Mitglied")
    async def pat(self, interaction: discord.Interaction, user: discord.Member):
        embed = success_embed("🥺 Kopftätscheln!", f"**{interaction.user.display_name}** tätschelt **{user.display_name}**! 🤝")
        embed.set_thumbnail(url=user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @fun.command(name="rps", description="Spiele Schere-Stein-Papier gegen den Bot.")
    @app_commands.describe(wahl="Deine Wahl")
    @app_commands.choices(wahl=[
        app_commands.Choice(name="🪨 Stein",  value="stein"),
        app_commands.Choice(name="📄 Papier", value="papier"),
        app_commands.Choice(name="✂️ Schere", value="schere"),
    ])
    async def rps(self, interaction: discord.Interaction, wahl: str):
        bot_choice = random.choice(["stein","papier","schere"])
        if wahl == bot_choice:             result, color = "🤝 Unentschieden!", discord.Color.from_rgb(52,152,219)
        elif RPS_WINS[wahl] == bot_choice: result, color = "🎉 Du gewinnst!",   discord.Color.from_rgb(46,204,113)
        else:                              result, color = "😢 Der Bot gewinnt!", discord.Color.from_rgb(231,76,60)
        embed = discord.Embed(title=f"✂️ RPS — {result}", color=color,
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name=f"Du: {RPS_EMOJIS[wahl]}",       value=wahl.title(),       inline=True)
        embed.add_field(name="VS",                              value="⚡",               inline=True)
        embed.add_field(name=f"Bot: {RPS_EMOJIS[bot_choice]}", value=bot_choice.title(), inline=True)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    @fun.command(name="cat", description="Zufälliger Katzen-Fakt.")
    async def cat(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=info_embed("🐱 Katzen-Fakt", random.choice(CAT_FACTS)))

    @fun.command(name="dog", description="Zufälliger Hunde-Fakt.")
    async def dog(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=info_embed("🐶 Hunde-Fakt", random.choice(DOG_FACTS)))

    @fun.command(name="fox", description="Zufälliger Fuchs-Fakt.")
    async def fox(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=info_embed("🦊 Fuchs-Fakt", random.choice(FOX_FACTS)))

    @fun.command(name="ship", description="Berechne die Liebes-Kompatibilität zweier User.")
    @app_commands.describe(user1="Erster User", user2="Zweiter User (optional, sonst du)")
    async def ship(self, interaction: discord.Interaction, user1: discord.Member,
                   user2: discord.Member = None):
        t2   = user2 or interaction.user
        seed = (min(user1.id, t2.id) * 100003 + max(user1.id, t2.id)) % 101
        pct  = seed
        if pct < 25:   label, color = "💔 Nicht wirklich...", discord.Color.from_rgb(235,77,75)
        elif pct < 50: label, color = "🤔 Vielleicht...",     discord.Color.from_rgb(243,156,18)
        elif pct < 75: label, color = "💛 Gute Chancen!",     discord.Color.from_rgb(241,196,15)
        elif pct < 90: label, color = "💚 Sehr kompatibel!",  discord.Color.from_rgb(88,214,141)
        else:          label, color = "💘 Perfektes Match!",   discord.Color.from_rgb(255,100,180)
        bar = "💗" * round(pct/10) + "🖤" * (10 - round(pct/10))
        ship_name = user1.display_name[:len(user1.display_name)//2] + t2.display_name[len(t2.display_name)//2:]
        embed = discord.Embed(title=f"💕 Ship: {ship_name}",
                              description=f"**{user1.mention}** 💕 **{t2.mention}**",
                              color=color, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="❤️ Kompatibilität", value=f"`{bar}` **{pct}%**", inline=False)
        embed.add_field(name="Urteil",             value=label,                 inline=False)
        embed.set_footer(text=get_footer_text(interaction))
        await interaction.response.send_message(embed=embed)

    @fun.command(name="wyr", description="Stellt eine Would-You-Rather Frage.")
    async def wyr(self, interaction: discord.Interaction):
        opt_a, opt_b = random.choice(WOULD_YOU_RATHERS)
        embed = discord.Embed(title="🤔 Would You Rather?",
                              color=discord.Color.from_rgb(130,80,255),
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="🅰  Option A", value=opt_a, inline=True)
        embed.add_field(name="🅱  Option B", value=opt_b, inline=True)
        embed.set_footer(text=f"Gestartet von {interaction.user}  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed, view=WouldYouRatherView(opt_a, opt_b))

    @fun.command(name="tod", description="Truth or Dare — zufällig oder per Wahl.")
    @app_commands.describe(wahl="Truth, Dare oder zufällig")
    @app_commands.choices(wahl=[
        app_commands.Choice(name="🎲 Zufällig", value="random"),
        app_commands.Choice(name="💬 Truth",    value="truth"),
        app_commands.Choice(name="🎯 Dare",     value="dare"),
    ])
    async def tod(self, interaction: discord.Interaction, wahl: str = "random"):
        if wahl == "random": wahl = random.choice(["truth","dare"])
        if wahl == "truth":
            content, title, color = random.choice(TRUTHS), "💬 Truth", discord.Color.from_rgb(88,101,242)
        else:
            content, title, color = random.choice(DARES), "🎯 Dare", discord.Color.from_rgb(235,77,75)
        embed = discord.Embed(title=title, description=content, color=color,
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_footer(text=f"Für {interaction.user.display_name}  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed)

    @fun.command(name="trivia", description="Beantworte eine Trivia-Frage (30 Sek.).")
    async def trivia(self, interaction: discord.Interaction):
        q    = random.choice(TRIVIA_QUESTIONS)
        view = TriviaView(q, interaction.user.id)
        embed = discord.Embed(title="🧠 Trivia-Frage",
                              description=f"**{q['q']}**\n\nDu hast **30 Sekunden** zum Antworten!",
                              color=discord.Color.from_rgb(130,80,255),
                              timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.set_footer(text=f"Gestartet von {interaction.user.display_name}  ·  {get_footer_text(interaction)}")
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
