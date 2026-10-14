import asyncio
import datetime as dt
import io
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import chart
import db
import ui_kit

log = logging.getLogger("lookout.summary")
PREFIX = "lksummary"
WEEK = 7 * 86400
SEND_DAY = 0      # Monday
SEND_HOUR = 9     # 09:00 UTC
STAFF_PERIODS = (7, 30, 90)
MAX_STAFF_ROWS = 10


def last_send_time(now: dt.datetime) -> dt.datetime:
    day = now - dt.timedelta(days=(now.weekday() - SEND_DAY) % 7)
    slot = day.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
    return slot if slot <= now else slot - dt.timedelta(days=7)


def week_before(slot: dt.datetime) -> tuple[float, float]:
    end = slot.replace(hour=0, minute=0, second=0, microsecond=0)
    return (end - dt.timedelta(days=7)).timestamp(), end.timestamp()


def last_7_days(now: dt.datetime) -> tuple[float, float]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=6)
    return start.timestamp(), now.timestamp()


def _duration(seconds: float) -> str:
    minutes = max(1, int(seconds // 60))
    if minutes >= 1440:
        return f"{minutes // 1440}d {minutes % 1440 // 60}h"
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m"


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _compared(now: int, before: int) -> str:
    if now == before:
        return f"{now} · same as the week before" if now else "0"
    return f"{now} · {'up' if now > before else 'down'} from {before} the week before"


def is_quiet(stats: dict) -> bool:
    keys = ("reports", "images", "warnings", "timeouts", "disconnects", "opened", "resolved", "still_open")
    return not any(stats[k] for k in keys)


def _top_rows(stats: dict) -> list[tuple[str, str]]:
    top = []
    if stats["top_channel"]:
        top.append(("Most Reported Channel", f"<#{stats['top_channel'][0]}> · {_count(stats['top_channel'][1], 'report')}"))
    if stats["top_member"]:
        top.append(("Most Flagged Member", f"<@{stats['top_member'][0]}> · {_count(stats['top_member'][1], 'flag')}"))
    if stats["top_staff"]:
        top.append(("Most Cases Resolved", f"<@{stats['top_staff'][0]}> · {_count(stats['top_staff'][1], 'case')}"))
    return top


# fallback if the picture can't be drawn
def _text_card(guild, stats, before, cases_cmd, dates, footer, buttons) -> discord.ui.LayoutView:
    still_open = str(stats["still_open"])
    if stats["still_open"]:
        still_open += f" · see them with {cases_cmd}"
    week = ui_kit.kv([
        ("Reports", _compared(stats["reports"], before["reports"])),
        ("Cases Opened", str(stats["opened"])),
        ("Cases Resolved", str(stats["resolved"])),
        ("Average Time to Resolve", _duration(stats["avg_seconds"]) if stats["avg_seconds"] else None),
        ("Still Open", still_open),
    ])
    actions = ui_kit.kv([(label, str(stats[key])) for label, key in (
        ("Warnings", "warnings"), ("Timeouts", "timeouts"), ("Disconnects", "disconnects"),
        ("Images Removed", "images"),
    ) if stats[key]]) or "Nothing this week."
    top = _top_rows(stats)
    return ui_kit.card(
        ui_kit.titled("summary", "Weekly Summary"),
        ("This Week", week), ("Actions", actions), ("Top of the Week", ui_kit.kv(top)) if top else None,
        subtitle=f"{discord.utils.escape_markdown(guild.name)} · {dates}", footer=footer, buttons=buttons,
        brand=True)


async def summary_message(bot, guild: discord.Guild, start: float, end: float, weekly: bool,
                          buttons: list | None = None) -> tuple[discord.ui.LayoutView, list[discord.File]]:
    stats = db.week_stats(guild.id, start, end)
    before = db.week_stats(guild.id, start - WEEK, start)
    cases_cmd = f"</cases:{bot.command_ids['cases']}>" if bot.command_ids.get("cases") else "`/cases`"

    first = dt.datetime.fromtimestamp(start, dt.timezone.utc)
    last = first + dt.timedelta(days=6)
    dates = f"{first:%b} {first.day} - {last:%b} {last.day}"
    days = [f"{first + dt.timedelta(days=i):%a}" for i in range(7)]
    footer = ("Sent every Monday. You can turn it off in /setup." if weekly
              else "Lookout posts this in the mod channel every Monday.")

    try:
        png = await asyncio.to_thread(chart.summary_image, stats, before,
                                      "WEEKLY SUMMARY" if weekly else "LAST 7 DAYS", dates, days,
                                      "THIS WEEK" if weekly else "LAST 7 DAYS")
    except Exception:
        log.warning("Could not draw the summary picture", exc_info=True)
        return _text_card(guild, stats, before, cases_cmd, dates, footer, buttons), []

    sections = []
    top = _top_rows(stats)
    if top:
        sections.append(("Top of the Week", ui_kit.kv(top)))
    if stats["still_open"]:
        sections.append(("Open Cases", f"{_count(stats['still_open'], 'case')} still waiting. See them with {cases_cmd}"))
    file = discord.File(io.BytesIO(png), filename="weekly-summary.png")
    view = ui_kit.card(None, *sections, image=f"attachment://{file.filename}", footer=footer, buttons=buttons,
                       brand=True)
    return view, [file]


# mod stats

async def _person(guild: discord.Guild, user_id: int) -> tuple[str, bytes | None]:
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            return "Left the server", None
    # the font can't draw everything, usernames are safe
    name = member.display_name if chart.can_draw(member.display_name) else member.name
    try:
        avatar = await member.display_avatar.replace(size=128, static_format="png").read()
    except discord.HTTPException:
        avatar = None
    return name[:32], avatar


def _staff_buttons(days: int) -> list:
    buttons = [discord.ui.Button(label=f"{d} Days", custom_id=f"{PREFIX}:staff:{d}", disabled=d == days)
               for d in STAFF_PERIODS]
    return buttons + [discord.ui.Button(label="Back to Summary", custom_id=f"{PREFIX}:week:0")]


async def staff_message(guild: discord.Guild, days: int) -> tuple[discord.ui.LayoutView, list[discord.File]]:
    stats = db.staff_stats(guild.id, time.time() - days * 86400)
    people = sorted(stats["staff"].items(),
                    key=lambda kv: (-kv[1]["cases"], -(kv[1]["warnings"] + kv[1]["timeouts"] + kv[1]["disconnects"])))
    rows = []
    for user_id, p in people[:MAX_STAFF_ROWS]:
        name, avatar = await _person(guild, user_id)
        rows.append({"id": user_id, "name": name, "avatar": avatar, "cases": p["cases"],
                     "avg_seconds": p["avg_seconds"],
                     "actions": p["warnings"] + p["timeouts"] + p["disconnects"]})

    buttons = _staff_buttons(days)
    title = ui_kit.titled("staff", "Mod Stats")
    footer = "Cases are alerts they resolved. Actions are warnings, timeouts and disconnects."
    if not rows:
        return ui_kit.card(title, "No mod activity yet. Cases and actions show up here once your mods handle alerts.",
                           subtitle=f"Last {days} days", buttons=buttons, brand=True), []
    try:
        png = await asyncio.to_thread(chart.staff_image, rows, stats, f"Last {days} days")
    except Exception:
        log.warning("Could not draw the mod stats picture", exc_info=True)
        lines = "\n".join(f"**{i}.** <@{r['id']}> · {r['cases']} cases · {r['actions']} actions"
                          for i, r in enumerate(rows, 1))
        return ui_kit.card(title, lines, subtitle=f"Last {days} days", footer=footer, buttons=buttons,
                           brand=True), []
    file = discord.File(io.BytesIO(png), filename="staff-stats.png")
    names = " · ".join(f"**{i}.** <@{r['id']}>" for i, r in enumerate(rows, 1))
    return ui_kit.card(None, ("In Order", names), image=f"attachment://{file.filename}", footer=footer,
                       buttons=buttons, brand=True), [file]


class Summary(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.weekly.start()

    def cog_unload(self):
        self.weekly.cancel()

    @tasks.loop(minutes=30)
    async def weekly(self):
        slot = last_send_time(discord.utils.utcnow()).timestamp()
        for guild in list(self.bot.guilds):
            try:
                if await self._post(guild, slot):
                    await asyncio.sleep(1)
            except Exception:
                log.warning("Couldn't post the weekly summary in server %s", guild.id, exc_info=True)

    async def _post(self, guild: discord.Guild, slot: float) -> bool:
        s = db.get_settings(guild.id)
        if not s.configured:
            return False
        sent = db.summary_sent_at(guild.id)
        if sent is not None and sent >= slot:
            return False
        db.mark_summary_sent(guild.id)
        await self._checkup(guild, s)
        if sent is None or not s.weekly_summary:
            return False  # new servers wait for next monday
        channel = guild.get_channel(s.staff_channel_id)
        start, end = week_before(dt.datetime.fromtimestamp(slot, dt.timezone.utc))
        if channel is None or is_quiet(db.week_stats(guild.id, start, end)):
            return False
        view, files = await summary_message(self.bot, guild, start, end, weekly=True)
        await channel.send(view=view, files=files, allowed_mentions=discord.AllowedMentions.none())
        return True

    # weekly checkup, tell the mods if the score dropped
    async def _checkup(self, guild: discord.Guild, s: db.GuildSettings) -> None:
        from cogs.setup import safety_checkup
        try:
            results = await safety_checkup(guild, s)
        except Exception:
            log.warning("Couldn't run the Safety Checkup in server %s", guild.id, exc_info=True)
            return
        passed, before = sum(ok for _, ok, _ in results), s.checkup_score
        s.checkup_score, s.checkup_at = passed, time.time()
        db.save_settings(s)
        channel = guild.get_channel(s.staff_channel_id)
        if before is None or passed >= before or channel is None:
            return
        broken = ", ".join(name for name, ok, _ in results if not ok)
        try:
            await channel.send(view=ui_kit.note(
                f"**Your Safety Checkup went from {before} to {passed} of {len(results)}.** "
                f"Something changed in your server's settings.\n-# Needs a fix: {broken}. Open /setup, then press "
                "Safety Checkup."), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    @weekly.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

    async def _week(self, interaction: discord.Interaction):
        start, end = last_7_days(discord.utils.utcnow())
        # staff stats are admin only
        admin = interaction.user.guild_permissions.manage_guild
        buttons = [discord.ui.Button(label="Mod Stats", custom_id=f"{PREFIX}:staff:30")] if admin else None
        return await summary_message(self.bot, interaction.guild, start, end, weekly=False, buttons=buttons)

    @app_commands.command(name="summary", description="See the last 7 days in numbers and charts (mods only)")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def summary(self, interaction: discord.Interaction):
        if not self.bot.is_staff(interaction.user):
            return await interaction.response.send_message(view=ui_kit.note("Only mods can see the summary."),
                                                           ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        view, files = await self._week(interaction)
        await interaction.followup.send(view=view, files=files, ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        parts = (interaction.data or {}).get("custom_id", "").split(":")
        if parts[0] != PREFIX or len(parts) < 3:
            return
        user = interaction.user
        if parts[1] == "staff" and not user.guild_permissions.manage_guild:
            return await interaction.response.send_message(view=ui_kit.note("Only admins can see Mod Stats."),
                                                           ephemeral=True)
        if not self.bot.is_staff(user):
            return await interaction.response.send_message(view=ui_kit.note("Only mods can see the summary."),
                                                           ephemeral=True)
        await interaction.response.defer()  # drawing takes a few secs
        if parts[1] == "staff":
            days = int(parts[2]) if parts[2].isdigit() and int(parts[2]) in STAFF_PERIODS else 30
            view, files = await staff_message(interaction.guild, days)
        else:
            view, files = await self._week(interaction)
        await interaction.edit_original_response(view=view, attachments=files,
                                                 allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Summary(bot))
