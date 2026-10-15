import asyncio
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui_kit

PREFIX = "lktrivia"
PAUSE = 5  # seconds between the answer and the next question

# question, right answer, wrong answers, why
SAFETY = [
    ("Someone DMs you: \"I accidentally reported your account, message this Discord staff member to fix it.\" "
     "What is it?",
     "A scam to take your account", ["A real warning from Discord", "A bug, just ignore it"],
     "Discord staff never message you from a normal account. Block and report it."),
    ("A friend sends you a QR code and says scan it for free Nitro. What can happen?",
     "They log into your account", ["You get free Nitro", "Nothing, QR codes are safe"],
     "Scanning it with the Discord app signs them in as you. Only scan codes you made yourself."),
    ("Someone says they'll share a photo of you unless you pay. What do you do first?",
     "Don't pay, keep the chat, tell someone", ["Pay so they go away", "Delete everything right away"],
     "Paying almost never makes them stop. Keep the chat as proof, report it, then block."),
    ("What does Take It Down do?",
     "Helps remove pics of you taken under 18", ["Deletes your Discord account", "Takes down whole servers"],
     "It makes a fingerprint of the photo on your phone. The photo itself never leaves your phone."),
    ("Which one is a red flag from someone you only know online?",
     "\"You're so mature for your age\"", ["\"GG, want to play again?\"", "\"What game are you playing?\""],
     "It's a common line to make a teen feel older and special. Be careful with anyone who says it."),
    ("How old do you have to be to use Discord?",
     "13, or older in some countries", ["10", "16 everywhere"],
     "It's 13 in most places, but some countries set it higher."),
    ("Someone asks you to paste code into your browser console (Ctrl+Shift+I) for a cool trick. What happens?",
     "They can steal your account", ["You unlock a secret theme", "Nothing, it's safe"],
     "That code can grab your login token. Discord even warns you about it in the console."),
    ("Where can DMs from people who aren't your friends end up?",
     "Message Requests", ["Your main DM list, always", "They get deleted"],
     "You don't have to open them. You can ignore or delete them."),
    ("What does 2FA do?",
     "Asks for a code when someone logs in", ["Hides your messages", "Blocks spam DMs"],
     "Even if someone gets your password, they still need the code from your phone."),
    ("Someone keeps asking you to move the chat to another app. Why's that a red flag?",
     "Nobody there can see or stop them", ["Other apps are faster", "It isn't, it's normal"],
     "Pulling you somewhere private is a common move. You can just say no."),
    ("Will Discord ever ask for your password in a DM?",
     "Never", ["Only if your account is in trouble", "Yes, to check it's you"],
     "Anyone asking for your password is trying to take your account."),
    ("A friend who never talks to you sends a \"free Nitro\" link. What's most likely?",
     "Their account got hacked", ["They're feeling nice", "Discord is giving out Nitro"],
     "Hacked accounts send scam links to everyone on the friend list. Tell your friend another way."),
    ("If someone online makes you feel uncomfortable, whose fault is it?",
     "Never yours", ["Yours, for replying", "Nobody's"],
     "You won't get in trouble for someone else's choices. Talk to an adult you trust."),
    ("What can Discord's Family Center show a parent?",
     "Who you added and messaged, not what you said", ["Every message you send", "Nothing at all"],
     "Parents can see things like new friends and servers, but never the messages."),
    ("Which one should you keep off your profile?",
     "Your school's name", ["Your favorite game", "Your pronouns"],
     "School, city and real name make it easy to find you offline."),
    ("A stranger offers you Nitro for a pic of you. What do you do?",
     "Report and block them", ["Send one, it's just a pic", "Ignore it and keep chatting"],
     "Gifts for pics is a big red flag. You won't get in trouble for reporting it."),
    ("What happens when you block someone?",
     "They can't DM you anymore", ["They get banned from Discord", "Discord tells them you blocked them"],
     "They aren't told, and you can unblock them anytime."),
    ("Someone in a voice call asks if you're home alone. What do you do?",
     "Don't answer and leave the call", ["Say yes, it's just a question", "Tell them when your parents get back"],
     "There's no good reason to ask that. You don't owe anyone a reply."),
    ("How old do you have to be to open age-restricted channels?",
     "18", ["16", "13"],
     "Age-restricted channels are for adults only."),
    ("Does reporting someone tell them who reported?",
     "No, reports are private", ["Yes, they see your name", "Only if they ask Discord"],
     "Discord doesn't tell people who reported them. Report as much as you need."),
    ("What's doxxing?",
     "Posting someone's private info online", ["A kind of Discord bot", "Reporting someone"],
     "Things like a real name, address or school. Never post yours or anyone else's."),
    ("Someone you met online wants your chats kept secret. What does that mean?",
     "It's a red flag, tell someone", ["They're just shy", "They really trust you"],
     "Safe people don't need you to hide things from your parents or friends."),
]

LOOKOUT = [
    ("Does Lookout read your DMs?",
     "No, only a DM you report", ["Yes, all of them", "Only on weekends"],
     "It only sees a DM if you report it, and it never joins voice channels."),
    ("What animal is Looky?",
     "A meerkat", ["An owl", "A ferret"],
     "Meerkats take turns keeping watch so the rest can relax. That's the whole idea."),
    ("How do you send a DM you got to a server's mods?",
     "Right-click it > Apps > Report Message to Mods", ["Forward it to #general", "Screenshot it to Lookout"],
     "Add Lookout to your apps first. It asks which server's mods should see it."),
    ("Who can see a report you send with /report?",
     "Only the mods", ["Everyone in the server", "The person you reported"],
     "Reports go straight to the mod channel. Nobody else sees them."),
    ("An adult and a teen are chatting about a game. What does Lookout do?",
     "Nothing, normal chat is fine", ["Removes the messages", "Warns the adult"],
     "It only steps in for inappropriate messages or contact info sent to a teen."),
    ("An adult sends a teen their phone number in chat. What happens?",
     "It's removed and the mods are told", ["Nothing", "The adult gets banned"],
     "The teen gets a heads-up too, with a Tell the Mods button."),
    ("An adult asks a teen where they live. How does the alert show up for the mods?",
     "Marked urgent", ["Low priority", "It doesn't, that's normal"],
     "Asking for a home address or to meet up gets marked urgent."),
    ("Can Lookout ban or kick people?",
     "No, the mods make every call", ["Yes, on its own", "Only people with no roles"],
     "It doesn't need Administrator and can't ban, kick or manage roles."),
    ("What does Teen by Default mean?",
     "No 18+ role means you're treated as a teen", ["Everyone gets a teen role", "Only teens can talk"],
     "Nobody gets a role that shows they're under 18."),
    ("Where should a server give out the 18+ role or button?",
     "In an age-restricted channel", ["In #general", "In DMs"],
     "Only adults Discord has verified can open those channels."),
    ("Who can see that you pressed Lookout's invisible 18+ button?",
     "Nobody", ["Everyone in the server", "Only your friends"],
     "No role and nothing on your profile. Lookout just remembers it."),
    ("Lookout got one wrong. What do the mods press?",
     "False Alarm", ["Undo Everything", "Report Lookout"],
     "It closes the case and takes back what that message counted for."),
    ("When does the weekly summary come out?",
     "Every Monday", ["Every Friday", "On the 1st of the month"],
     "It goes to the mod channel with the week in numbers and charts."),
    ("What happens to a server's data when it removes Lookout?",
     "It's deleted right away", ["It's kept forever", "It's kept for a year"],
     "Everything for that server is deleted the moment Lookout leaves."),
    ("An adult gets reported by 2 different teens. What does Lookout do?",
     "Keeps them away from teens until the mods decide", ["Bans them", "Nothing"],
     "Their replies to teens get removed until a mod lifts it or keeps it."),
    ("What does the Safety Checkup look at?",
     "Your server's own Discord settings", ["Every member's DMs", "Your internet speed"],
     "Things like Discord's image filter and verification level, and how to fix them."),
    ("How much does Lookout cost?",
     "Nothing, it's free", ["$5 a month", "Free for small servers only"],
     "Every feature, for every server."),
    ("Someone says they're 14 but has an adult role. What does Lookout do?",
     "Gives the mods a heads-up", ["Changes their role", "Kicks them"],
     "Lookout can't change roles, so it's just a note for the mods."),
    ("What does Export Case give the mods?",
     "A text file of the whole case", ["A screenshot", "The member's DMs"],
     "Who was in it, what happened and everything the mods did, in one file."),
    ("How can you see everything Lookout does with made-up data?",
     "Run /demo", ["Run /setup", "Run /summary"],
     "Only you see it, and nothing you press affects anyone."),
    ("A teen posts a photo of themselves in chat. What does Lookout do?",
     "DMs them a tip that it's public", ["Deletes it", "Tells the mods"],
     "Just a heads-up that anyone can save it, and how to delete it."),
    ("Someone sets their nickname to \"mia 14\". What does Lookout do?",
     "DMs them a tip to change it", ["Bans them", "Nothing"],
     "Ages in names make teens easy to find."),
]

TOPICS = {"safety": SAFETY, "lookout": LOOKOUT, "both": SAFETY + LOOKOUT}


class Game:
    def __init__(self, questions: list, seconds: int, title: str):
        self.id = random.randrange(1 << 30)
        self.questions = questions
        self.seconds = seconds
        self.title = title
        self.number = 0           # question we're on, from 1
        self.open = False
        self.options: list[str] = []
        self.right = 0
        self.ends = 0
        self.picks: dict[int, int] = {}
        self.points: dict[int, int] = {}
        self.players: set[int] = set()


def _points(n: int) -> str:
    return f"{n} point{'s' * (n != 1)}"


# same points, same place
def _ranked(rows) -> str:
    lines, rank, last = [], 0, None
    for n, (user, points) in enumerate(rows, 1):
        if points != last:
            rank, last = n, points
        lines.append(f"**{rank}.** <@{user}> · {_points(points)}")
    return "\n".join(lines)


def _names(ids: list[int], limit: int = 10) -> str:
    names = ", ".join(f"<@{i}>" for i in ids[:limit])
    return names + (f" and {len(ids) - limit} more" if len(ids) > limit else "")


def question_view(game: Game) -> discord.ui.LayoutView:
    text = game.questions[game.number - 1][0]
    buttons = [discord.ui.Button(label=option, custom_id=f"{PREFIX}:{game.id}:{game.number}:{i}")
               for i, option in enumerate(game.options)]
    return ui_kit.card(ui_kit.titled(ui_kit.mascot("peek"), game.title), (None, [f"## {text}", *buttons]),
                       subtitle=f"Question {game.number} of {len(game.questions)} · ends <t:{game.ends}:R>")


def answer_view(game: Game) -> discord.ui.LayoutView:
    text, answer, _, why = game.questions[game.number - 1]
    right = [user for user, pick in game.picks.items() if pick == game.right]
    if right:
        result = f"**{len(right)} of {len(game.picks)} got it:** {_names(right)}"
    else:
        result = "**Nobody got this one.**" if game.picks else "**Nobody answered.**"
    return ui_kit.card(ui_kit.titled(ui_kit.mascot("thumbsup" if right else "shock"), game.title),
                       (None, f"## {text}\n**Answer:** {answer}\n-# {why}"), result,
                       subtitle=f"Question {game.number} of {len(game.questions)}")


def results_view(game: Game) -> discord.ui.LayoutView:
    top = sorted(game.points.items(), key=lambda p: -p[1])[:10]
    players = len(game.players)
    if top:
        text = _ranked(top)
    else:
        text = "Nobody got any right this time. Next round!" if players else "Nobody played this time."
    return ui_kit.card(ui_kit.titled(ui_kit.mascot("party"), "Trivia Results"), text,
                       subtitle=f"{len(game.questions)} questions · {players} player{'s' * (players != 1)}",
                       buttons=[discord.ui.Button(label="Leaderboard", custom_id=f"{PREFIX}:top")])


def leaderboard_view(guild_id: int, user_id: int) -> discord.ui.LayoutView:
    rank, points = db.trivia_rank(guild_id, user_id)
    you = f"You're #{rank} with {_points(points)}." if points else "You don't have any points yet."
    return ui_kit.card("Trivia Leaderboard", _ranked(db.trivia_top(guild_id)) or "Nobody has points yet.",
                       subtitle="All time", footer=you)


@app_commands.describe(questions="How many questions (default 5)", seconds="Seconds for each question (default 20)",
                       topic="What the questions are about (default both)")
@app_commands.choices(topic=[app_commands.Choice(name="Staying safe online", value="safety"),
                             app_commands.Choice(name="Lookout", value="lookout"),
                             app_commands.Choice(name="Both", value="both")])
async def trivia_command(interaction: discord.Interaction, questions: app_commands.Range[int, 1, 10] = 5,
                         seconds: app_commands.Range[int, 10, 60] = 20, topic: str = "both"):
    cog = interaction.client.get_cog("Trivia")
    channel = interaction.channel
    if channel.id in cog.games:
        return await interaction.response.send_message(view=ui_kit.note("There's already a game going in here."),
                                                       ephemeral=True)
    perms = channel.permissions_for(interaction.guild.me)
    if not (perms.view_channel and perms.send_messages):
        return await interaction.response.send_message(view=ui_kit.note(
            "Lookout can't post here. It needs **Send Messages** in this channel."), ephemeral=True)
    pool = TOPICS.get(topic, TOPICS["both"])
    game = Game(random.sample(pool, min(questions, len(pool))), seconds,
                "Lookout Trivia" if topic == "lookout" else "Safety Trivia")
    cog.games[channel.id] = game
    await interaction.response.send_message(view=ui_kit.note(
        f"**Trivia started.** {len(game.questions)} questions, {seconds} seconds each."), ephemeral=True)
    try:
        await cog.play(game, channel)
    finally:
        cog.games.pop(channel.id, None)


# only synced to the support server (bot.py)
COMMAND = app_commands.Command(name="trivia", description="Start a safety trivia game in this channel",
                               callback=trivia_command)
COMMAND.default_permissions = discord.Permissions(manage_messages=True)
COMMAND.guild_only = True


class Trivia(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: dict[int, Game] = {}  # channel id -> game
        self.owner_command = COMMAND

    async def play(self, game: Game, channel) -> None:
        quiet = discord.AllowedMentions.none()
        for number, (_, answer, wrong, _) in enumerate(game.questions, 1):
            game.number = number
            game.options = [answer, *wrong]
            random.shuffle(game.options)
            game.right = game.options.index(answer)
            game.picks = {}
            game.ends = int(time.time()) + game.seconds
            try:
                message = await channel.send(view=question_view(game), allowed_mentions=quiet)
            except discord.HTTPException:
                return
            game.open = True
            await asyncio.sleep(game.seconds)
            game.open = False

            right = [user for user, pick in game.picks.items() if pick == game.right]
            for user in right:
                game.points[user] = game.points.get(user, 0) + 1
            db.add_trivia_points(channel.guild.id, right)
            try:
                await message.edit(view=answer_view(game), allowed_mentions=quiet)
            except discord.HTTPException:
                pass
            if number < len(game.questions):
                await asyncio.sleep(PAUSE)
        try:
            await channel.send(view=results_view(game), allowed_mentions=quiet)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        parts = (interaction.data or {}).get("custom_id", "").split(":")
        if parts[0] != PREFIX or len(parts) < 2:
            return
        user = interaction.user
        if parts[1] == "top":
            return await interaction.response.send_message(view=leaderboard_view(interaction.guild_id, user.id),
                                                           ephemeral=True,
                                                           allowed_mentions=discord.AllowedMentions.none())
        try:
            game_id, number, pick = int(parts[1]), int(parts[2]), int(parts[3])
        except (ValueError, IndexError):
            return
        game = self.games.get(interaction.channel_id)
        if not game or game.id != game_id or game.number != number or not game.open or pick >= len(game.options):
            return await interaction.response.send_message(view=ui_kit.note("This question's over."), ephemeral=True)
        if user.id in game.picks:
            return await interaction.response.send_message(view=ui_kit.note(
                f"You already picked **{game.options[game.picks[user.id]]}**."), ephemeral=True)
        game.picks[user.id] = pick
        game.players.add(user.id)
        await interaction.response.send_message(view=ui_kit.note(
            f"**Locked in:** {game.options[pick]}\n-# The answer shows <t:{game.ends}:R>."), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Trivia(bot))
