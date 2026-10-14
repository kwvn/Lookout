import logging
import time

import discord
from discord.ext import commands, tasks

import db
import ui_kit

log = logging.getLogger("lookout.reminder")
TOO_OLD = 3600  # skip anything older than an hour (after downtime)


class Reminder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check.start()

    def cog_unload(self):
        self.check.cancel()

    @tasks.loop(seconds=60)
    async def check(self):
        now = time.time()
        for alert in db.due_reminders(now):
            db.mark_reminded(alert["message_id"])  # mark first so it never pings twice
            if now - alert["created_at"] > TOO_OLD:
                continue
            try:
                await self._remind(alert, now)
            except Exception:
                log.warning("Couldn't send the reminder for case %s", alert["case_no"], exc_info=True)

    async def _remind(self, alert: dict, now: float):
        guild = self.bot.get_guild(alert["guild_id"])
        channel = guild.get_channel(alert["staff_channel_id"]) if guild else None
        if channel is None:
            return
        role = alert["staff_role_id"]
        waited = max(1, int((now - alert["created_at"]) // 60))
        text = (f"{f'<@&{role}> ' if role else ''}**Case #{alert['case_no']} is still unclaimed.**\n"
                f"-# It's been waiting {waited} minute{'' if waited == 1 else 's'}. "
                "This is the last ping for it.")
        try:
            await channel.get_partial_message(alert["message_id"]).reply(
                view=ui_kit.note(text), allowed_mentions=ui_kit.ping_for(role))
        except discord.HTTPException as e:
            log.info("Reminder for case %s not sent: %s", alert["case_no"], e)

    @check.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Reminder(bot))
