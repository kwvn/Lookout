import asyncio
import datetime as dt
import io
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import banner
import chart
import config
import db
import ui_kit

log = logging.getLogger("lookout.totals")
TOPGG_STATS = "https://top.gg/api/bots/{}/stats"
# key: (days, button, word for "the ... before")
RANGES = {"week": (7, "This Week", "week"), "month": (30, "30 Days", "30 days"), "all": (None, "All Time", "")}
SAFETY = (("removed", "messages to teens removed"), ("contact", "contact info removed"),
          ("threats", "threats and picture requests"), ("images", "images removed"),
          ("safety_checks", "Is this safe? checks"), ("handled", "cases handled"))


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _short(day: str | dt.date) -> str:  # "Oct 29"
    day = dt.date.fromisoformat(day) if isinstance(day, str) else day
    return f"{day:%b} {day.day}"


def _change(cur: float, prev: float | None, word: str) -> str:  # "▲ 12% from 279"
    if prev is None:
        return ""
    diff = cur - prev
    if not diff:
        return f"same as the {word} before"
    arrow = "▲" if diff > 0 else "▼"
    return f"{arrow} {abs(diff) / prev:.0%} from {prev:,}" if prev >= 20 else f"{arrow} {abs(diff):,} from {prev:,}"


def _grew(n: int | None, when: str) -> str:  # "▲ 18 this week"
    if n is None:
        return ""
    return f"{'▲' if n > 0 else '▼'} {abs(n):,} {when}" if n else f"no change {when}"


# everything chart.totals draws, for one range
def gather(bot, rng: str) -> dict:
    days, _, word = RANGES[rng]
    now = db.totals()
    servers, members = len(bot.guilds), sum(g.member_count or 0 for g in bot.guilds)
    today = _today()
    saved = db.days_since("")
    start = before = None
    partial = False
    if days:  # saved rows are the start of their day, so the last 7 days start 6 days ago
        start = db.day_before(str(today - dt.timedelta(days=days - 1)))
        if start is None and saved:  # not a full range saved yet, count from the first save
            start, partial = saved[0], True
        if start and not partial:
            before = db.day_before(str(today - dt.timedelta(days=2 * days - 1)))
            if before and before["day"] == start["day"]:
                before = None

    def counts(key: str) -> tuple[int, int | None]:
        cur = now.get(key, 0) - (start.get(key, 0) if start else 0)
        prev = start.get(key, 0) - before.get(key, 0) if start and before else None
        return cur, prev

    first = _short(saved[0]["day"]) if saved else _short(today)
    if not days:
        when, rng_text, title = "", "All time", "Keeping teens safe, all time"
    elif partial:
        when = f"since {_short(start['day'])}"
        rng_text, title = f"Since {_short(start['day'])} · a full {word} isn't saved yet", f"Keeping teens safe {when}"
    else:
        when = "this week" if days == 7 else "in 30 days"
        rng_text = f"Last {days} days" + (f" · compared to the {word} before" if before else "")
        title = "Keeping teens safe this week" if days == 7 else "Keeping teens safe, last 30 days"

    joined, left = counts("joined")[0], counts("left")[0]
    growth = [(f"{servers:,}", "server" if servers == 1 else "servers",
               _grew(servers - start["servers"], when) if start else ""),
              (f"{members:,}", "members", _grew(members - start["members"], when) if start else ""),
              (f"{joined:,} / {left:,}", f"joined / left {when or f'since {first}'}", "")]

    safety = []
    for key, label in SAFETY:
        cur, prev = counts(key)
        safety.append((f"{cur:,}", label, _change(cur, prev, word)))

    (alerts, prev_alerts), (wrong, prev_wrong) = counts("filter_alerts"), counts("false_alarms")
    rate = f"{wrong / alerts:.1%}" if alerts else "None yet"
    rate_change = ""
    if alerts and prev_alerts:
        old = prev_wrong / prev_alerts
        rate_change = (f"same as the {word} before" if round(old, 3) == round(wrong / alerts, 3)
                       else f"{'▲' if wrong / alerts > old else '▼'} from {old:.1%}")
    since_ts = dt.datetime.combine(dt.date.fromisoformat(start["day"]), dt.time(), dt.timezone.utc).timestamp() \
        if start else 0
    claim = db.claim_minutes(since_ts)
    claim_change = ""
    if days and before:
        old = db.claim_minutes(since_ts - days * 86400, since_ts)
        if old is not None and claim is not None and chart._minutes(old) != chart._minutes(claim):
            claim_change = f"{'▲' if claim > old else '▼'} from {chart._minutes(old)}"
    quality = [(rate, f"false alarms · {wrong:,} of {alerts:,} alerts" if alerts else "false alarms", rate_change),
               (chart._minutes(claim), "average time to claim a case", claim_change)]

    setups = [(g, db.get_settings(g.id)) for g in bot.guilds]
    done = [s for _, s in setups if s.configured]
    setup = [("Finished setup", len(done), servers), ("Teen Safety on", sum(s.teen_safety for s in done), servers),
             ("Image filter on", sum(s.image_scan for s in done), servers),
             ("Lookout Network on", sum(s.network for s in done), servers)]
    waiting = sorted((g for g, s in setups if not s.configured), key=lambda g: g.member_count or 0, reverse=True)[:3]
    stamp = discord.utils.utcnow()
    big = [(g.name, f"{g.member_count or 0:,} members" + (f" · joined {banner.ago(g.me.joined_at, stamp)}"
                                                           if g.me and g.me.joined_at else ""))
           for g in waiting]

    month = [r for r in saved if r["day"] >= str(today - dt.timedelta(days=29)) and r["day"] != str(today)]
    return {"range": rng_text, "safety_title": title, "growth": growth, "safety": safety, "quality": quality,
            "setup": setup, "big": big, "chart": [r["members"] for r in month] + [members],
            "chart_from": _short(month[0]["day"]) if month else _short(today), "chart_to": _short(today),
            "chart_empty": f"Fills in day by day, starting {first}"}


async def render(bot, rng: str) -> tuple[discord.ui.LayoutView, discord.File]:
    png = await asyncio.to_thread(chart.totals, gather(bot, rng))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://totals.png",
                                                                   description="Lookout's totals")))
    listed, heads_ups = db.network_size(), db.totals().get("network_alerts", 0)
    view.add_item(discord.ui.TextDisplay(
        f"**Lookout Network:** {listed:,} account{'' if listed == 1 else 's'} on it · {heads_ups:,} "
        f"heads-up{'' if heads_ups == 1 else 's'} sent\n"
        "-# Someone in two servers is counted twice, so Members runs a little high."))
    view.add_item(discord.ui.ActionRow(*(
        discord.ui.Button(label=label, custom_id=f"lktotals:{key}",
                          style=discord.ButtonStyle.primary if key == rng else discord.ButtonStyle.secondary)
        for key, (_, label, _) in RANGES.items())))
    return view, discord.File(io.BytesIO(png), filename="totals.png")


async def totals_command(interaction: discord.Interaction):
    bot = interaction.client
    if not await bot.is_owner(interaction.user):
        return await interaction.response.send_message(view=ui_kit.note("Only Lookout's owner can use this."),
                                                       ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    view, file = await render(bot, "week")
    await interaction.followup.send(view=view, file=file, ephemeral=True)


# only synced to the support server (bot.py)
COMMAND = app_commands.Command(name="totals", description="Lookout's numbers across every server (owner only)",
                               callback=totals_command)
COMMAND.default_permissions = discord.Permissions(administrator=True)
COMMAND.guild_only = True


class Totals(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_command = COMMAND
        self._bearer = False        # newer top.gg tokens want "Bearer " in front
        self.save_day.start()
        if config.TOPGG_TOKEN:
            self.post_count.start()

    def cog_unload(self):
        self.save_day.cancel()
        self.post_count.cancel()

    # the numbers at the start of each day, so /totals can compare. hourly, the first save of a day sticks
    @tasks.loop(hours=1)
    async def save_day(self):
        db.save_day(str(_today()), len(self.bot.guilds), sum(g.member_count or 0 for g in self.bot.guilds))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id") or ""
        if not cid.startswith("lktotals:") or cid[9:] not in RANGES:
            return
        if not await self.bot.is_owner(interaction.user):
            return await interaction.response.send_message(view=ui_kit.note("Only Lookout's owner can use this."),
                                                           ephemeral=True)
        await interaction.response.defer()
        view, file = await render(self.bot, cid[9:])
        await interaction.edit_original_response(view=view, attachments=[file])

    @tasks.loop(minutes=30)
    async def post_count(self):
        body = {"server_count": len(self.bot.guilds), "shard_count": self.bot.shard_count or 1}
        for attempt in range(2):
            token = ("Bearer " if self._bearer else "") + config.TOPGG_TOKEN
            try:
                async with self.bot.web.post(TOPGG_STATS.format(self.bot.user.id), json=body,
                                             headers={"Authorization": token},
                                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 401 and attempt == 0:
                        self._bearer = not self._bearer
                        continue
                    if r.status >= 400:
                        log.warning("top.gg didn't take the server count: %s", r.status)
            except Exception as e:
                log.warning("Couldn't post the server count to top.gg: %s", e)
            return

    @save_day.before_loop
    @post_count.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Totals(bot))
