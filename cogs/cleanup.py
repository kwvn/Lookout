import time

import discord
from discord.ext import commands

import db
import ui_kit

PREFIX = "lkclean"


def _ago(ts: float) -> str:
    s = int(time.time() - ts)
    for unit, secs in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= secs:
            return f"{s // secs}{unit} ago"
    return "just now"


def history_controls(member_id: int, rows: list[tuple]) -> list[discord.ui.Item]:
    from cogs.staff import LABELS
    options = [discord.SelectOption(label=f"{LABELS.get(kind, kind)} · {_ago(created)}"[:100],
                                    description=(detail or "No details")[:100], value=str(eid))
               for kind, detail, _, _, created, eid in rows[:25]]
    return [
        discord.ui.Select(custom_id=f"{PREFIX}:entry:{member_id}", placeholder="Remove an entry...", options=options),
        discord.ui.Button(label="Clear Warnings", custom_id=f"{PREFIX}:ask:warnings:{member_id}"),
        discord.ui.Button(label="Clear All History", custom_id=f"{PREFIX}:ask:everything:{member_id}"),
    ]


# (title, what happens, the button that does it)
CONFIRM = {
    "warnings": ("Clear Warnings?", "This removes every warning <@{t}> has. You can't undo this.", "Clear Warnings"),
    "everything": ("Clear All History?", "This deletes everything Lookout has on <@{t}>, including their cases. You "
                                         "can't undo this.", "Clear History"),
    "case": ("Delete Case #{t}?", "This permanently deletes the case and everything on it. You can't undo this.",
             "Delete Case"),
}
DONE_TEXT = {
    "warnings": "Removed {n} from <@{t}>.",
    "everything": "Cleared <@{t}>'s history ({n}).",
    "case": "Deleted case #{t}.",
}


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class Cleanup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    async def _reply(interaction, text, edit=False):
        view = ui_kit.note(text)
        if edit:
            await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(view=view, ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        data = interaction.data or {}
        parts = data.get("custom_id", "").split(":")
        if parts[0] != PREFIX:
            return
        mod, guild = interaction.user, interaction.guild
        if not isinstance(mod, discord.Member) or not self.bot.is_staff(mod):
            return await self._reply(interaction, "Only mods can do this.")
        perms = mod.guild_permissions
        action = parts[1]

        if action == "cancel":
            return await self._reply(interaction, "-# Cancelled. Nothing was removed.", edit=True)

        if action == "entry":
            if not perms.moderate_members:
                return await self._reply(interaction, "You need **Timeout Members** to remove entries.")
            eid = int((data.get("values") or ["0"])[0])
            row = db.delete_event(guild.id, eid)
            if not row:
                return await self._reply(interaction, "That entry was already removed.")
            db.log(guild.id, None, "admin_remove", f"removed a {row[0]} entry from {row[1]}", actor_id=mod.id)
            return await self._reply(interaction, f"Removed that entry from <@{row[1]}>'s history.\n"
                                                  "-# Run /history again to see the change.")

        what, target = parts[2], int(parts[3])
        big = what in ("everything", "case")
        if big and not perms.manage_guild:
            return await self._reply(interaction, "You need **Manage Server** for this.")
        if not big and not perms.moderate_members:
            return await self._reply(interaction, "You need **Timeout Members** for this.")

        if action == "ask":
            title, text, label = CONFIRM[what]
            buttons = [discord.ui.Button(label=label, custom_id=f"{PREFIX}:do:{what}:{target}"),
                       discord.ui.Button(label="Cancel", custom_id=f"{PREFIX}:cancel")]
            view = ui_kit.card(title.format(t=target), text.format(t=target), buttons=buttons)
            return await interaction.response.send_message(view=view, ephemeral=True,
                                                           allowed_mentions=discord.AllowedMentions.none())

        if action == "do":
            if what == "case":
                case = db.delete_case(guild.id, target)
                if not case:
                    return await self._reply(interaction, "That case was already deleted.", edit=True)
                n = 1
            else:
                n = db.clear_member(guild.id, target, what)
            done = DONE_TEXT[what].format(n=_count(n, "warning" if what == "warnings" else "record"), t=target)
            db.log(guild.id, None, "admin_clear", done.replace("**", ""), actor_id=mod.id)
            await self._reply(interaction, done, edit=True)


async def setup(bot):
    await bot.add_cog(Cleanup(bot))
