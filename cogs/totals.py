import asyncio
import io
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import chart
import config
import db
import ui_kit

log = logging.getLogger("lookout.totals")
TOPGG_STATS = "https://top.gg/api/bots/{}/stats"


async def totals_command(interaction: discord.Interaction):
    bot = interaction.client
    if not await bot.is_owner(interaction.user):
        return await interaction.response.send_message(view=ui_kit.note("Only Lookout's owner can use this."),
                                                       ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    users = sum(g.member_count or 0 for g in bot.guilds)
    data = await asyncio.to_thread(chart.totals, len(bot.guilds), users, db.totals(), db.claim_minutes())
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://totals.png",
                                                                   description="Lookout's totals")))
    listed, heads_ups = db.network_size(), db.totals().get("network_alerts", 0)
    view.add_item(discord.ui.TextDisplay(
        f"**Lookout Network:** {listed:,} account{'' if listed == 1 else 's'} on it · {heads_ups:,} "
        f"heads-up{'' if heads_ups == 1 else 's'} sent\n"
        "-# Someone in two servers is counted twice, so Users runs a little high."))
    await interaction.followup.send(view=view, file=discord.File(io.BytesIO(data), filename="totals.png"),
                                    ephemeral=True)


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
        if config.TOPGG_TOKEN:
            self.post_count.start()

    def cog_unload(self):
        self.post_count.cancel()

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

    @post_count.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Totals(bot))
