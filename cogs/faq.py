import discord
from discord import app_commands
from discord.ext import commands

import config
import ui_kit

PICK = "lkfaq:pick"  # one dropdown per topic: lkfaq:pick:<topic>
SOURCE_URL = "https://github.com/kwvn/Lookout"
SUPPORT_CHANNEL = 1553440549455925319  # in the support server


# topic: (name, [(key, question, summary, answer, link)]). answers come from here when picked, so editing one needs
# no repost. adding or removing a question does, run /post-faq again
TOPICS = {
    "start": ("Getting Started", [
        ("setup", "How do I set up Lookout?", "Setup takes about a minute.",
         "Add Lookout to your server, then press **Set Up** on its welcome message or run /setup. Pick your mod channel, "
         "your mod role and your age roles. That's all it needs.", "invite"),
        ("scan", "Can we try Lookout before it does anything?", "Yes, with a free 7-day Safety Scan.",
         "Add Lookout and press **Free Safety Scan** on its welcome message, or in /setup. For 7 days it only "
         "watches: nothing is removed, nobody gets a message and your mods aren't pinged. Then your mods get a report "
         "of everything it would have done, with a link to each message. Turn it on from there, or don't.\n-# The "
         "scan-only invite doesn't even give Lookout permission to delete messages. Threats to share a kid's pictures "
         "and self-harm images still go to your mods right away.", "scan"),
        ("teen", "How does Lookout know who's a teen?", "Your age roles, Teen by Default, or Auto-Detect.",
         "The safest way is **Teen by Default**: you only pick an 18+ role, and everyone without it is protected as a "
         "teen. Nobody gets a role that shows they're under 18.\n-# Give the 18+ role out in an age-restricted channel, "
         "so only adults Discord has verified can get it. Servers that already have teen and adult roles can use those."
         "\n\nNo age roles at all? Use **Auto-Detect**. Members who say they're under 18, like \"im 14\" or \"8th "
         "grade\", are protected from then on, and so are names like \"mia 14yo\".", None),
        ("auto", "How does Auto-Detect work?", "Members who say they're under 18 get protected.",
         "When a member says their age, their school year or the year they were born, Lookout remembers it for that "
         "server and protects them if they're under 18. It goes up by a year every year, and is deleted after 12 "
         "months. Lookout only saves the age and a link to the message, never the message itself.\n\nSaying an adult "
         "age sticks. If someone said they're 24 and later says they're 15, Lookout keeps treating them as an adult "
         "and tells your mods.", None),
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
        ("roblox", "Does Lookout work for Roblox communities?", "Yes, without flagging normal Roblox chat.",
         "Roblox servers rarely have age roles, so use **Auto-Detect**. Kids who say they're 10 or older are "
         "protected, and kids asking each other's age or to play together never counts against anyone.\n\nAn adult "
         "offering a kid Robux or money in return for something counts as a warning sign, and members can check a "
         "DM like that with **Is this safe?**.", None),
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
         "anyone, and the heads-up never says which servers or what was said.\n\nIt also catches what one server "
         "can't see: an adult showing warning signs toward 3 or more teens across other servers, like asking their "
         "age, asking them to DM, or offering Robux for something. And someone who says they're 15 in one server "
         "but said they're 24 in another gets checked like an adult.", None),
        ("fishing", "Why tell us about warning signs from other servers?", "One server sees one question, not the "
                                                                           "pattern.",
         "Predators rarely stick to one server. They join lots of them and go after a different kid in each one. In "
         "each server it looks like one normal question. Lookout is in all of them, so it can see the pattern and "
         "warn the servers that person is in.\n-# It takes 3 teens in 2 other servers and more than just asking "
         "ages. Servers that have had Lookout under 2 weeks don't count, so nobody can make a server just to put "
         "someone on it.", None),
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
        return discord.ui.Button(label="Add Lookout", style=link, url=ui_kit.invite_url(bot))
    if kind == "scan":
        return discord.ui.Button(label="Add Lookout for a Safety Scan", style=link, url=ui_kit.invite_url(bot, scan=True))
    if kind == "apps":
        return ui_kit.add_app_button()
    if kind == "source":
        return discord.ui.Button(label="Source Code", style=link, url=SOURCE_URL)
    if kind == "privacy":
        return discord.ui.Button(label="Privacy Policy", style=link, url=config.PRIVACY_URL)
    if kind == "terms":
        return discord.ui.Button(label="Terms of Service", style=link, url=config.TERMS_URL)
    return None


def faq_view(bot) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    ui_kit.head(view, "Frequently Asked Questions", "Lookout Help Center", "help")
    box = discord.ui.Container()
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
    await ui_kit.post_card(interaction, lambda perms: faq_view(interaction.client), _is_faq, "FAQ",
                           "Run /post-faq here again after adding or removing a question.")


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
        try:  # reset the dropdowns, so the same question can be picked again
            await interaction.message.edit(view=faq_view(self.bot))
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Faq(bot))
