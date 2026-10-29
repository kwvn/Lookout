import discord
from discord import app_commands
from discord.ext import commands

import config
import ui_kit

PICK = "lkfaq:pick"
SOURCE_URL = "https://github.com/kwvn/Lookout"
SUPPORT_CHANNEL, IDEAS_CHANNEL = 1553440549455925319, 1553424915317661806  # in the support server


def _invite(bot) -> str:
    return discord.utils.oauth_url(bot.user.id, permissions=discord.Permissions(config.INVITE_PERMISSIONS),
                                   scopes=("bot", "applications.commands"))


# key: (question, answer, extra button). answers come from here when picked, so editing one needs no repost.
# adding or removing a question does, run /post-faq again
FAQ = {
    "what": ("What does Lookout do?",
             "It keeps teens safe in servers where teens and adults hang out together. Normal chat is left alone. "
             "Lookout steps in when an adult sends a teen something inappropriate or their contact info, and it gives "
             "your mods private reports and one place to handle them.", None),
    "setup": ("How do I set it up?",
              "Add Lookout, then press **Set Up** on its welcome message or run /setup. Pick your mod channel, mod role "
              "and age roles. It takes about a minute.", "invite"),
    "teen": ("How does it know who's a teen?",
             "The safest way is **Teen by Default**: you only pick an 18+ role, and everyone without it is protected as "
             "a teen. Nobody gets a role that shows they're under 18.\n-# Give the 18+ role out in an age-restricted "
             "channel, so only adults Discord has verified can get it. Servers that already have teen and adult roles "
             "can still use those.", None),
    "fake": ("What if an adult picks a teen role?",
             "Lookout notices when someone with a teen role says they're 18 or older, has an account older than their "
             "age role allows, or is on the Lookout Network. Their messages to teens then get checked like an adult's, "
             "and the mods get a heads-up. If they really are a teen, a mod presses **They're a Teen**.", None),
    "normal": ("Will it flag normal chat?",
               "No. Adults and teens talking is normal. Lookout only looks at messages an adult sends to a teen, and only "
               "acts on inappropriate messages or contact info. If it gets one wrong, your mods press **False Alarm**.",
               None),
    "catch": ("What does it catch?",
              "Inappropriate messages an adult sends to a teen, like asking them to keep it secret, asking if they're "
              "home alone, or trying to meet up. Trying to meet up is marked urgent. It also removes phone numbers, "
              "emails and social handles sent to teens, even when they're spelled to get past filters.\n"
              "Threats to share a teen's pictures and asking a teen for pictures are removed from anyone, even other "
              "teens. Asking a teen's age or saying \"dm me\" isn't removed, but counts as a warning sign.", None),
    "images": ("Does it check images?",
               "Yes, with the image filter on. Inappropriate images are removed in channels teens can see. Lookout also "
               "uses Discord's own labels for explicit, gore and self-harm images. If something looks like self-harm, "
               "Lookout removes it, sends that person a private message with a helpline, and tells the mods so they "
               "can check in.", None),
    "caught": ("What happens when it catches something?",
               "The message is removed and the sender is told why. The teen gets a calm heads-up with a **Tell the "
               "Mods** button, and your mods get an alert with a copy of the message.", None),
    "reports": ("Can fake reports get someone kept away from teens?",
                "If 2 different teens report an adult, Lookout keeps them away from teens until the mods decide. Teens "
                "who joined this week or have a new account don't count, so alts can't do it.", None),
    "safe": ("What's Is this safe?",
             "Right-click any message, even in a DM, then **Apps** > **Is this safe?**. Lookout tells only you if it "
             "looks like a known red flag, and what to do. It doesn't save the message.\n-# Add Lookout to your apps "
             "first.", "apps"),
    "network": ("What's the Lookout Network?",
                "If Lookout's filter catches someone sending inappropriate messages to teens in 2 servers, with 2 "
                "different teens, other servers that use Lookout get a heads-up when they start chatting there. Mods "
                "can't add anyone, and the heads-up never says which servers or what was said. You can turn it off in "
                "/setup > **Extras**.", None),
    "mistake": ("On the Lookout Network by mistake?",
                f"Open a post in <#{SUPPORT_CHANNEL}>. We check what Lookout caught and take you off if it was a "
                "mistake.", None),
    "dms": ("Does it read DMs or listen to voice?",
            "Not on its own. Lookout only sees a DM if you report it or check it with **Is this safe?**, and only that "
            "one message. It never joins voice channels.", None),
    "report_dm": ("Can members report a DM?",
                  "Yes. They add Lookout to their apps, then right-click the DM > **Apps** > **Report Message to Mods**. "
                  "Lookout asks which server's mods should see it, and only lists servers they're both in. It comes "
                  "straight from Discord, so it can't be faked.", "apps"),
    "ban": ("Can it ban or kick people?",
            "No. Lookout doesn't need Administrator and can't ban, kick or manage roles. Your mods make every call, even "
            "after a Lookout Network heads-up.", None),
    "checkup": ("What's the Safety Checkup?",
                "A button in /setup that checks your server's own Discord settings, like the verification level and "
                "which channels teens can see, and tells you what to fix.", None),
    "free": ("Is Lookout free?", "Yes. Every feature, for every server, and it's open source.", "source"),
    "ideas": ("Found a bug or have an idea?", f"Open a post in <#{SUPPORT_CHANNEL}> or <#{IDEAS_CHANNEL}>.", None),
    "data": ("How do I get my data deleted?",
             f"Open a post in <#{SUPPORT_CHANNEL}> with your Discord user ID. When Lookout is removed from a server, "
             "that server's data is deleted right away. Lookout Network records stay for 12 months unless a network "
             "appeal removes them.", "privacy"),
}


def _button(bot, kind: str | None) -> discord.ui.Button | None:
    link = discord.ButtonStyle.link
    if kind == "invite":
        return discord.ui.Button(label="Add Lookout", style=link, url=_invite(bot))
    if kind == "apps":
        return ui_kit.add_app_button()
    if kind == "source":
        return discord.ui.Button(label="Source Code", style=link, url=SOURCE_URL)
    if kind == "privacy":
        return discord.ui.Button(label="Privacy Policy", style=link, url=config.PRIVACY_URL)
    return None


def faq_view(bot) -> discord.ui.LayoutView:
    pick = discord.ui.Select(custom_id=PICK, placeholder="Pick a question...",
                             options=[discord.SelectOption(label=q, value=key) for key, (q, _, _) in FAQ.items()])
    buttons = [b for b in (_button(bot, "invite"), _button(bot, "apps"), _button(bot, "privacy")) if b]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay(f"## {ui_kit.titled(ui_kit.mascot('think', 'help'), 'Frequently Asked Questions')}\n"
                               "Pick a question below. Only you see the answer."),
        discord.ui.ActionRow(pick),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"-# Still stuck? Open a post in <#{SUPPORT_CHANNEL}>."),
        discord.ui.ActionRow(*buttons)))
    return view


def _is_faq(message: discord.Message) -> bool:
    try:
        view = discord.ui.LayoutView.from_message(message, timeout=None)
    except Exception:
        return False
    return any(getattr(item, "custom_id", None) == PICK for item in view.walk_children())


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
    if perms.read_message_history:
        async for message in channel.history(limit=50):
            if message.author.id == bot.user.id and _is_faq(message):
                await message.edit(view=faq_view(bot))
                return await interaction.followup.send(view=ui_kit.note(f"**Updated the FAQ in {channel.mention}.**"),
                                                       ephemeral=True)
    await channel.send(view=faq_view(bot), allowed_mentions=discord.AllowedMentions.none())
    await interaction.followup.send(view=ui_kit.note(f"**Posted in {channel.mention}.** Run /post-faq here again "
                                                     "after adding or removing a question."), ephemeral=True)


# only synced to the support server (bot.py)
POST_FAQ = app_commands.Command(name="post-faq", description="Post the FAQ here, one message with a dropdown "
                                                             "(owner only)", callback=post_faq)
POST_FAQ.default_permissions = discord.Permissions(administrator=True)
POST_FAQ.guild_only = True


class Faq(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_command = POST_FAQ

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or (interaction.data or {}).get("custom_id") != PICK:
            return
        key = ((interaction.data or {}).get("values") or [""])[0]
        if key not in FAQ:
            return await interaction.response.send_message(view=ui_kit.note(
                "That question isn't in the FAQ anymore. Pick another one."), ephemeral=True)
        question, answer, extra = FAQ[key]
        button = _button(self.bot, extra)
        await interaction.response.send_message(view=ui_kit.card(question, answer, buttons=[button] if button else None),
                                                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        try:  # reset the dropdown, so the same question can be picked again
            await interaction.message.edit(view=faq_view(self.bot))
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Faq(bot))
