import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import ui_kit


TIPS = [
    ("Keep Your Info to Yourself", "Your real name, age, school, city and number don't belong in your profile or "
                                   "chats. People online aren't always who they say they are."),
    ("Control Who Can DM You", "Click the server's name, open **Privacy Settings** and turn off **Direct Messages**.\n"
                               "-# DMs from people who aren't your friends go to **Message Requests** first. You don't "
                               "have to open them."),
    ("Red Flags", "Be careful if someone you only know online:\n- asks your age, where you live or if you're alone\n"
                  "- wants your chats kept secret\n- offers Nitro, Robux or gifts\n- says you're mature for your age\n"
                  "- asks for pics, a call, or to move to another app"),
    ("Never Share These", "Your password or login codes. And never scan a QR code someone sends you. It can hand "
                          "over your account."),
    ("If Something Feels Off", "Leave the chat or call. You don't owe anyone a reply.\n"
                               "-# If Discord warns you about a friend request, there's a reason. You can ignore it."),
    ("It's Never Your Fault", "Talk to an adult you trust. You won't get in trouble for someone else's choices.\n"
                              "Going through something hard? You can talk to someone for free, any time."),
]


def tips_message(banner: bool = True) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    ui_kit.head(view, "Safety Tips", ui_kit.titled(ui_kit.mascot("wave"), "Short tips for staying safe on Discord."),
                "warning", banner=banner)
    link = discord.ButtonStyle.link
    add_app = ui_kit.add_app_button()
    buttons = {
        "If Something Feels Off": [discord.ui.Button(label="Report a Problem", custom_id="lkreport:open")]
                                  + ([add_app] if add_app else []),
        "It's Never Your Fault": [discord.ui.Button(label="Someone's Threatening Me", custom_id="lkhelp:threat"),
                                  discord.ui.Button(label="Find a Helpline", style=link, url=ui_kit.HELPLINE_URL)],
    }
    for title, text in TIPS:
        if title == "If Something Feels Off" and add_app:
            text += ("\n-# Got a DM that's not okay? Add Lookout to your apps, then right-click it > **Apps** > "
                     "**Report Message to Mods**.")
        box = discord.ui.Container(discord.ui.TextDisplay(f"## {title}\n{text}"))
        if title in buttons:
            box.add_item(discord.ui.ActionRow(*buttons[title]))
        view.add_item(box)
    return view


# blackmail with a photo. private, the mods only hear about it if they press the button
def threat_help() -> discord.ui.LayoutView:
    link = discord.ButtonStyle.link
    add_app = ui_kit.add_app_button()
    return ui_kit.card(
        ui_kit.titled("warning", "If Someone's Threatening You"),
        "**It's not your fault, and you're not in trouble.** If someone says they'll share a photo or video of you "
        "unless you pay or send more, here's what to do.",
        ("Don't Pay or Send More", "Paying doesn't make them stop. They almost always ask for more."),
        ("Keep the Chat, Then Block", "Don't delete anything. It's proof. Report it first, then block them."
                                      + ("\n-# Got it in a DM? Add Lookout to your apps, then right-click it > "
                                         "**Apps** > **Report Message to Mods**." if add_app else "")),
        ("Stop It From Spreading", "**Take It Down** makes a fingerprint of the photo on your phone, so sites that use "
                                   "it can find and remove copies. The photo itself never leaves your phone."),
        ("Tell Someone", "A parent or another adult you trust can help you report it. You can also report it to "
                         "NCMEC's CyberTipline yourself."),
        footer="Outside the US? Your local police can help too.",
        buttons=[discord.ui.Button(label="Take It Down", style=link, url="https://takeitdown.ncmec.org"),
                 discord.ui.Button(label="CyberTipline", style=link, url="https://report.cybertip.org"),
                 discord.ui.Button(label="Tell the Mods", custom_id="lkreport:open")])


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _cmd(self, name: str) -> str:
        cid = self.bot.command_ids.get(name)
        return f"</{name}:{cid}>" if cid else f"`/{name}`"

    @app_commands.command(name="help", description="See what Lookout does and how to use it")
    @app_commands.guild_only()
    async def help(self, interaction: discord.Interaction):
        user = interaction.user
        c = self._cmd
        s = db.get_settings(interaction.guild.id)

        add_app = ui_kit.add_app_button()
        looky = ui_kit.mascot
        items = [("report", ui_kit.titled(looky("noted"), "Report a Problem"),
                  f"{c('report')} lets the mods know something's wrong. Only they can see it.\n-# You can also "
                  "right-click someone or a message, then **Apps**."
                  + ("\n-# Got a DM that's not okay? Add Lookout to your apps, then right-click it > **Apps** > "
                     "**Report Message to Mods**." if add_app else ""),
                  [add_app] if add_app else None)]
        if s.teen_safety:
            items.append(("brand", ui_kit.titled(looky("watching"), "Teen Safety Is On"),
                          "Inappropriate messages and contact info that adults send to teens get removed, and the "
                          "mods are told.\n-# Lookout never reads your DMs or listens to voice. It only sees a DM "
                          "you report yourself.", None))
        if isinstance(user, discord.Member) and self.bot.is_staff(user):
            items.append(("staff", ui_kit.titled(looky("watching"), "For Mods"), "\n".join([
                f"{c('cases')} Browse open and past alerts",
                f"{c('history')} See a member's warnings, reports and flags",
                f"{c('warn')} Warn a member. They get a DM.",
                f"{c('summary')} The last 7 days in numbers and charts",
                f"{c('trivia')} Host a safety trivia game in the trivia channel",
            ]), None))
        if isinstance(user, discord.Member) and user.guild_permissions.manage_guild:
            items.append(("settings", ui_kit.titled(looky("knight"), "For Admins"),
                          f"{c('setup')} Every setting in one place, plus a Safety Checkup for your server\n"
                          f"-# In {c('summary')}, admins also get Mod Stats. A weekly summary goes to the mod "
                          "channel every Monday.",
                          None))
        items.append(("warning", ui_kit.titled(looky("love"), "Safety Tips"), "Tips for staying safe on Discord.",
                      [discord.ui.Button(label="Safety Tips", custom_id="lkhelp:tips"),
                       discord.ui.Button(label="See a Demo", custom_id="lkhelp:demo")]))
        items.append(("help", ui_kit.titled(looky("thumbsup"), "Need Help?"),
                      "Questions or something not working? Ask us in the support server.\n"
                      "-# The Privacy Policy says what Lookout keeps and how long it keeps it.",
                      [discord.ui.Button(label="Support Server", style=discord.ButtonStyle.link, url=config.SUPPORT_URL),
                       discord.ui.Button(label="Privacy Policy", style=discord.ButtonStyle.link,
                                         url=config.PRIVACY_URL)]))
        view = ui_kit.boxes("Help", "What Lookout does in this server", items, icon="help")
        await interaction.response.send_message(view=view, files=ui_kit.files(view), ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id")
        if cid == "lkhelp:tips":
            view = tips_message()
            await interaction.response.send_message(view=view, files=ui_kit.files(view), ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())
        elif cid.startswith("lkappeal:"):  # Appeal buttons on old warnings, appeals were removed
            await interaction.response.send_message(view=ui_kit.note(
                "Appeals through Lookout were turned off. Think a warning was a mistake? Talk to the server's mods."),
                ephemeral=True)
        elif cid == "lkhelp:threat":
            await interaction.response.send_message(view=threat_help(), ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())
        elif cid == "lkhelp:demo":
            demo = self.bot.get_cog("Demo")
            if demo:
                view, files = await demo.first_page(interaction.user)
                await interaction.response.send_message(view=view, files=files, ephemeral=True,
                                                        allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Help(bot))
