import discord
from discord import app_commands
from discord.ext import commands

import ui_kit
from cogs.welcome import BUGS, FAQ, IDEAS

TITLE = "Contact Us"
TOPICS = [("General Support", "Setting up Lookout or getting it working in your server"),
          ("Partnerships", "Working together with your server or community"),
          ("Staff Applications", "Joining the Lookout team"),
          ("Private Questions", "Anything you'd rather not ask in public")]


# the info card in the ticket channel of the support server. the Tickets bot posts its own button under it
def contact_card() -> discord.ui.LayoutView:
    rows = []
    for name, text in TOPICS:
        if rows:
            rows.append(discord.ui.Separator())
        rows.append(f"**{name}**\n-# {text}")
    return ui_kit.card(
        TITLE,
        ("How We Can Help", rows),
        ("Looking for Something Else?", f"- Quick answers are in <#{FAQ}>\n- Bugs go in <#{BUGS}>\n"
                                        f"- Ideas go in <#{IDEAS}>"),
        subtitle="Private support from the Lookout team",
        footer="To report someone in your own server, use /report there so your mods can see it.",
        icon="brand", looky="noted")


def _is_contact(message: discord.Message) -> bool:
    try:
        view = discord.ui.LayoutView.from_message(message, timeout=None)
    except Exception:
        return False
    return any(isinstance(item, discord.ui.TextDisplay) and TITLE in item.content.split("\n")[0]
               for item in view.walk_children())


async def post_contact(interaction: discord.Interaction):
    await ui_kit.post_card(interaction, lambda perms: contact_card(), _is_contact, "contact card",
                           "Now post your Tickets panel under it. Run /post-contact again anytime to update this card.")


# only synced to the support server (bot.py)
POST_CONTACT = app_commands.Command(name="post-contact", description="Post the Contact Us card here, for the ticket "
                                                                     "channel (owner only)", callback=post_contact)
POST_CONTACT.default_permissions = discord.Permissions(administrator=True)
POST_CONTACT.guild_only = True


class Contact(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_command = POST_CONTACT


async def setup(bot):
    await bot.add_cog(Contact(bot))
