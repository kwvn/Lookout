import time
from collections import Counter

import discord
from discord.ext import commands, tasks

import db
import ui_kit

PREFIX = "lkscan"
SCAN_DAYS = 7
# what lookout would have done during a safety scan, by find kind (teensafety and imagescan save them):
# (kind, short name for the list, one, more than one)
WOULD_REMOVE = [
    ("inappropriate", "Inappropriate message", "inappropriate message sent to a kid", "inappropriate messages sent to kids"),
    ("threat", "Threat", "threat to share a kid's pictures", "threats to share kids' pictures"),
    ("pics", "Asked for pictures", "request for pictures from a kid", "requests for pictures from kids"),
    ("contact", "Contact info", "phone number, email or social sent to a kid", "phone numbers, emails or socials sent to kids"),
    ("own_contact", "Own contact info", "kid posting their own contact info", "kids posting their own contact info"),
    ("image", "Explicit image", "explicit image where kids can see it", "explicit images where kids can see them"),
    ("restricted", "Kept away", "message to a kid from someone kept away from kids",
     "messages to kids from someone kept away from kids"),
]
WOULD_TELL = [
    ("warning_signs", "Warning signs", "adult whose warning signs added up", "adults whose warning signs added up"),
    ("keep_away", "Reported by 2 kids", "adult reported by 2 different kids", "adults reported by 2 different kids"),
    ("fake_teen", "Not really a teen?", "member who probably isn't the teen they say",
     "members who probably aren't the teens they say"),
    ("two_ages", "Two different ages", "member who said two different ages", "members who said two different ages"),
    ("age_mismatch", "Age mismatch", "age role that doesn't match what they said",
     "age roles that don't match what they said"),
    ("network", "Caught in other servers", "account caught going after kids in other servers",
     "accounts caught going after kids in other servers"),
    ("fishing", "Going after kids elsewhere", "account going after kids in other servers",
     "accounts going after kids in other servers"),
    ("alt", "Possible alt", "possible alt account", "possible alt accounts"),
]
EXAMPLES = 5  # latest finds listed under each part


def _group(finds: list[dict], kinds: list) -> str | None:
    by = Counter(f["kind"] for f in finds)
    counts = [f"**{by[kind]}** {one if by[kind] == 1 else many}" for kind, _, one, many in kinds if by[kind]]
    if not counts:
        return None
    short = {kind: name for kind, name, *_ in kinds}
    latest = []
    for f in [f for f in finds if f["kind"] in short][:EXAMPLES]:
        who = f" · <@{f['user_id']}>" if f["user_id"] else ""
        to = f" to <@{f['other_id']}>" if f["other_id"] else ""
        jump = f" · [Jump to It]({f['link']})" if f["link"] else ""
        latest.append(f"- {short[f['kind']]}{who}{to} · <t:{int(f['created_at'])}:R>{jump}")
    return "\n".join(counts) + "\n-# Latest\n" + "\n".join(latest)


def report_view(guild: discord.Guild, s: db.GuildSettings) -> discord.ui.LayoutView:
    finds = db.scan_finds(guild.id, since=s.scan_started or 0)
    days = max(1, min(SCAN_DAYS, round((time.time() - (s.scan_started or time.time())) / 86400)))
    removed, told = _group(finds, WOULD_REMOVE), _group(finds, WOULD_TELL)
    total = sum(1 for f in finds if f["kind"] in {k for k, *_ in WOULD_REMOVE + WOULD_TELL})
    name = discord.utils.escape_markdown(guild.name)
    span = f"{days} day{'' if days == 1 else 's'}"
    if total:
        lead = (f"In {span}, Lookout would have stepped in **{total} time{'' if total == 1 else 's'}** in **{name}**. "
                "Nothing was removed, nobody got a message and nobody was pinged. This is what it saw.")
    else:
        lead = (f"In {span}, Lookout didn't find anything it would have stepped in on in **{name}**. That's good "
                "news. It keeps watching, and the next time someone goes after a kid here it'll be ready.")
    teens = db.seen_teens(guild.id) if s.auto_ages else 0
    return ui_kit.card(
        "Safety Scan", lead,
        ("Would Have Removed", removed) if removed else None,
        ("Would Have Told the Mods", told) if told else None,
        ("Kids Lookout Found", f"**{teens}** member{'' if teens == 1 else 's'} said they're under 18. Messages adults "
                               "send them would be checked from day one.") if teens else None,
        subtitle=f"{name} · {span}", icon="summary",
        footer="Lookout keeps watching quietly until an admin turns it on. Threats and self-harm still come to you "
               "right away.",
        buttons=[discord.ui.Button(label="Turn It On", custom_id=f"{PREFIX}:on")])


def start(s: db.GuildSettings, user_id: int) -> None:
    now = time.time()
    s.scan_started, s.scan_until, s.scan_reported, s.scan_by = now, now + SCAN_DAYS * 86400, False, user_id


class Scan(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.due_reports.start()

    def cog_unload(self):
        self.due_reports.cancel()

    @tasks.loop(minutes=30)
    async def due_reports(self):
        for guild_id in db.due_scans(time.time()):
            s = db.get_settings(guild_id)
            s.scan_reported = True  # once, even if it can't be posted
            db.save_settings(s)
            guild = self.bot.get_guild(guild_id)
            if guild:
                await self.post_report(guild, s)

    @due_reports.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

    async def post_report(self, guild: discord.Guild, s: db.GuildSettings) -> discord.Message | None:
        channel = guild.get_channel(s.staff_channel_id) if s.staff_channel_id else None
        perms = channel.permissions_for(guild.me) if channel else None
        if not (perms and perms.view_channel and perms.send_messages):
            return None
        view = report_view(guild, s)
        try:
            message = await channel.send(view=view, files=ui_kit.files(view),
                                         allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            return None
        # whoever started it hears that it's ready
        try:
            user = (self.bot.get_user(s.scan_by) or await self.bot.fetch_user(s.scan_by)) if s.scan_by else None
        except discord.HTTPException:
            user = None
        if user:
            n = len(db.scan_finds(guild.id, since=s.scan_started or 0))
            note = ui_kit.note(
                f"**Your Safety Scan for {discord.utils.escape_markdown(guild.name)} is done.** "
                + (f"Lookout would have stepped in {n} time{'' if n == 1 else 's'}." if n else
                   "Lookout didn't find anything to step in on.") + f" The report is in {channel.mention}.",
                buttons=[discord.ui.Button(label="See the Report", style=discord.ButtonStyle.link,
                                           url=message.jump_url)])
            try:
                await user.send(view=note)
            except discord.HTTPException:
                pass
        return message

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        action = ui_kit.custom_id(interaction).split(":")
        if action[0] != PREFIX or len(action) < 2:
            return
        user, guild = interaction.user, interaction.guild
        if not isinstance(user, discord.Member) or not user.guild_permissions.manage_guild:
            return await interaction.response.send_message(view=ui_kit.note(
                "Only admins can do this. You need the **Manage Server** permission."), ephemeral=True)
        s = db.get_settings(guild.id)
        if action[1] == "report":
            if not s.scanning:
                return await interaction.response.send_message(view=ui_kit.note("Lookout isn't scanning, it's on."),
                                                               ephemeral=True)
            return await interaction.response.send_message(view=report_view(guild, s), ephemeral=True,
                                                           allowed_mentions=discord.AllowedMentions.none())
        if action[1] != "on":
            return
        if not s.scanning:
            return await interaction.response.send_message(view=ui_kit.note("**Lookout is already on.**"),
                                                           ephemeral=True)
        s.scan_started = s.scan_until = s.scan_by = None
        s.scan_reported = False
        db.save_settings(s)
        db.log(guild.id, None, "settings_changed", "safety scan: turned on", actor_id=user.id)
        from cogs.setup import permission_report
        perms, ok = permission_report(guild, s)
        text = (f"**Lookout is on.** From now on it removes what it finds and sends alerts to <#{s.staff_channel_id}>."
                + ("" if ok else f"\n\n**It still needs a few permissions to do that:**\n{perms}"))
        buttons = [] if ok else [discord.ui.Button(label="Give Lookout Its Permissions", style=discord.ButtonStyle.link,
                                                   url=ui_kit.invite_url(self.bot))]
        await interaction.response.send_message(view=ui_kit.note(text, buttons), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Scan(bot))
