import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui_kit
from cogs import teensafety as ts

log = logging.getLogger("lookout.safecheck")
TAKE_IT_DOWN = "https://takeitdown.ncmec.org"
REPORT = "lkreport"  # the Tell the Mods button is handled in reports.py

# what lookout found, most serious first: (title, what it means, what to do)
VERDICTS = {
    "sextortion": (
        "This Looks Like Sextortion",
        "Threatening to share pictures unless you send more or pay is a common scam. **It's not your fault, and "
        "you're not in trouble.**",
        "1. Don't send anything, and don't pay. Paying makes it worse.\n2. Screenshot the chat, then block them.\n"
        "3. Tell an adult you trust."),
    "pics": (
        "They're Asking for Pictures",
        "Asking for pictures is a big red flag, even if they seem nice or say they're your age. Once a picture is "
        "sent, you can't take it back.",
        "1. Don't send anything.\n2. You don't owe them a reply. You can block them.\n"
        "3. If they keep asking or get angry, tell an adult you trust."),
    "inappropriate": (
        "This Message Isn't Okay",
        "Nobody should send you messages like this, least of all someone you only know online.",
        "You don't have to reply. Block them if you want, and tell the mods or an adult you trust."),
    "move": (
        "They Want to Talk Somewhere Else",
        "Moving the chat to another app or a private place is often the first step for someone who wants you alone, "
        "away from mods and friends.",
        "Keep the chat where other people are around. It's okay to say no."),
    "personal": (
        "They're Asking Personal Questions",
        "Questions about your age, school or where you live can be how someone works out who you are and where to "
        "find you.",
        "You don't have to answer. Keep your age, school and city to yourself."),
}
NOTHING = (
    "No Known Red Flags",
    "Lookout didn't spot a known red flag in this message. That doesn't mean it's safe. **If it still feels off, "
    "trust that.**",
    "Watch out if someone you only know online asks for pictures, wants to keep things secret, offers you gifts, or "
    "wants to move to another app.")
# only picks the advice after the filter already caught something, it never flags a message by itself
_ASKS_PICS = re.compile(r"\b(send|show|take|share)\b.{0,40}\b(pics?|photos?|pictures?|selfies?|vids?|videos?)\b", re.I)


# the same checks the filters use. all the languages lookout knows, there's no server setting in a DM
def verdict(text: str) -> str | None:
    langs = tuple(getattr(ts.wordlists, "NAMES", {"en": "English"}))
    if ts.has_threat(text):
        return "sextortion"
    if ts.has_nude_ask(text):
        return "pics"
    if ts.check_text(text, langs)[0]:  # "send me a pic in your bra" is asking for pictures too
        return "pics" if any(_ASKS_PICS.search(v) for v in ts.variants(text)) else "inappropriate"
    sign = ts.soft_sign(text)
    if sign == "private" or ts.has_contact(text):
        return "move"
    if sign == "probe":
        return "personal"
    return None


class SafeCheck(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # right-click a message > Apps > Is this safe?. works in DMs when the user added the app
        self.menu = app_commands.ContextMenu(
            name="Is this safe?", callback=self.check,
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
            allowed_contexts=app_commands.AppCommandContext(guild=True, private_channel=True))
        self.bot.tree.add_command(self.menu)

    async def cog_load(self):
        if not ui_kit.USER_INSTALL:  # reports.py checks the dev portal setting first
            self.menu.allowed_installs = app_commands.AppInstallationType(guild=True)
            self.menu.allowed_contexts = app_commands.AppCommandContext(guild=True)

    def cog_unload(self):
        self.bot.tree.remove_command(self.menu.name, type=self.menu.type)

    # only checks the one message they picked, in memory. nothing gets saved
    async def check(self, interaction: discord.Interaction, message: discord.Message):
        if message.author.id == interaction.user.id:
            return await interaction.response.send_message(
                view=ui_kit.note("That's your own message. Right-click a message someone sent you."), ephemeral=True)
        if message.author.bot:
            return await interaction.response.send_message(
                view=ui_kit.note("That's from a bot. Right-click a message a person sent you."), ephemeral=True)
        text = message.content or ""
        found = verdict(text) if text else None
        title, means, todo = VERDICTS[found] if found else NOTHING
        if not text and message.attachments:
            means = "Lookout can only check the words in a message, not pictures. " + means
        db.bump("safety_checks")

        buttons = []
        reports = self.bot.get_cog("Reports")
        # no Tell the Mods in a server lookout isn't in, there's no one to tell
        if reports and (interaction.guild_id is None or self.bot.get_guild(interaction.guild_id)):
            # held for 15 minutes so Tell the Mods can use it, same as a normal report
            copy = reports._copy(message)
            if interaction.guild_id is None:
                copy |= {"dm": True, "to_id": interaction.user.id}
            reports._hold(interaction.user.id, copy)
            buttons.append(discord.ui.Button(label="Tell the Mods", custom_id=f"{REPORT}:safetell:{message.id}"))
        if found == "sextortion":
            buttons.append(discord.ui.Button(label="Take It Down", style=discord.ButtonStyle.link, url=TAKE_IT_DOWN))
        if found in ("sextortion", "pics"):
            buttons.append(discord.ui.Button(label="Talk to Someone", style=discord.ButtonStyle.link,
                                             url=ui_kit.HELPLINE_URL))
        icon = "warning" if found else "help"
        view = ui_kit.card(ui_kit.titled(icon, title), means, ("What to Do" if found else "Red Flags", todo),
                           footer="Lookout only looked at this one message. It doesn't keep it unless you press Tell the Mods.", buttons=buttons)
        await interaction.response.send_message(view=view, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(SafeCheck(bot))
