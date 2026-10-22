import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui_kit

log = logging.getLogger("lookout.joinlog")
META = "join_log_channel"
# how far a server got with /setup
STAGES = {0: "Not set up yet", 1: "Mod channel picked, teen safety still off", 2: "Set up, teen safety on"}


def stage_of(s: db.GuildSettings) -> int:
    return 2 if s.configured and s.teen_safety else 1 if s.configured else 0


def _since(seconds: float) -> str:
    for unit, secs in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= secs:
            n = int(seconds // secs)
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return "under a minute"


def joined_view(guild: discord.Guild, entry: dict) -> discord.ui.LayoutView:
    stage = entry["stage"]
    status = f"**{STAGES[stage]}**"
    if stage and entry.get("stage_at"):
        status += f" · {_since(entry['stage_at'] - entry['joined_at'])} after joining"
    return ui_kit.card(
        ui_kit.titled(ui_kit.mascot("party", "brand"), f"Joined {discord.utils.escape_markdown(guild.name)}"),
        f"`{guild.id}` · {guild.member_count or 0:,} members · server made <t:{int(guild.created_at.timestamp())}:R>\n"
        f"{status}",
        footer=f"Server number {entry['servers']:,}")


def left_view(guild: discord.Guild, entry: dict | None, servers: int) -> discord.ui.LayoutView:
    stay = f" · was there {_since(time.time() - entry['joined_at'])}" if entry else ""
    setup = f"\nGot as far as: **{STAGES[entry['stage']]}**" if entry else ""
    return ui_kit.card(
        ui_kit.titled(ui_kit.mascot("cry", "warning"), f"Left {discord.utils.escape_markdown(guild.name)}"),
        f"`{guild.id}` · {guild.member_count or 0:,} members{stay}{setup}",
        footer=f"Lookout is in {servers:,} server{'' if servers == 1 else 's'} now")


# posts the log in the channel it's run in, from the support server
async def join_log_command(interaction: discord.Interaction):
    bot = interaction.client
    if not await bot.is_owner(interaction.user):
        return await interaction.response.send_message(view=ui_kit.note("Only Lookout's owner can use this."),
                                                       ephemeral=True)
    db.set_meta(META, str(interaction.channel_id))
    stages = [stage_of(db.get_settings(g.id)) for g in bot.guilds]
    await interaction.response.send_message(view=ui_kit.note(
        f"**Server joins and leaves will show up here.**\nRight now Lookout is in {len(stages):,} servers: "
        f"{stages.count(2):,} set up, {stages.count(1):,} with only a mod channel, {stages.count(0):,} not set up."))


COMMAND = app_commands.Command(name="join-log", description="Post Lookout's server joins and leaves in this channel "
                                                            "(owner only)", callback=join_log_command)
COMMAND.default_permissions = discord.Permissions(administrator=True)
COMMAND.guild_only = True


class JoinLog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_command = COMMAND

    def _channel(self):
        cid = db.get_meta(META)
        return self.bot.get_channel(int(cid)) if cid and cid.isdigit() else None

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        channel = self._channel()
        if channel is None:
            return
        entry = {"joined_at": time.time(), "stage": 0, "servers": len(self.bot.guilds)}
        try:
            msg = await channel.send(view=joined_view(guild, entry), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            return log.info("Couldn't post the join for %s: %s", guild.id, e)
        db.log_join(guild.id, msg.id, entry["servers"])

    # called by /setup after every save
    async def progress(self, guild: discord.Guild, s: db.GuildSettings) -> None:
        entry, channel = db.join_entry(guild.id), self._channel()
        stage = stage_of(s)
        if entry is None or channel is None or stage <= entry["stage"]:
            return
        db.set_join_stage(guild.id, stage)
        entry.update(stage=stage, stage_at=time.time())
        try:
            await channel.get_partial_message(entry["message_id"]).edit(view=joined_view(guild, entry),
                                                                        allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.info("Couldn't update the join post for %s: %s", guild.id, e)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        entry, channel = db.join_entry(guild.id), self._channel()
        db.drop_join(guild.id)
        if channel is None:
            return
        try:
            await channel.send(view=left_view(guild, entry, len(self.bot.guilds)),
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            log.info("Couldn't post the leave for %s: %s", guild.id, e)


async def setup(bot):
    await bot.add_cog(JoinLog(bot))
