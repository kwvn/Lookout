import time

import discord
from discord.ext import commands

import db
import ui_kit

PREFIX = "lkappeal"


def _find_text(components, custom_id):
    for c in components or []:
        if c.get("custom_id") == custom_id and "value" in c:
            return c["value"]
        inner = c.get("components") or ([c["component"]] if isinstance(c.get("component"), dict) else [])
        found = _find_text(inner, custom_id)
        if found:
            return found
    return None


class Appeals(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    async def _reply(interaction, text):
        await interaction.response.send_message(view=ui_kit.note(text), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type not in (discord.InteractionType.component, discord.InteractionType.modal_submit):
            return
        parts = (interaction.data or {}).get("custom_id", "").split(":")
        if parts[0] != PREFIX or len(parts) < 4:
            return
        try:
            action, guild_id, warning_id = parts[1], int(parts[2]), int(parts[3])
        except ValueError:
            return
        if action in ("open", "send"):
            await self._from_member(interaction, action, guild_id, warning_id)
        elif action in ("accept", "deny"):
            await self._from_staff(interaction, action, guild_id, warning_id)

    # member side (DMs)

    async def _from_member(self, interaction, action, guild_id, warning_id):
        user = interaction.user
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await self._reply(interaction, "Lookout isn't in that server anymore, so you can't appeal here.")
        warning = db.get_event(guild_id, warning_id)
        if not warning or warning["kind"] != "staff_warn" or warning["user_id"] != user.id:
            return await self._reply(interaction, "This warning was already removed, so there's nothing to appeal.")
        s = db.get_settings(guild_id)
        channel = guild.get_channel(s.staff_channel_id) if s.staff_channel_id else None
        if not s.appeals or channel is None:
            return await self._reply(interaction, "This server doesn't take appeals through Lookout. Ask its mods "
                                                  "instead.")
        earlier = db.get_appeal(warning_id)
        if earlier:
            waiting = earlier["status"] == "pending"
            return await self._reply(interaction, "You already appealed this warning. "
                                     + ("The mods haven't decided yet." if waiting else "The mods already decided."))

        if action == "open":
            modal = discord.ui.Modal(title="Appeal This Warning", custom_id=f"{PREFIX}:send:{guild_id}:{warning_id}")
            modal.add_item(discord.ui.TextInput(label="Why should it be removed?", custom_id="text",
                                                style=discord.TextStyle.paragraph, min_length=10, max_length=500,
                                                placeholder="Tell the mods what happened, in your own words."))
            return await interaction.response.send_modal(modal)

        text = (_find_text(interaction.data.get("components"), "text") or "").strip()
        if not text or not db.create_appeal(warning_id, guild_id, user.id, text):
            return await self._reply(interaction, "You already appealed this warning.")
        warnings = db.count_kind(guild_id, user.id, "staff_warn")
        info = ui_kit.kv([
            ("Member", f"{user.mention} · {warnings} warning{'' if warnings == 1 else 's'}"),
            ("Warned", f"<t:{int(warning['created_at'])}:R>"
                       + (f" by <@{warning['actor_id']}>" if warning["actor_id"] else "")),
        ])
        view = ui_kit.card(
            ui_kit.titled("cases", "Warning Appeal"), info,
            ("Warning Reason", discord.utils.escape_markdown(warning["detail"] or "No reason given")),
            ("Their Appeal", discord.utils.escape_markdown(text)),
            subtitle=ui_kit.member_line(user), thumbnail=user.display_avatar.url,
            footer="Accept removes the warning from their history. They get a DM with your decision.",
            buttons=[discord.ui.Button(label="Accept", custom_id=f"{PREFIX}:accept:{guild_id}:{warning_id}"),
                     discord.ui.Button(label="Deny", custom_id=f"{PREFIX}:deny:{guild_id}:{warning_id}")])
        try:
            await channel.send(view=view, allowed_mentions=discord.AllowedMentions.none())   # no ping on purpose
        except discord.HTTPException:
            db.delete_appeal(warning_id)
            return await self._reply(interaction, "Lookout couldn't reach the mods right now. Try again in a bit.")
        await self._reply(interaction, f"**Appeal sent** to the mods of {discord.utils.escape_markdown(guild.name)}.\n"
                                       "-# You'll get a message here when they decide.")

    # staff side

    async def _from_staff(self, interaction, action, guild_id, warning_id):
        mod, guild = interaction.user, interaction.guild
        if guild is None or guild.id != guild_id or not isinstance(mod, discord.Member) or not self.bot.is_staff(mod):
            return await self._reply(interaction, "Only mods can decide appeals.")
        appeal = db.get_appeal(warning_id)
        if appeal is None:
            return await self._reply(interaction, "That appeal doesn't exist anymore.")
        accepted = action == "accept"
        if not db.decide_appeal(warning_id, "accepted" if accepted else "denied", mod.id):
            return await self._reply(interaction, "Someone already decided this one.")

        warning = db.get_event(guild_id, warning_id)
        if accepted and warning:
            db.delete_event(guild_id, warning_id)
        db.log(guild_id, appeal["user_id"], f"staff_appeal_{action}", (warning or {}).get("detail") or "",
               actor_id=mod.id)

        stamp = (f"-# **{'Accepted' if accepted else 'Denied'}** by {mod.mention} · <t:{int(time.time())}:R>"
                 + (" · warning removed" if accepted else " · the warning stays"))
        view = discord.ui.LayoutView.from_message(interaction.message, timeout=None)
        box = ui_kit.action_box(view)
        ui_kit.remove_actions(box)
        box.add_item(discord.ui.TextDisplay(ui_kit.tidy(stamp)))
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())

        result = ui_kit.card(
            "Appeal Accepted" if accepted else "Appeal Denied",
            "Your warning was removed." if accepted else
            "The mods read your appeal and decided the warning stays.",
            subtitle=f"From the mods of {discord.utils.escape_markdown(guild.name)}")
        try:
            user = self.bot.get_user(appeal["user_id"]) or await self.bot.fetch_user(appeal["user_id"])
            await user.send(view=result)
        except discord.HTTPException:
            await interaction.followup.send(view=ui_kit.note("Lookout couldn't DM them the result. Their DMs are closed."),
                                            ephemeral=True)


async def setup(bot):
    await bot.add_cog(Appeals(bot))
