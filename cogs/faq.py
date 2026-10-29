import discord
from discord import app_commands
from discord.ext import commands

import chart
import config
import ui_kit

PICK = "lkfaq:pick"  # one dropdown per topic: lkfaq:pick:<topic>
BANNER = "faq-banner.png"
SOURCE_URL = "https://github.com/kwvn/Lookout"
TERMS_URL = "https://mammoth-elbow-750.notion.site/Lookout-Terms-of-Service-811dfe2233704bf780b9433ddee23df3"
SUPPORT_CHANNEL = 1553440549455925319  # in the support server


def _invite(bot) -> str:
    return discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions(config.INVITE_PERMISSIONS),
                                   scopes=("bot", "applications.commands"))


# topic: (name, [(key, question, summary, answer, link)]). answers come from here when picked, so editing one needs
# no repost. adding or removing a question does, run /post-faq again
TOPICS = {
    "start": ("Getting Started", [
        ("setup", "How do I set up Lookout?", "Setup takes about a minute.",
         "Add Lookout to your server, then press **Set Up** on its welcome message or run /setup. Pick your mod channel, "
         "your mod role and your age roles. That's all it needs.", "invite"),
        ("teen", "How does Lookout know who's a teen?", "Your age roles, or Teen by Default.",
         "The safest way is **Teen by Default**: you only pick an 18+ role, and everyone without it is protected as a "
         "teen. Nobody gets a role that shows they're under 18.\n-# Give the 18+ role out in an age-restricted channel, "
         "so only adults Discord has verified can get it. Servers that already have teen and adult roles can use those.",
         None),
        ("perms", "What permissions does Lookout need?", "No Administrator. It can't ban or kick.",
         "Lookout doesn't need Administrator, and it can't ban, kick or manage roles. It removes messages and alerts "
         "your mods. Your mods make every decision.", None),
        ("free", "Is Lookout free?", "Every feature, for every server.",
         "Yes. Every feature is free for every server. Lookout is also open source, so anyone can check what it does.",
         "source"),
        ("checkup", "What is the Safety Checkup?", "A check of your server's own settings.",
         "A button in /setup that checks your server's own Discord settings, like the verification level and which "
         "channels teens can see, and tells you what to fix. It never changes anything itself.", None),
    ]),
    "safety": ("Safety Features", [
        ("normal", "Will Lookout flag normal conversations?", "No. Normal chat is left alone.",
         "No. Adults and teens talking is normal. Lookout only looks at messages an adult sends to a teen, and only acts "
         "on inappropriate messages or contact info. If it gets one wrong, your mods press **False Alarm**.", None),
        ("remove", "What does Lookout remove?", "Inappropriate messages and contact info sent to teens.",
         "Inappropriate messages an adult sends to a teen, like asking them to keep a secret, asking if they're home "
         "alone, or trying to meet up. Meet-up requests are marked urgent. It also removes phone numbers, emails and "
         "social handles sent to teens, even when they're spelled to get past filters.\n\nThreats to share a teen's "
         "pictures and requests for pictures are removed from anyone, including other teens. Asking a teen's age or "
         "saying \"dm me\" isn't removed, but counts as a warning sign.", None),
        ("removed", "What happens when a message is removed?", "The sender, the teen and your mods are told.",
         "The sender is told why. The teen gets a calm heads-up with a **Tell the Mods** button, and your mods get an "
         "alert with a copy of the message.", None),
        ("images", "Does Lookout check images?", "Yes, including Discord's own image labels.",
         "Yes, with the image filter on. Inappropriate images are removed in channels teens can see. Lookout also uses "
         "Discord's own labels for explicit, gore and self-harm images.\n\nIf something looks like self-harm, Lookout "
         "removes it, sends that person a private message with a helpline, and alerts your mods so they can check in.",
         None),
        ("fake", "What if an adult uses a teen role?", "Their messages to teens are checked like an adult's.",
         "Lookout notices when someone with a teen role says they're 18 or older, has an account older than their age "
         "role allows, or is on the Lookout Network. From then on, their messages to teens are checked like an "
         "adult's, and your mods get an alert.\n\nIf they really are a teen, a mod can press **They're a Teen** to "
         "undo it.", None),
        ("reports", "Can false reports restrict a member?", "Not from new accounts or alts.",
         "If 2 different teens report the same adult, Lookout keeps that adult away from teens until your mods decide. "
         "Teens who joined in the last week or have a new account don't count, so alt accounts can't trigger it.",
         None),
        ("safe", "What is Is this safe?", "A private check for any message, even in DMs.",
         "Members can right-click any message, even in a DM, and pick **Apps** > **Is this safe?**. Lookout privately "
         "tells them if it looks like a known red flag, and what to do. It doesn't save the message.\n-# They need to "
         "add Lookout to their apps first.", "apps"),
    ]),
    "privacy": ("Privacy and Data", [
        ("dms", "Does Lookout read DMs or listen to voice?", "No, not on its own.",
         "No. Lookout only sees a DM if someone reports it or checks it with **Is this safe?**, and then only that one "
         "message. It never joins voice channels.", None),
        ("report_dm", "How do members report a DM?", "Add Lookout to their apps, then right-click.",
         "They add Lookout to their apps, then right-click the DM and pick **Apps** > **Report Message to Mods**. "
         "Lookout asks which server's mods should see it, and only lists servers they're both in. It comes straight "
         "from Discord, so it can't be faked.", "apps"),
        ("store", "What data does Lookout store?", "Only what mods need, for a limited time.",
         "Only what your mods need to handle reports and keep teens safe: settings, reports, warnings and safety "
         "records. Messages aren't stored unless Lookout removes one or someone reports it. Most records are deleted "
         "after 90 days, and warning signs after 30.", "privacy"),
        ("delete", "How do I request data deletion?", "Open a post with your user ID.",
         f"Open a post in <#{SUPPORT_CHANNEL}> with your Discord user ID. When Lookout is removed from a server, that "
         "server's data is deleted right away. Lookout Network records stay for 12 months unless an appeal removes "
         "them.", "privacy"),
        ("terms", "What are Lookout's terms?", "The short version of the rules.",
         "Follow Discord's rules, don't send false reports, and don't try to get someone onto the Lookout Network on "
         "purpose. Server owners need to set up age roles accurately and tell members Lookout is there. Lookout is "
         "free and provided as is, and your mods make every final decision.", "terms"),
    ]),
    "network": ("Lookout Network", [
        ("network", "What is the Lookout Network?", "Servers warning each other about repeat offenders.",
         "If Lookout's filter catches someone sending inappropriate messages to teens in 2 servers, with 2 different "
         "teens, other servers that use Lookout get a heads-up when that person starts chatting there. Mods can't add "
         "anyone, and the heads-up never says which servers or what was said.", None),
        ("mistake", "What if I'm listed by mistake?", "Contact us and we'll review it.",
         f"Open a post in <#{SUPPORT_CHANNEL}>. We'll check what Lookout caught and remove you if it was a mistake.",
         None),
        ("optout", "Can a server turn off the Network?", "Yes, in /setup > Extras.",
         "Yes, in /setup > **Extras**. The server then stops sharing catches and getting heads-ups, and what Lookout "
         "already caught there is taken off the network.", None),
    ]),
}
QUESTIONS = {key: (topic, question, answer, link) for topic, (_, items) in TOPICS.items()
             for key, question, _, answer, link in items}


def _link(bot, kind: str | None) -> discord.ui.Button | None:
    link = discord.ButtonStyle.link
    if kind == "invite":
        return discord.ui.Button(label="Add Lookout", style=link, url=_invite(bot))
    if kind == "apps":
        return ui_kit.add_app_button()
    if kind == "source":
        return discord.ui.Button(label="Source Code", style=link, url=SOURCE_URL)
    if kind == "privacy":
        return discord.ui.Button(label="Privacy Policy", style=link, url=config.PRIVACY_URL)
    if kind == "terms":
        return discord.ui.Button(label="Terms of Service", style=link, url=TERMS_URL)
    return None


def faq_view(bot) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    box = discord.ui.Container()
    data = chart.banner("help", "Frequently Asked Questions", "Lookout Help Center", mascot=False)
    if data:
        box.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{BANNER}",
                                                                      description="Frequently Asked Questions")))
        view.banner = (BANNER, data)
    else:
        box.add_item(discord.ui.TextDisplay("# Frequently Asked Questions"))
    box.add_item(discord.ui.TextDisplay("Find answers to the most common questions about Lookout. Choose a topic below. "
                                        "Answers are only visible to you.\n-# Browse by topic"))
    for topic, (name, items) in TOPICS.items():
        box.add_item(discord.ui.ActionRow(discord.ui.Select(
            custom_id=f"{PICK}:{topic}", placeholder=name,
            options=[discord.SelectOption(label=q, value=key, description=summary) for key, q, summary, _, _ in items])))
    box.add_item(discord.ui.Separator())
    box.add_item(discord.ui.TextDisplay(f"-# Can't find what you're looking for? Open a post in <#{SUPPORT_CHANNEL}> "
                                        "and our team will help."))
    box.add_item(discord.ui.ActionRow(_link(bot, "invite")))
    view.add_item(box)
    return view


def answer_view(bot, key: str) -> discord.ui.LayoutView:
    topic, question, answer, link = QUESTIONS[key]
    box = discord.ui.Container(discord.ui.TextDisplay(f"### {question}\n{answer}"), discord.ui.Separator(),
                               discord.ui.TextDisplay(f"-# Lookout FAQ · {TOPICS[topic][0]}"))
    button = _link(bot, link)
    if button:
        box.add_item(discord.ui.ActionRow(button))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(box)
    return view


def _is_faq(message: discord.Message) -> bool:
    try:
        view = discord.ui.LayoutView.from_message(message, timeout=None)
    except Exception:
        return False
    return any(str(getattr(item, "custom_id", None) or "").startswith(PICK) for item in view.walk_children())


async def post_faq(interaction: discord.Interaction):
    bot, channel = interaction.client, interaction.channel
    if not await bot.is_owner(interaction.user):
        return await interaction.response.send_message(view=ui_kit.note("Only Lookout's owner can use this."),
                                                       ephemeral=True)
    perms = channel.permissions_for(interaction.guild.me)
    if not (perms.view_channel and perms.send_messages):
        return await interaction.response.send_message(view=ui_kit.note(
            "Lookout can't post here. It needs **Send Messages** in this channel."), ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    view = faq_view(bot)
    if perms.read_message_history:
        async for message in channel.history(limit=50):
            if message.author.id == bot.user.id and _is_faq(message):
                await message.edit(view=view, attachments=ui_kit.files(view))
                return await interaction.followup.send(view=ui_kit.note(f"**Updated the FAQ in {channel.mention}.**"),
                                                       ephemeral=True)
    await channel.send(view=view, files=ui_kit.files(view), allowed_mentions=discord.AllowedMentions.none())
    await interaction.followup.send(view=ui_kit.note(f"**Posted in {channel.mention}.** Run /post-faq here again "
                                                     "after adding or removing a question."), ephemeral=True)


# only synced to the support server (bot.py)
POST_FAQ = app_commands.Command(name="post-faq", description="Post the FAQ here, one message with dropdowns "
                                                             "(owner only)", callback=post_faq)
POST_FAQ.default_permissions = discord.Permissions(administrator=True)
POST_FAQ.guild_only = True


class Faq(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_command = POST_FAQ

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        cid = str((interaction.data or {}).get("custom_id") or "")
        if interaction.type != discord.InteractionType.component or not cid.startswith(PICK):
            return
        key = ((interaction.data or {}).get("values") or [""])[0]
        if key not in QUESTIONS:
            return await interaction.response.send_message(view=ui_kit.note(
                "That question isn't in the FAQ anymore. Pick another one."), ephemeral=True)
        await interaction.response.send_message(view=answer_view(self.bot, key), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
        try:  # reset the dropdowns, so the same question can be picked again. the banner stays attached
            await interaction.message.edit(view=faq_view(self.bot))
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Faq(bot))
