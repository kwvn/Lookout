import discord
from discord import app_commands
from discord.ext import commands

import db
import ui_kit

PREFIX = "lknet"
SHOWN = 10  # catches listed at once


def record_view(bot, user_id: int, name: str, record: dict) -> discord.ui.LayoutView:
    catches = record["catches"]
    if not catches:
        return ui_kit.note(f"**{name}** isn't on the Lookout Network. Lookout hasn't caught them anywhere.")
    n = len(catches)
    state = ("**On the network.** Lookout servers get a heads-up when they show up." if record["listed"] else
             f"**Not on the network.** It takes {db.NETWORK_SERVERS} servers and {db.NETWORK_TEENS} different teens.")
    lines = []
    for catch in catches[:SHOWN]:
        guild = bot.get_guild(catch["guild_id"])
        where = discord.utils.escape_markdown(guild.name) if guild else f"Server {catch['guild_id']} (Lookout left)"
        text = discord.utils.escape_markdown(catch["text"] or "") or "No text"
        lines.append(f"<t:{int(catch['created_at'])}:d> · {where} · teen `{catch['teen_id']}`\n||{text}||")
    if n > SHOWN:
        lines.append(f"-# And {n - SHOWN} older.")
    return ui_kit.card(
        ui_kit.titled("brand", f"Lookout Network · {name}"),
        f"{state}\n**{n}** removed message{'' if n == 1 else 's'} in **{record['servers']}** "
        f"server{'' if record['servers'] == 1 else 's'}, to **{record['teens']}** different "
        f"teen{'' if record['teens'] == 1 else 's'}.",
        ("What Lookout Caught", discord.ui.TextDisplay("\n".join(lines)[:4000])),  # not tidied, it's evidence
        footer="Take them off if the filter got it wrong or it looks like they were set up.",
        buttons=[discord.ui.Button(label="Take Off the Network", custom_id=f"{PREFIX}:clear:{user_id}")])


# network appeals, from the support server
@app_commands.describe(user_id="Their Discord user ID")
async def network_command(interaction: discord.Interaction, user_id: str):
    bot = interaction.client
    if not await bot.is_owner(interaction.user):
        return await interaction.response.send_message(view=ui_kit.note("Only Lookout's owner can use this."),
                                                       ephemeral=True)
    raw = user_id.strip().strip("<@!>")
    if not raw.isdigit():
        return await interaction.response.send_message(view=ui_kit.note("That isn't a user ID."), ephemeral=True)
    uid = int(raw)
    try:
        user = bot.get_user(uid) or await bot.fetch_user(uid)
    except discord.HTTPException:
        user = None
    name = f"@{user.name}" if user else raw
    await interaction.response.send_message(view=record_view(bot, uid, name, db.network_record(uid)), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


# only synced to the support server (bot.py)
COMMAND = app_commands.Command(name="network", description="Look someone up on the Lookout Network, for appeals "
                                                           "(owner only)", callback=network_command)
COMMAND.default_permissions = discord.Permissions(administrator=True)
COMMAND.guild_only = True


class Network(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_command = COMMAND

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        parts = (interaction.data or {}).get("custom_id", "").split(":")
        if parts[0] != PREFIX or len(parts) != 3 or parts[1] != "clear" or not parts[2].isdigit():
            return
        if not await self.bot.is_owner(interaction.user):
            return await interaction.response.send_message(view=ui_kit.note("Only Lookout's owner can use this."),
                                                           ephemeral=True)
        n = db.clear_network(int(parts[2]))
        await interaction.response.edit_message(view=ui_kit.note(
            f"**Taken off the Lookout Network.** {n} removed message{'' if n == 1 else 's'} deleted. Servers won't "
            "get heads-ups about them anymore."))


async def setup(bot):
    await bot.add_cog(Network(bot))
