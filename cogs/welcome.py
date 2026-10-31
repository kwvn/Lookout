import discord
from discord import app_commands
from discord.ext import commands

import config
import ui_kit

RULES = "lkwelcome:rules"
# channels in the support server
DEMO, FAQ, NEWS = 1557021939980046426, 1553445422905954384, 1553408494093213799
SUPPORT, BUGS, IDEAS = 1553440549455925319, 1553426373580689478, 1553424915317661806

ABOUT = ("Lookout is a free teen safety bot for servers where teens and adults hang out together.\n\n"
         "When an adult goes after a teen, like asking for inappropriate pictures, sharing contact info or pushing to "
         "move things to DMs, Lookout steps in and gives the mods the full story so they can act early.\n\n"
         "Teens get their own tools too, like private reports (even for a DM) and **Is this safe?**, a private check "
         "for any message. "
         "Servers using Lookout also warn each other about repeat offenders through the **Lookout Network**.\n"
         "-# Open source · Never reads DMs on its own · Never needs Administrator")

# the rules only show when someone presses Read the Rules, so editing them needs no repost
RULE_LIST = [
    ("Follow Discord's Rules", "Everything here has to follow the [Community Guidelines](<https://discord.com/guidelines>)"
                               " and [Terms of Service](<https://discord.com/terms>)."),
    ("Be Kind", "Treat everyone with respect. No harassment, hate speech or personal attacks, even as a joke."),
    ("Keep It Safe for Everyone", "Teens hang out here too. No adult content, and nothing inappropriate toward anyone, "
                                  "especially members under 18."),
    ("Keep Personal Info Private", "Don't share your own or anyone else's address, phone number, socials or photos."),
    ("No Advertising", "Don't promote servers, bots or anything else, in chat or in DMs."),
    ("Keep Chat Readable", "No spam or walls of text. Use the right channel, and stick to English so everyone can "
                           "follow along."),
    ("Questions and Bugs Go in the Forum", f"Post them in <#{SUPPORT}> so nothing gets lost and others can find the "
                                           "answer later."),
    ("Listen to the Mods", "If a mod asks you to stop, stop. Disagree with a call? DM a mod instead of arguing in "
                           "chat."),
]


def welcome_card(bot, banner: bool = True) -> discord.ui.LayoutView:
    link = discord.ButtonStyle.link
    return ui_kit.card(
        "Welcome to Lookout",
        ("About Lookout", [ABOUT, ui_kit.button_row(
            discord.ui.Button(label="Add Lookout", style=link, url=ui_kit.invite_url(bot)),
            discord.ui.Button(label="YourSitee", style=link, url=config.SITE_URL))]),
        ("What This Server Is For", f"- Try every feature in <#{DEMO}> before adding Lookout anywhere\n"
                                    f"- Find quick answers in <#{FAQ}>\n"
                                    f"- Get help setting it up in <#{SUPPORT}>\n"
                                    f"- Report bugs in <#{BUGS}> and share ideas in <#{IDEAS}>\n"
                                    f"- Hear about updates first in <#{NEWS}>"),
        ("Rules", [discord.ui.Section(
            "A few simple rules to keep this place friendly and safe.\n-# Only you can see them · By chatting here, "
            "you agree to them", accessory=discord.ui.Button(label="Read the Rules", custom_id=RULES))]),
        footer=f"What Lookout stores, how long it keeps it, and how to get it deleted.\nOn the Lookout Network by "
               f"mistake? Open a post in <#{SUPPORT}>.",
        buttons=[discord.ui.Button(label="Terms of Service", style=link, url=config.TERMS_URL),
                 discord.ui.Button(label="Privacy Policy", style=link, url=config.PRIVACY_URL)],
        subtitle="The official support server", icon="brand", banner=banner)


def rules_view() -> discord.ui.LayoutView:
    text = "\n\n".join(f"**{i}. {name}**\n{body}" for i, (name, body) in enumerate(RULE_LIST, 1))
    return ui_kit.card("Server Rules", text, subtitle="Breaking these can get you warned, timed out or banned.",
                       footer="By chatting here, you agree to these rules.")


def _has_rules_button(message: discord.Message) -> bool:
    try:
        view = discord.ui.LayoutView.from_message(message, timeout=None)
    except Exception:
        return False
    return any(getattr(item, "custom_id", None) == RULES for item in view.walk_children())


async def post_welcome(interaction: discord.Interaction):
    await ui_kit.post_card(interaction, lambda perms: welcome_card(interaction.client, banner=perms.attach_files),
                           _has_rules_button, "welcome message", "Run /post-welcome here again anytime to update it.")


# only synced to the support server (bot.py)
POST_WELCOME = app_commands.Command(name="post-welcome", description="Post the welcome message here, with a Read the "
                                                                     "Rules button (owner only)", callback=post_welcome)
POST_WELCOME.default_permissions = discord.Permissions(administrator=True)
POST_WELCOME.guild_only = True


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_command = POST_WELCOME

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component and ui_kit.custom_id(interaction) == RULES:
            await interaction.response.send_message(view=rules_view(), ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Welcome(bot))
