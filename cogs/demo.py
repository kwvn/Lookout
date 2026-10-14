import asyncio
import datetime as dt
import io

import discord
from discord import app_commands
from discord.ext import commands

import chart
import config
import ui_kit

PREFIX = "lkdemo"
PAGES = 8
CLAIMED, WARNED, TIMED_OUT, DISCONNECTED, RESOLVED = 1, 2, 4, 8, 16  # bit flags for the example alert

WEEK = dict(reports=47, images=12, warnings=18, timeouts=9, disconnects=6, opened=31, resolved=28, avg_seconds=14 * 60, still_open=3, daily=[4, 6, 3, 8, 11, 9, 6],
            types=[("Harassment", 16), ("Inappropriate content", 13), ("Threats or self-harm", 7), ("Scam or doxxing", 6),
                   ("Other", 5)])
WEEK_BEFORE = dict(WEEK, reports=39, avg_seconds=22 * 60, daily=[5, 3, 6, 4, 9, 8, 4])
STAFF = [dict(name="Kai", avatar=None, cases=41, avg_seconds=9 * 60, actions=57),
         dict(name="Sam", avatar=None, cases=33, avg_seconds=14 * 60, actions=38),
         dict(name="Noor", avatar=None, cases=18, avg_seconds=21 * 60, actions=12),
         dict(name="Leo", avatar=None, cases=7, avg_seconds=26 * 60, actions=4)]
STAFF_TOTALS = dict(resolved=99, opened=112, resolve_avg=17 * 60, claim_avg=3 * 60 + 20)


def _nav(page: int) -> list:
    row = [None]
    if page > 1:
        row.append(discord.ui.Button(label="Back", custom_id=f"{PREFIX}:go:{page - 1}"))
    if page < PAGES:
        row.append(discord.ui.Button(label="Next", custom_id=f"{PREFIX}:go:{page + 1}"))
    return row


def _step(page: int) -> str:
    return f"Step {page} of {PAGES}"


def _card(page: int, name: str, *sections, subtitle: str | None = None, **kwargs):
    sub = f"**{name}** · {_step(page)}" + (f" · {subtitle}" if subtitle else "")
    sub = ui_kit.titled(ui_kit.mascot("watching"), sub)
    return ui_kit.card("Demo", *sections, subtitle=sub, icon="brand", **kwargs)


def _picture_item(file: discord.File) -> discord.ui.MediaGallery:
    return discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{file.filename}"))


# the #demo card in the support server

START = "lkhelp:demo"  # handled in help.py


def demo_card(bot, banner: bool = True) -> discord.ui.LayoutView:
    invite = discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions(config.INVITE_PERMISSIONS),
                                     scopes=("bot", "applications.commands"))
    start = discord.ui.Button(label="Start the Demo", custom_id=START)
    return ui_kit.card(
        "Try Lookout",
        ("How It Works", [discord.ui.Section(
            "Press **Start the Demo** for a private tour with made-up names and example data.\n"
            "-# Only you can see it · Nothing you press affects anyone", accessory=start)]),
        ("What You'll See", "1. How members report a problem, even a DM they got\n2. The alert your mods get. Try the "
                            "buttons.\n3. What the Message Filter and Contact Info Filter catch\n4. The heads-up a teen "
                            "gets when a message to them is removed\n5. What your mods get from teen safety\n6. The "
                            "weekly summary\n7. Mod Stats\n8. How to add Lookout to your server"),
        ("Add It to Your Server", ["Add Lookout, then run /setup. It takes about a minute.",
                                   discord.ui.Button(label="Add Lookout to Your Server", style=discord.ButtonStyle.link,
                                                     url=invite)]),
        subtitle="See everything Lookout does in about two minutes, without adding it to your server.", icon="brand",
        banner=banner)


def _has_start_button(message: discord.Message) -> bool:
    try:
        view = discord.ui.LayoutView.from_message(message, timeout=None)
    except Exception:
        return False
    return any(getattr(item, "custom_id", None) == START for item in view.walk_children())


async def post_demo(interaction: discord.Interaction):
    bot, channel = interaction.client, interaction.channel
    if not await bot.is_owner(interaction.user):
        return await interaction.response.send_message(view=ui_kit.note("Only Lookout's owner can use this."),
                                                       ephemeral=True)
    perms = channel.permissions_for(interaction.guild.me)
    if not (perms.view_channel and perms.send_messages):
        return await interaction.response.send_message(view=ui_kit.note(
            "Lookout can't post here. It needs **Send Messages** in this channel."), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    view = demo_card(bot, banner=perms.attach_files)
    if perms.read_message_history:
        async for message in channel.history(limit=50):
            if message.author.id == bot.user.id and _has_start_button(message):
                await message.edit(view=view, attachments=ui_kit.files(view))
                return await interaction.followup.send(view=ui_kit.note(f"**Updated the card in {channel.mention}.**"),
                                                       ephemeral=True)
    await channel.send(view=view, files=ui_kit.files(view))
    await interaction.followup.send(view=ui_kit.note(f"**Posted in {channel.mention}.** Run /post-demo here again "
                                                     "anytime to update it."), ephemeral=True)


# only synced to the support server (bot.py)
POST_DEMO = app_commands.Command(name="post-demo", description="Post the Try Lookout card here, with a Start the "
                                                               "Demo button (owner only)", callback=post_demo)
POST_DEMO.default_permissions = discord.Permissions(administrator=True)
POST_DEMO.guild_only = True


class Demo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._pictures: dict[str, bytes] = {}
        self.owner_command = POST_DEMO

    @app_commands.command(name="demo", description="See what Lookout does, with example data. Only you see it.")
    @app_commands.guild_only()
    async def demo(self, interaction: discord.Interaction):
        view, files = await self._page(1, interaction.user, 0)
        await interaction.response.send_message(view=view, files=files, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        parts = (interaction.data or {}).get("custom_id", "").split(":")
        if parts[0] != PREFIX or len(parts) < 3:
            return
        try:
            if parts[1] == "go":
                page, state = max(1, min(PAGES, int(parts[2]))), 0
            else:  # lkdemo:act:<state>:<button>
                page, state = 2, int(parts[2]) | int(parts[3])
        except (ValueError, IndexError):
            return
        view, files = await self._page(page, interaction.user, state)
        await interaction.response.edit_message(view=view, attachments=files,
                                                allowed_mentions=discord.AllowedMentions.none())

    # pages

    async def _page(self, page: int, user, state: int) -> tuple[discord.ui.LayoutView, list[discord.File]]:
        view, files = await self._build(page, user, state)
        return view, ui_kit.files(view) + files

    async def _build(self, page: int, user, state: int) -> tuple[discord.ui.LayoutView, list[discord.File]]:
        if page == 1:
            return self._report(), []
        if page == 2:
            return self._alert(user, state), []
        if page == 3:
            return self._teen_safety(), []
        if page == 4:
            return self._heads_up(), []
        if page == 5:
            return self._mods_get(), []
        if page == 6:
            file = discord.File(io.BytesIO(await self._picture("week")), filename="weekly-summary.png")
            return _card(
                6, "A Weekly Summary",
                "**Every Monday, the mods get the week as a picture.**\n-# Type /summary to see the last 7 days "
                "anytime.",
                _picture_item(file), buttons=_nav(6)), [file]
        if page == 7:
            file = discord.File(io.BytesIO(await self._picture("staff")), filename="mod-stats.png")
            return _card(
                7, "Mod Stats",
                "**See who handles the most cases, and how fast.**\n-# Admins open /summary, then press Mod Stats.",
                _picture_item(file), buttons=_nav(7)), [file]
        return self._done(), []

    async def _picture(self, which: str) -> bytes:
        if which not in self._pictures:
            if which == "week":
                today = dt.datetime.now(dt.timezone.utc)
                monday = today - dt.timedelta(days=today.weekday() + 7)
                sunday = monday + dt.timedelta(days=6)
                dates = f"{monday:%b} {monday.day} - {sunday:%b} {sunday.day}"
                days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                self._pictures[which] = await asyncio.to_thread(
                    chart.summary_image, WEEK, WEEK_BEFORE, "WEEKLY SUMMARY · EXAMPLE", dates, days)
            else:
                self._pictures[which] = await asyncio.to_thread(
                    chart.staff_image, STAFF, STAFF_TOTALS, "Last 30 days", "MOD STATS · EXAMPLE")
        return self._pictures[which]

    @staticmethod
    def _report():
        return _card(
            1, "A Member Reports a Problem",
            ("Ways to Report", "Type **/report**, press the **Report a Problem** button, or right-click someone or a "
                               "message, then **Apps**.\n"
                               "-# They pick what's going on, and can add a screenshot. Members who add Lookout to "
                               "their apps can report DMs too."),
            ("What the Member Sees", "**Thanks for letting us know.**\nNobody else can see that they reported."),
            footer="This is a demo with made-up names. Nothing you press here affects anyone.",
            buttons=_nav(1))

    @staticmethod
    def _alert(user, state: int):
        you = user.mention
        done = []
        if state & CLAIMED:
            done.append(f"Claimed by {you} · just now")
        if state & WARNED:
            done.append(f'{you} warned User (#3): "Stop screaming in voice" · just now')
        if state & TIMED_OUT:
            done.append(f"{you} timed out User for 10 minutes · just now")
        if state & DISCONNECTED:
            done.append(f"{you} disconnected User · just now")

        def act(label: str, bit: int):
            return discord.ui.Button(label=label, custom_id=f"{PREFIX}:act:{state}:{bit}", disabled=bool(state & bit))

        resolved = None
        if state & RESOLVED:
            actions = [text for bit, text in ((WARNED, "Warned User"), (TIMED_OUT, "Timed out User for 10 minutes"),
                                              (DISCONNECTED, "Disconnected User")) if state & bit]
            resolved = ui_kit.kv([("Handled By", f"{you}, in 4m"),
                                  ("Actions Taken", ", ".join(actions) or "No actions taken"),
                                  ("What They Did", "Joined the channel and heard it myself.")])
            status = f"**Resolved** · by {you} in 4m · 1 report"
            buttons = [discord.ui.Button(label="Start Again", custom_id=f"{PREFIX}:go:2")]
            footer = "In a real server, the alert stays like this as a record of what was done."
        else:
            claim = discord.ui.Button(label=f"Claimed by {user.display_name}"[:80] if state & CLAIMED else "Claim",
                                      custom_id=f"{PREFIX}:act:{state}:{CLAIMED}", disabled=bool(state & CLAIMED))
            status = f"**Open** · {'Claimed by ' + you if state & CLAIMED else 'Unclaimed'} · 1 report"
            buttons = [claim, act("Resolve", RESOLVED), None,
                       act("Warn", WARNED), act("Timeout", TIMED_OUT), act("Disconnect", DISCONNECTED)]
            footer = "Mods get this in their private channel. Try the buttons, nothing real happens."
        return ui_kit.alert(
            "Demo", f"**Voice Report #12** · {_step(2)}", status, [
                ("member", "Who", "User · streaming · 2 earlier flags"),
                ("report", "What Happened", "Harassment · They keep screaming at people"),
                ("voice", "Where", "Lounge · 6 people: User, Anna, June, Milo and 2 more"),
                ("cases", "Resolved", resolved),
            ], footer="\n".join(done + [footer]), buttons=buttons + _nav(2), icon="brand")

    async def first_page(self, user) -> tuple[discord.ui.LayoutView, list[discord.File]]:
        return await self._page(1, user, 0)

    @staticmethod
    def _teen_safety():
        return _card(
            3, "Teen Safety",
            ("Message Filter", "**Inappropriate messages adults send to teens get removed.**\n"
                               "User (18+) replies to a 14 year old with something that crosses the line. It's removed, "
                               "User gets a DM, and the mods get a copy.\n"
                               "-# It also catches adults telling a teen to keep it secret, asking if they're home "
                               "alone, or trying to meet up."),
            ("Contact Info Filter", "**Contact info sent to teens gets removed.**\n"
                                    "User replies to a 14 year old with their phone number. It's removed, and they get a "
                                    "DM."),
            footer="Normal chat between adults and teens is never touched. Lookout doesn't read DMs or listen to voice, "
                   "and only sees a DM someone reports.",
            buttons=_nav(3))

    @staticmethod
    def _heads_up():
        return _card(
            4, "A Message to You Was Removed",
            "**@user** replied to you in #general with something that wasn't okay, so Lookout removed it. "
            "The mods know about it.",
            ("Good to Know", "**You didn't do anything wrong.** You don't have to reply, and it's okay to block them.\n"
                             "-# Want to add something? Press **Tell the Mods**. They won't say it was you."),
            ("If They DM You", "You can report that to the mods too. Add Lookout to your apps, then right-click the "
                               "message > **Apps** > **Report Message to Mods**."),
            subtitle="The DM a teen gets after a message to them is removed",
            buttons=[discord.ui.Button(label="Tell the Mods", custom_id=f"{PREFIX}:noop:0", disabled=True),
                     ui_kit.block_button()] + _nav(4))

    @staticmethod
    def _mods_get():
        return _card(
            5, "What the Mods Get",
            ("Inappropriate Message · #14", "**User sent an inappropriate message to a 14 year old.**\n"
                                     "Lookout removed it and kept a copy, so it's still there if they delete the rest. "
                                     "If it was a mistake, the mods press **False Alarm**."),
            ("Warning Signs · #15", "**Several warning signs added up for User:**\n"
                                    "- Sent an inappropriate message to a teen · 2 times\n"
                                    "- Sent contact info to a teen · 1 time\n"
                                    "-# One sign never flags anyone · The first week is a test only admins see"),
            ("Keep Away After 2 Reports", "If 2 teens report User, their replies to teens get removed until the mods "
                                          "press **Lift** or **Keep**."),
            ("Reported From a DM · #16", "**A teen reported a DM User sent them.**\n"
                                         "It came straight from Discord, so the mods know it's real."),
            footer="This is a demo with made-up names.", buttons=_nav(5))

    def _done(self):
        invite = discord.utils.oauth_url(self.bot.user.id, permissions=discord.Permissions(config.INVITE_PERMISSIONS),
                                         scopes=("bot", "applications.commands"))
        more = "\n".join([
            "- Inappropriate images are removed from chat",
            "- Warned members can appeal from the DM",
            "- Reporters hear back when their case is closed",
            "- A Safety Checkup for your server's own settings",
        ])
        return _card(
            PAGES, "That's Lookout",
            ("Also Included", more),
            ("Set It Up", "**Add Lookout, then press Set Up.**\n"
                          "-# Four short steps: mod channel · mod role · age roles · Report button"),
            footer="Lookout doesn't need Administrator, and it can't ban, kick or manage roles.",
            buttons=[None, discord.ui.Button(label="Back", custom_id=f"{PREFIX}:go:{PAGES - 1}"),
                     discord.ui.Button(label="Add Lookout to Your Server", style=discord.ButtonStyle.link, url=invite)],
            brand=True)


async def setup(bot):
    await bot.add_cog(Demo(bot))
