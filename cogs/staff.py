import asyncio
import datetime as dt
import io
import logging
import math
import re

import discord
from discord import app_commands
from discord.ext import commands

import banner
import config
import db
import ui_kit
from cogs.cleanup import history_controls

log = logging.getLogger("lookout.staff")
PREFIX = "lookout"
# same options as discord's own timeout menu, plus Custom
TIMEOUTS = [(1, "60 seconds"), (5, "5 minutes"), (10, "10 minutes"), (60, "1 hour"), (1440, "1 day"), (10080, "1 week")]
MAX_TIMEOUT = 40320  # discord's limit, 28 days in minutes
_UNITS = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440, "w": 10080}
_LINK = re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")


# "3h", "2d", "1d 12h", "45" (minutes) to minutes. 0 if it can't be read
def parse_length(text: str) -> int:
    text = text.lower().replace(" ", "")
    parts = re.findall(r"(\d+)([smhdw]?)", text)
    if not parts or re.sub(r"\d+[smhdw]?", "", text):
        return 0
    return math.ceil(sum(int(n) * _UNITS[u or "m"] for n, u in parts))


def length_label(minutes: int) -> str:
    out = []
    for size, name in ((10080, "week"), (1440, "day"), (60, "hour"), (1, "minute")):
        n, minutes = divmod(minutes, size)
        if n:
            out.append(f"{n} {name}{'' if n == 1 else 's'}")
    return " ".join(out[:2]) or "1 minute"

WARNING_ICON = config.ASSETS / "warning.png"

# some are from removed features, kept for old history entries
LABELS = {
    "vc_report": "Reported", "image_flagged": "Inappropriate image", "speech_flagged": "Flagged speech",
    "auto_action": "Auto-action", "staff_timeout": "Timed out",
    "staff_disconnect": "Disconnected", "staff_false": "False report", "staff_warn": "Warned",
    "staff_resolve": "Resolved an alert", "staff_untimeout": "Timeout removed",
    "staff_appeal_accept": "Appeal accepted", "staff_appeal_deny": "Appeal denied",
    "voicemsg_flagged": "Flagged voice message", "staff_delmsg": "Message deleted",
    "teen_voice": "Kept out of a teen voice channel", "teen_contact": "Contact info to a teen removed",
    "teen_role_switch": "Switched to a teen role", "teen_restricted": "Kept away from teens",
    "teen_unrestricted": "No longer kept away from teens", "teen_photo_reply": "Reply to a teen's photo removed",
    "teen_left": "Left during a case", "teen_flirty": "Inappropriate message to a teen removed",
    "staff_proofsubmit": "Proof added to a case",
}


def _cid(action, member_id=0, channel_id=0, alert_id=0, extra=""):
    return f"{PREFIX}:{action}:{member_id or 0}:{channel_id or 0}:{alert_id or 0}:{extra}"


CLOSE_ACTIONS = ("resolve", "safe", "false")  # "safe" and "false" are from old alerts


def claim_button(member_id, channel_id, claim: tuple[int, str] | None) -> discord.ui.Button:
    label = f"Claimed by {claim[1]}"[:80] if claim else "Claim"
    return discord.ui.Button(label=label, custom_id=_cid("claim", member_id, channel_id))


# Claim and Resolve as buttons, everything else in the Take action… menu (ui_kit.add_action).
# ids live in the custom_id so the buttons survive restarts
def action_buttons(guild_id: int, channel_id: int | None, member_id: int | None,
                   claim: tuple[int, str] | None = None, voice: bool = False) -> list[discord.ui.Item | None]:
    c = channel_id or 0
    items: list[discord.ui.Item | None] = [
        claim_button(member_id, c, claim),
        discord.ui.Button(label="Resolve", custom_id=_cid("resolve", member_id, c)),
    ]
    if member_id:
        ui_kit.add_action(items, "Warn", "They get a DM, and it's saved in their history", _cid("warn", member_id, c))
        ui_kit.add_action(items, "Time Out", "Mute them for a while", _cid("timeout", member_id, c))
        if voice:
            ui_kit.add_action(items, "Disconnect", "Take them out of the voice channel", _cid("disconnect", member_id, c))
    ui_kit.add_action(items, "Add Proof", "Add a message link or a note to this case", _cid("proof", member_id, c))
    return items


def outranks(actor: discord.Member, target: discord.Member) -> bool:
    if target.id == target.guild.owner_id:
        return False
    return actor.id == actor.guild.owner_id or actor.top_role > target.top_role


def _find_value(components, custom_id):
    for c in components or []:
        if c.get("custom_id") == custom_id and "value" in c:
            return c["value"]
        found = _find_value(c.get("components"), custom_id) or (
            _find_value([c["component"]], custom_id) if isinstance(c.get("component"), dict) else None)
        if found:
            return found
    return None


async def send_warning(guild: discord.Guild, mod: discord.Member, target: discord.Member,
                       reason: str, channel_id: int | None) -> str:
    count = db.count_kind(guild.id, target.id, "staff_warn") + 1
    db.log(guild.id, target.id, "staff_warn", reason, channel_id, mod.id)
    icon_file = discord.File(WARNING_ICON, filename="warning.png") if WARNING_ICON.exists() else None
    dm = ui_kit.card("You Got a Warning",
                     ("Reason", discord.utils.escape_markdown(reason)),
                     subtitle=f"From the mods of {discord.utils.escape_markdown(guild.name)}",
                     thumbnail="attachment://warning.png" if icon_file else None,
                     footer=f"This is warning #{count}. More warnings can lead to a timeout. Think it's a "
                            "mistake? Talk to the mods.")
    try:
        await target.send(view=dm, files=[icon_file] if icon_file else [])
        delivered = ""
    except discord.HTTPException:
        delivered = " (their DMs are closed, so they weren't told)"
    return f'warned {target.mention} (#{count}): "{reason[:120]}"{delivered}'


async def profile_image(member: discord.Member) -> discord.File | None:
    try:
        avatar = await member.display_avatar.replace(size=512, static_format="png").read()
        now = discord.utils.utcnow()
        meta = []
        if getattr(member, "joined_at", None):
            meta.append(f"joined {banner.ago(member.joined_at, now)}")
        meta.append(f"account made {banner.ago(member.created_at, now)}")
        png = await asyncio.to_thread(banner.profile_banner, avatar, member.display_name,
                                      member.name, member.id, "  ·  ".join(meta))
        return discord.File(io.BytesIO(png), filename=f"profile-{member.id}.png")
    except Exception:
        log.warning("Could not draw profile banner", exc_info=True)
        return None


class Staff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.guild is None or interaction.type not in (discord.InteractionType.component,
                                                                 discord.InteractionType.modal_submit):
            return
        data = interaction.data or {}
        cid = ui_kit.custom_id(interaction)
        if not cid.startswith(PREFIX + ":"):
            return
        parts = cid.split(":") + ["0", ""]  # old ids have fewer parts
        try:
            action, member_id, channel_id, alert_id = parts[1], int(parts[2]), int(parts[3]), int(parts[4] or 0)
        except ValueError:
            return
        extra = parts[5] if len(parts) > 5 else ""

        mod, guild = interaction.user, interaction.guild
        if not isinstance(mod, discord.Member) or not self.bot.is_staff(mod):
            return await self._reply(interaction, "Only mods can use these.")
        # panels and modals carry the alert id, otherwise it's the clicked message
        alert_id = alert_id or (interaction.message.id if interaction.message else 0)
        now = int(dt.datetime.now().timestamp())

        claim = db.get_claim(alert_id)
        admin = mod.guild_permissions.manage_guild
        if action == "claim":
            if claim is None:
                db.set_claim(guild.id, alert_id, mod.id, mod.display_name)
                return await self._stamp(interaction, alert_id, f"-# claimed by {mod.mention} · <t:{now}:R>",
                                         claim=(mod.id, mod.display_name))
            if claim[0] == mod.id or admin:
                db.set_claim(guild.id, alert_id, None, None)
                return await self._stamp(interaction, alert_id, f"-# {mod.mention} unclaimed this · <t:{now}:R>",
                                         claim=False)
            return await self._reply(interaction, f"<@{claim[0]}> already claimed this one.")
        # any mod can add proof, even to a case someone else claimed
        if claim and claim[0] != mod.id and not admin and action not in ("proof", "proofsubmit"):
            return await self._reply(interaction, f"<@{claim[0]}> claimed this alert, so only they can act on it.")

        if action == "pick":
            member_id = int((data.get("values") or ["0"])[0])
            return await self._open_panel(interaction, member_id, channel_id)

        if action in CLOSE_ACTIONS + ("resolvesubmit",) and db.is_resolved(alert_id):
            return await self._reply(interaction, "Someone already resolved this one.")
        if action in CLOSE_ACTIONS:
            modal = discord.ui.Modal(title="Resolve Alert", custom_id=_cid("resolvesubmit", member_id, channel_id, alert_id))
            modal.add_item(discord.ui.TextInput(label="What did you do? (only mods see this)", custom_id="note",
                                                style=discord.TextStyle.paragraph, min_length=3, max_length=500,
                                                placeholder="Like: Joined the voice channel and saw it myself. Warned "
                                                            "them and timed them out for 1 hour."))
            # only when someone reported it and the server sends reporter updates
            if db.has_reporters(alert_id) and db.get_settings(guild.id).report_updates:
                modal.add_item(discord.ui.TextInput(label="Tell the reporter (optional)", custom_id="reporter",
                                                    style=discord.TextStyle.paragraph, required=False, max_length=300,
                                                    placeholder="Like: We took care of it. Thanks for letting us know!"))
            return await interaction.response.send_modal(modal)
        if action == "resolvesubmit":
            if claim is None:
                db.set_claim(guild.id, alert_id, mod.id, mod.display_name)
            return await self._resolve(interaction, alert_id, mod, data)

        if action == "proof":
            modal = discord.ui.Modal(title="Add Proof", custom_id=_cid("proofsubmit", member_id, channel_id, alert_id))
            modal.add_item(discord.ui.TextInput(label="Message link (optional)", custom_id="link", required=False,
                                                max_length=200, placeholder="https://discord.com/channels/…"))
            modal.add_item(discord.ui.TextInput(label="Note", custom_id="note", style=discord.TextStyle.paragraph,
                                                required=False, max_length=300,
                                                placeholder="Like: Said the same thing in #general 10 minutes later"))
            return await interaction.response.send_modal(modal)
        if action == "proofsubmit":
            if db.is_resolved(alert_id):
                return await self._reply(interaction, "This case is already resolved.")
            link = (_find_value(data.get("components"), "link") or "").strip()
            note = (_find_value(data.get("components"), "note") or "").strip()
            m = _LINK.fullmatch(link) if link else None
            if link and not m:
                return await self._reply(interaction, "That isn't a message link. Right-click the message > **Copy Message Link**.")
            if m and int(m.group(1)) != guild.id:
                return await self._reply(interaction, "That message is from another server.")
            if not link and not note:
                return await self._reply(interaction, "Add a message link, a note, or both.")
            parts = ([f"[Jump to message](<{link}>)"] if link else []) + \
                ([f"\"{discord.utils.escape_mentions(discord.utils.escape_markdown(note))}\""] if note else [])
            result = "added proof: " + " · ".join(parts)
            return await self._record(interaction, guild, mod, alert_id, claim, member_id, channel_id, action, result, now)

        if action == "delmsg":  # Delete Message button
            channel = guild.get_channel(channel_id)
            if channel is None or not channel.permissions_for(mod).manage_messages:
                return await self._reply(interaction, "You need the **Manage Messages** permission there for this.")
            try:
                await channel.get_partial_message(int(extra)).delete()
                result = "deleted the reported message"
            except discord.NotFound:
                result = "found the reported message was already deleted"
            except (discord.Forbidden, ValueError):
                return await self._reply(interaction, "Lookout can't delete messages there. Give it **Manage Messages**.")
            return await self._record(interaction, guild, mod, alert_id, claim, member_id, channel_id, action, result, now)

        target = await self._member(guild, member_id) if member_id else None
        if member_id and target is None:
            return await self._reply(interaction, "That member isn't in the server anymore.")

        if action == "timeout" and not extra:
            buttons = [discord.ui.Button(label=label, custom_id=_cid("timeout", member_id, channel_id, alert_id, str(m)))
                       for m, label in TIMEOUTS]
            custom = discord.ui.Button(label="Custom…", custom_id=_cid("timeout", member_id, channel_id, alert_id, "custom"))
            view = ui_kit.card(f"Timeout {target.display_name}", "How long should the timeout last?",
                               buttons=buttons[:3] + [None] + buttons[3:] + [None, custom])
            return await interaction.response.send_message(view=view, ephemeral=True)
        if action == "timeout" and extra == "custom":
            modal = discord.ui.Modal(title=f"Timeout {target.display_name}"[:45],
                                     custom_id=_cid("timeoutsubmit", member_id, channel_id, alert_id))
            modal.add_item(discord.ui.TextInput(label="How long?", custom_id="length", max_length=20,
                                                placeholder="Like 3h, 2d or 45m. Up to 28 days."))
            return await interaction.response.send_modal(modal)
        if action == "timeoutsubmit":
            minutes = parse_length(_find_value(data.get("components"), "length") or "")
            if not minutes:
                return await self._reply(interaction, "Lookout didn't get that. Try something like **3h**, **2d** or **45m**.")
            if minutes > MAX_TIMEOUT:
                return await self._reply(interaction, "Discord's limit is **28 days**. Try a shorter one.")
            action, extra = "timeout", str(minutes)

        if action == "warn":
            modal = discord.ui.Modal(title=f"Warn {target.display_name}"[:45],
                                     custom_id=_cid("warnsubmit", member_id, channel_id, alert_id))
            modal.add_item(discord.ui.TextInput(label="Reason (they'll see this)", custom_id="reason",
                                                style=discord.TextStyle.paragraph, max_length=300,
                                                placeholder="Like: Being rude to people in voice"))
            return await interaction.response.send_modal(modal)

        reason = f"Lookout: action by {mod} ({mod.id})"
        try:
            result = await self._do(interaction, action, mod, guild, target, channel_id, extra, reason, data)
        except discord.Forbidden:
            return await self._reply(interaction, "Lookout can't do that. Its role needs to be above theirs, with "
                                                 "**Timeout Members** and **Move Members**. Check it in /setup.")
        if result is None:
            return
        await self._record(interaction, guild, mod, alert_id, claim, member_id, channel_id, action, result, now)

    async def _record(self, interaction, guild, mod, alert_id, claim, member_id, channel_id, action, result, now):
        if action != "warnsubmit":
            db.log(guild.id, member_id or None, f"staff_{action}", result, channel_id or None, mod.id)
        new_claim = None
        if alert_id:
            db.add_alert_action(alert_id, result)
            if claim is None and action != "proofsubmit":  # doing something claims it, adding proof doesn't
                db.set_claim(guild.id, alert_id, mod.id, mod.display_name)
                new_claim = (mod.id, mod.display_name)
        await self._stamp(interaction, alert_id, f"-# {mod.mention} {result} · <t:{now}:R>", claim=new_claim)

    async def _do(self, interaction, action, mod, guild, target, channel_id, extra, reason, data):
        if not outranks(mod, target):
            await self._reply(interaction, "You can't do that to someone with the same or a higher role.")
            return None
        if action == "warnsubmit":
            text = (_find_value(data.get("components"), "reason") or "No reason given").strip()
            return await send_warning(guild, mod, target, text, channel_id)
        if action == "timeout":
            if not mod.guild_permissions.moderate_members:
                await self._reply(interaction, "You need the **Timeout Members** permission for this.")
                return None
            minutes = int(extra) if extra.isdigit() else 10
            minutes = max(1, min(minutes, MAX_TIMEOUT))
            await target.timeout(dt.timedelta(minutes=minutes), reason=reason)
            label = next((l for m, l in TIMEOUTS if m == minutes), length_label(minutes))
            return f"timed out {target.mention} for {label}"
        if action == "untimeout":
            if not mod.guild_permissions.moderate_members:
                await self._reply(interaction, "You need the **Timeout Members** permission for this.")
                return None
            await target.timeout(None, reason=reason)
            return f"removed {target.mention}'s timeout"
        if action == "disconnect":
            if not mod.guild_permissions.move_members:
                await self._reply(interaction, "You need the **Move Members** permission for this.")
                return None
            if not target.voice:
                await self._reply(interaction, f"{target.mention} isn't in a voice channel right now.")
                return None
            await target.move_to(None, reason=reason)
            return f"disconnected {target.mention}"
        return None

    async def _resolve(self, interaction, alert_id, mod, data):
        note = (_find_value(data.get("components"), "note") or "").strip() or "No details given."
        told = (_find_value(data.get("components"), "reporter") or "").strip()[:300]
        actions = db.alert_actions(alert_id)
        guild = interaction.guild
        db.log(guild.id, None, "staff_resolve", note, None, mod.id)
        db.resolve_case(alert_id, mod.id, note)
        reports = self.bot.get_cog("Reports")
        if reports:
            reports.close_case(alert_id)
        reporters = db.take_reporters(alert_id)
        if reporters and db.get_settings(guild.id).report_updates:
            self.bot.loop.create_task(self._tell_reporters(guild, reporters, skip=mod.id, message=told))
        else:
            told = ""

        try:
            msg = await interaction.channel.fetch_message(alert_id)
        except discord.HTTPException:
            return await self._reply(interaction, "Lookout couldn't find that alert anymore.")
        took = discord.utils.utcnow() - msg.created_at
        mins = int(took.total_seconds() // 60)
        took_txt = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{max(mins, 1)}m"
        done = "\n".join(f"- {a[0].upper()}{a[1:]}" for a in actions) or "No actions taken."
        summary = ui_kit.kv([
            ("Handled By", f"{mod.mention}, in {took_txt}"),
            ("Actions Taken", done),
            ("What They Did", discord.utils.escape_markdown(note)),
            ("Told the Reporter", discord.utils.escape_markdown(told) if told else None),
        ])

        view = discord.ui.LayoutView.from_message(msg, timeout=None)
        box = ui_kit.action_box(view)
        ui_kit.remove_actions(box)
        ui_kit.set_status(view, "Resolved", f"by {mod.mention} in {took_txt}")
        used = [i.id or 0 for i in view.walk_children()]
        nid = max([ui_kit.STAFF_NOTE_ID - 1, *used]) + 1
        box.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        box.add_item(discord.ui.TextDisplay(ui_kit.tidy("## Resolved\n" + summary)[:4000], id=nid))
        case = db.case_by_message(alert_id)
        row = []
        if case and case["kind"] in ("teen", "risk"):  # keep it, they might still report it to discord
            row.append(discord.ui.Button(label="Report to Discord", custom_id=f"lkteen:tns:{case['target_id'] or 0}"))
        if case and case["case_no"]:
            row.append(discord.ui.Button(label="Export Case", custom_id=f"lkcases:export:{case['case_no']}"))
        if row:
            box.add_item(discord.ui.ActionRow(*row))
        if interaction.message and interaction.message.id == alert_id:
            await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())
        else:
            await msg.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
            await self._reply(interaction, "Alert resolved.")

    # message: what the mod wrote for the reporter, if anything. which mod handled it is never shown
    async def _tell_reporters(self, guild: discord.Guild, reporters: list[int], skip: int, message: str = ""):
        view = ui_kit.card("Your Report Was Reviewed",
                           "The mods looked at your report and closed the case. Thanks for reporting it.",
                           ("From the mods", discord.utils.escape_mentions(message)) if message else None,
                           subtitle=f"From the mods of {discord.utils.escape_markdown(guild.name)}")
        for user_id in reporters:
            if user_id == skip:
                continue
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                await user.send(view=view)
            except discord.HTTPException:
                pass    # DMs closed
            await asyncio.sleep(1)

    async def _open_panel(self, interaction, member_id, channel_id):
        guild = interaction.guild
        target = await self._member(guild, member_id)
        if target is None:
            return await self._reply(interaction, "That member isn't in the server anymore.")
        if target.bot:
            return await self._reply(interaction, "That's a bot.")
        alert_id = interaction.message.id if interaction.message else 0
        info = ui_kit.kv([
            ("Voice", target.voice.channel.mention if target.voice and target.voice.channel else "Not in voice"),
            ("Flags", str(db.count_flags(guild.id, target.id))),
            ("Warnings", str(db.count_kind(guild.id, target.id, "staff_warn"))),
        ])
        buttons = [discord.ui.Button(label="Warn", custom_id=_cid("warn", target.id, channel_id, alert_id)),
                   discord.ui.Button(label="Disconnect", custom_id=_cid("disconnect", target.id, channel_id, alert_id))]
        if target.is_timed_out():
            buttons.append(discord.ui.Button(label="Remove Timeout",
                                             custom_id=_cid("untimeout", target.id, channel_id, alert_id)))
        buttons.insert(1, discord.ui.Button(label="Timeout", custom_id=_cid("timeout", target.id, channel_id, alert_id)))
        await interaction.response.defer(ephemeral=True, thinking=True)
        file = await profile_image(target)
        if file:
            view = ui_kit.card(None, info, buttons=buttons, image=f"attachment://{file.filename}")
        else:
            view = ui_kit.card(target.display_name, info, buttons=buttons,
                               subtitle=ui_kit.member_line(target), thumbnail=target.display_avatar.url,
                               footer="Only you can see this. Actions are logged on the alert.")
        await interaction.followup.send(view=view, files=[file] if file else [], ephemeral=True)

    @staticmethod
    async def _member(guild, member_id):
        m = guild.get_member(member_id)
        if m is None:
            try:
                m = await guild.fetch_member(member_id)
            except discord.HTTPException:
                return None
        return m

    @staticmethod
    async def _reply(interaction, text):
        if interaction.response.is_done():
            await interaction.followup.send(view=ui_kit.note(text), ephemeral=True)
        else:
            await interaction.response.send_message(view=ui_kit.note(text), ephemeral=True)

    # claim: (id, name) to claim, False to unclaim, None to leave it
    async def _stamp(self, interaction, alert_id, stamp, claim=None):
        on_alert = interaction.message is not None and interaction.message.id == alert_id \
            and not interaction.message.flags.ephemeral
        try:
            msg = interaction.message if on_alert else await interaction.channel.fetch_message(alert_id)
            view = discord.ui.LayoutView.from_message(msg, timeout=None)
            box = ui_kit.action_box(view)
            if claim is not None:
                ui_kit.set_status(view, who=f"Claimed by <@{claim[0]}>" if claim else "Unclaimed")
                for row in [i for i in box.children if isinstance(i, discord.ui.ActionRow)]:
                    for btn in row.children:
                        if isinstance(btn, discord.ui.Button) and (btn.custom_id or "").startswith(f"{PREFIX}:claim:"):
                            _, _, m, c, *_ = btn.custom_id.split(":") + ["0"]
                            new = claim_button(int(m), int(c), claim or None)
                            btn.label = ui_kit.tidy(new.label)
            used = [i.id or 0 for i in view.walk_children()]
            ui_kit.insert_above_actions(box, [discord.ui.TextDisplay(ui_kit.tidy(stamp),
                                                                     id=max([ui_kit.STAFF_NOTE_ID - 1, *used]) + 1)])
            if on_alert:
                return await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())
            await msg.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            log.warning("Could not stamp alert %s", alert_id, exc_info=True)
        await self._reply(interaction, stamp.removeprefix("-# "))

    @app_commands.command(name="warn", description="Warn a member. They get a DM, and it's saved in their history.")
    @app_commands.describe(member="Who to warn", reason="Why (they'll see this)")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 1, 300]):
        mod = interaction.user
        if not self.bot.is_staff(mod):
            return await self._reply(interaction, "Only mods can warn members.")
        if member.bot or not outranks(mod, member):
            return await self._reply(interaction, "You can't warn that member.")
        await interaction.response.defer(ephemeral=True)  # DMing can be slow
        result = await send_warning(interaction.guild, mod, member, reason, None)
        await interaction.followup.send(view=ui_kit.note(f"You {result}"), ephemeral=True)
        await self.bot.alert_staff(interaction.guild, ui_kit.note(f"-# {mod.mention} {result}"))

    @app_commands.command(name="history", description="See a member's safety history (mods only)")
    @app_commands.describe(member="Whose history to show")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def history(self, interaction: discord.Interaction, member: discord.Member):
        if not self.bot.is_staff(interaction.user):
            return await self._reply(interaction, "Only mods can see a member's history.")
        g = interaction.guild.id
        rows = db.history(g, member.id)
        flags = db.count_flags(g, member.id)
        warns = db.count_kind(g, member.id, "staff_warn")
        timeouts = db.count_kind(g, member.id, "staff_timeout") + db.count_kind(g, member.id, "auto_action")

        overview = f"**{flags}** flags · **{warns}** warnings · **{timeouts}** timeouts"
        entries = []
        for kind, detail, actor_id, channel_id, created_at, _ in rows:
            meta = [f"<t:{int(created_at)}:R>"]
            if channel_id:
                meta.append(f"in <#{channel_id}>")
            if actor_id and kind.startswith("staff_"):
                meta.append(f"by <@{actor_id}>")
            text = f"**{LABELS.get(kind, kind)}** · " + " · ".join(meta)
            if detail and kind in ("teen_flirty", "teen_contact"):  # copy of the removed message, keep it hidden
                text += f"\n-# {ui_kit.hidden(detail, 140)}"
            elif detail:
                text += f"\n-# {discord.utils.escape_markdown(detail[:140])}"
            entries.append(text)
        activity = "\n".join(entries) if entries else "Nothing on record."

        await interaction.response.defer(ephemeral=True, thinking=True)
        file = await profile_image(member)
        sections = [("Overview", overview), (f"Recent Activity · {len(rows)}", activity)]
        controls = history_controls(member.id, rows) if rows else None
        if file:
            view = ui_kit.card(None, *sections, image=f"attachment://{file.filename}",
                               buttons=controls, brand=True)
        else:
            view = ui_kit.card(member.display_name, *sections, buttons=controls, brand=True,
                               subtitle=ui_kit.member_line(member), thumbnail=member.display_avatar.url)
        await interaction.followup.send(view=view, files=[file] if file else [], ephemeral=True)


async def setup(bot):
    await bot.add_cog(Staff(bot))
