import asyncio
import io
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import chart
import config
import db
import ui_kit
from cogs.staff import _cid, action_buttons

# what members pick from. mods get the labels below
CATEGORIES = [
    app_commands.Choice(name="Inappropriate content", value="inappropriate"),
    app_commands.Choice(name="Violent content", value="violence"),
    app_commands.Choice(name="Harassment or bullying", value="harassment"),
    app_commands.Choice(name="Threats or danger", value="threats"),
    app_commands.Choice(name="A teen might be in danger", value="young"),
    app_commands.Choice(name="Scam or someone's private info", value="scam"),
    app_commands.Choice(name="Something else", value="other"),
]
STAFF_LABELS = {
    "inappropriate": "Inappropriate content", "violence": "Violence", "harassment": "Harassment",
    "threats": "Threats or self-harm", "young": "Minor safety", "scam": "Scam or doxxing", "other": "Other",
    "mod": "Opened by a mod",
}
URGENT = {"Minor safety", "Threats or self-harm"}
PANEL = "lkreport"
log = logging.getLogger("lookout.reports")

MAX_SCREENSHOT_MB = 8
MAX_SCREENSHOTS = 3  # per alert
NO_SCREENSHOT = {"young"}  # never post screenshots for these
SCREENSHOT_TYPES = ("png", "jpg", "jpeg", "gif", "webp")


def _screenshot_problem(shot: discord.Attachment) -> str | None:
    if not (shot.content_type or "").startswith("image/"):
        return "Only pictures can be sent as screenshots, so your file wasn't sent."
    if shot.size > MAX_SCREENSHOT_MB * 1024 * 1024:
        return f"Your screenshot is over {MAX_SCREENSHOT_MB} MB, so it wasn't sent."
    return None


# modal fields can be nested in labels/rows
def _walk(components):
    for c in components or []:
        yield c
        yield from _walk(c.get("components"))
        if isinstance(c.get("component"), dict):
            yield from _walk([c["component"]])


def _message_box(reported: dict) -> str:
    where = "in your DMs" if reported.get("dm") else f"in <#{reported['channel_id']}>"
    quote = reported["content"].replace("```", "'''")
    return f"<@{reported['author_id']}> {where}\n```\n{quote}\n```"


def _report_modal(category: str, upload: bool, member_id: int = 0) -> discord.ui.Modal:
    modal = discord.ui.Modal(title="Report a Problem", custom_id=f"{PANEL}:send:{category}:{member_id}")
    question = "What's going on? (optional)"
    details = discord.ui.TextInput(label=None if upload else question, custom_id="details",
                                   style=discord.TextStyle.paragraph, required=False, max_length=300)
    if not upload:
        return modal.add_item(details)
    modal.add_item(discord.ui.Label(text=question, component=details))
    modal.add_item(discord.ui.Label(
        text="Screenshot (optional)", description="A picture of what happened. Only the mods will see it.",
        component=discord.ui.FileUpload(custom_id="shot", required=False, min_values=0, max_values=1)))
    return modal


def panel_view(banner: bool = True) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    button = discord.ui.Button(label="Report a Problem", custom_id=f"{PANEL}:open")
    if banner and chart.banner("report", "Report a Problem"):
        ui_kit.head(view, "Report a Problem", None, "report")
        text = "**Something wrong?**\nTell the mods privately. Only they see your report, and nobody else knows you sent it."
    else:
        text = (f"## {ui_kit.titled('report', 'Report a Problem')}\nSomething wrong? Tell the mods privately. Only they "
                "see your report.")
    view.add_item(discord.ui.Container(discord.ui.Section(text, accessory=button)))
    lines = ["-# You can also type /report, or right-click someone or a message, then **Apps**."]
    if url := ui_kit.add_app_url():
        lines.append(f"-# Got a DM that's not okay? [Add Lookout to your apps](<{url}>), then right-click it > "
                     "**Apps** > **Report Message to Mods**.")
    view.add_item(discord.ui.TextDisplay("\n".join(lines)))
    return view


def _has_report_button(message: discord.Message) -> bool:
    try:
        view = discord.ui.LayoutView.from_message(message, timeout=None)
    except Exception:
        return False
    return any(getattr(item, "custom_id", None) == f"{PANEL}:open" for item in view.walk_children())


# one alert per voice channel or member, reports in the window update it
class Case:

    def __init__(self, message, target_id):
        self.message: discord.Message = message
        self.target_id: int | None = target_id
        self.started = time.time()
        self.reasons: list[str] = []
        self.reporters: set[int] = set()
        self.case_no = 0
        self.shots = 0  # screenshots posted so far
        self.shot_held = False  # got one but didn't post it
        self.messages: list[dict] = []  # copies of right-click reported messages

    def expired(self):
        return time.time() - self.started > config.REPORT_WINDOW_MINUTES * 60


class Reports(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cases: dict[str, Case] = {}  # "voice:<channel>" or "member:<guild>:<member>"
        self.cooldowns: dict[tuple[int, str], float] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.modal_upload = True  # off if discord rejects the file upload field
        self.pending: dict[tuple[int, int], tuple[dict, float]] = {}  # (reporter, message id): (copy, time)
        # right-click menus. the message one also works in DMs if the user added the app
        self.menu = app_commands.ContextMenu(name="Report to Mods", callback=self.report_member)
        self.menu.guild_only = True
        self.message_menu = app_commands.ContextMenu(
            name="Report Message to Mods", callback=self.report_message,
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
            allowed_contexts=app_commands.AppCommandContext(guild=True, private_channel=True))
        for menu in (self.menu, self.message_menu):
            self.bot.tree.add_command(menu)

    # DM reports need User Install turned on in the dev portal
    async def cog_load(self):
        try:
            info = await self.bot.application_info()
            ui_kit.USER_INSTALL = info.user_integration_config is not None
        except Exception:
            ui_kit.USER_INSTALL = False
        if not ui_kit.USER_INSTALL:
            self.message_menu.allowed_installs = app_commands.AppInstallationType(guild=True)
            self.message_menu.allowed_contexts = app_commands.AppCommandContext(guild=True)
            log.info("User Install is off in the Developer Portal, so members can't report DMs")

    def cog_unload(self):
        for menu in (self.menu, self.message_menu):
            self.bot.tree.remove_command(menu.name, type=menu.type)

    # swap old report panels for the current one, once per start
    @commands.Cog.listener()
    async def on_ready(self):
        if getattr(self, "_panels_refreshed", False):
            return
        self._panels_refreshed = True
        for guild in list(self.bot.guilds):
            s = db.get_settings(guild.id)
            channel = guild.get_channel(s.panel_channel_id) if s.panel_channel_id else None
            if channel is None:
                continue
            try:
                await self.refresh_panel(channel)
            except Exception:
                log.info("Couldn't update the Report card in server %s", guild.id)
            await asyncio.sleep(1)

    async def refresh_panel(self, channel) -> bool:
        perms = channel.permissions_for(channel.guild.me)
        if not (perms.view_channel and perms.read_message_history):
            return False
        async for message in channel.history(limit=50):
            if message.author.id == self.bot.user.id and _has_report_button(message):
                view = panel_view(banner=perms.attach_files)
                await message.edit(view=view, attachments=ui_kit.files(view))
                return True
        return False

    async def report_member(self, interaction: discord.Interaction, member: discord.Member):
        problem = self._cant_report(interaction.guild)
        if member.bot:
            problem = "That's a bot. Pick the member who's causing the problem."
        elif member.id == interaction.user.id:
            problem = "You can't report yourself."
        if problem:
            return await interaction.response.send_message(view=ui_kit.note(problem), ephemeral=True)
        view = self._category_picker(member.id)
        await interaction.response.send_message(view=view, files=ui_kit.files(view), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @staticmethod
    def _copy(message: discord.Message) -> dict:
        files = len(message.attachments)
        text = message.content or ""
        if files:
            text += ("\n" if text else "") + f"({files} picture{'' if files == 1 else 's'} or file{'' if files == 1 else 's'})"
        return {"content": text[:1000] or "(no text)", "author_id": message.author.id,
                "channel_id": message.channel.id, "message_id": message.id, "jump": message.jump_url}

    # copy it right away in case it gets deleted. mods skip the form
    async def report_message(self, interaction: discord.Interaction, message: discord.Message):
        if message.author.bot:
            problem = "That's a bot's message. Lookout only takes reports about members."
        elif message.author.id == interaction.user.id:
            problem = "You can't report your own message."
        elif interaction.guild_id is None:
            return await self._report_dm(interaction, message)
        elif self.bot.get_guild(interaction.guild_id) is None:  # user install in a server we're not in
            problem = ("Lookout isn't in this server, so there's no one to send it to. To report it to Discord, "
                       "right-click the message and pick **Report Message**.")
        else:
            problem = self._cant_report(interaction.guild)
        if problem:
            return await interaction.response.send_message(view=ui_kit.note(problem), ephemeral=True)
        reported = self._copy(message)
        if isinstance(interaction.user, discord.Member) and self.bot.is_staff(interaction.user):
            await interaction.response.defer(ephemeral=True, thinking=True)
            author = message.author if isinstance(message.author, discord.Member) else None
            return await self.submit(interaction, "mod", None, author, reported=reported, by_mod=True)
        self._hold(interaction.user.id, reported)
        await interaction.response.send_message(view=self._message_picker(reported), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    def _hold(self, user_id: int, reported: dict) -> None:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v[1] < 900}
        self.pending[(user_id, reported["message_id"])] = (reported, now)

    async def _report_dm(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guilds = await self._shared_servers(interaction.user.id, message.author.id)
        if not guilds:
            name = discord.utils.escape_markdown(message.author.name)
            return await interaction.followup.send(view=ui_kit.note(
                f"You and **@{name}** don't share a server that uses Lookout, so there's no one to send it to.\n"
                "-# To report it to Discord, right-click the message and pick **Report Message**."),
                ephemeral=True)
        reported = self._copy(message) | {"dm": True, "to_id": interaction.user.id}
        self._hold(interaction.user.id, reported)
        view = self._message_picker(reported, guilds[0]) if len(guilds) == 1 else self._server_picker(reported, guilds)
        await interaction.followup.send(view=view, files=ui_kit.files(view), ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    # no member cache, so this fetches each member (a few at a time)
    async def _shared_servers(self, *user_ids: int) -> list[discord.Guild]:
        gate = asyncio.Semaphore(5)

        async def has_all(guild):
            async with gate:
                for user_id in user_ids:
                    if await self._member(guild, user_id) is None:
                        return None
                return guild

        ready = [g for g in self.bot.guilds if (s := db.get_settings(g.id)).configured and s.staff_channel_id]
        found = [g for g in await asyncio.gather(*(has_all(g) for g in ready)) if g]
        return sorted(found, key=lambda g: g.name.lower())[:25]

    @staticmethod
    async def _member(guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        return member

    async def _send_dm_report(self, interaction: discord.Interaction, category: str, details: str | None,
                              reported: dict, guild_id: int):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = self.bot.get_guild(guild_id)
        reporter = author = None
        if guild and db.get_settings(guild.id).configured:
            reporter = await self._member(guild, interaction.user.id)
            author = await self._member(guild, reported["author_id"])
        if reporter is None or author is None:
            return await interaction.followup.send(view=ui_kit.note(
                "Lookout couldn't send it to that server. One of you might have left it."), ephemeral=True)
        await self.submit(interaction, category, details, author, reported=reported, guild=guild, reporter=reporter)

    @staticmethod
    def _server_picker(reported: dict, guilds: list[discord.Guild]) -> discord.ui.LayoutView:
        menu = discord.ui.Select(custom_id=f"{PANEL}:dmserver:{reported['message_id']}",
                                 placeholder="Which server should know?",
                                 options=[discord.SelectOption(label=g.name[:100], value=str(g.id)) for g in guilds])
        return ui_kit.card("Report a Problem", ("The Message", _message_box(reported)),
                           "Pick which server's mods should see it. Only servers you're both in that use Lookout show "
                           "up here.", subtitle="Reporting a DM", buttons=[menu], icon="report")

    @staticmethod
    def _message_picker(reported: dict, guild: discord.Guild | None = None) -> discord.ui.LayoutView:
        to = f":{guild.id}" if guild else ""
        what = discord.ui.Select(custom_id=f"{PANEL}:mpick:{reported['message_id']}{to}", placeholder="What's going on?",
                                 options=[discord.SelectOption(label=c.name, value=c.value) for c in CATEGORIES])
        text = (f"Pick what's going on. Only the mods of **{discord.utils.escape_markdown(guild.name)}** will see it."
                if guild else "Pick what's going on. Only the mods can see your report.")
        return ui_kit.card("Report a Problem", ("The Message", _message_box(reported)), text,
                           subtitle="Reporting a DM" if reported.get("dm") else "Reporting a message",
                           buttons=[what], icon="report")

    @staticmethod
    def _category_picker(member_id: int = 0, note: str | None = None) -> discord.ui.LayoutView:
        who = discord.ui.UserSelect(custom_id=f"{PANEL}:who", placeholder="Who's it about? (optional)",
                                    min_values=0, max_values=1,
                                    default_values=[discord.Object(member_id)] if member_id else [])
        what = discord.ui.Select(custom_id=f"{PANEL}:pick:{member_id}", placeholder="What's going on?",
                                 options=[discord.SelectOption(label=c.name, value=c.value) for c in CATEGORIES])
        text = "Pick who it's about, if you know, and what's going on. Only the mods can see your report."
        if note:
            text += f"\n\n**{note}**"
        return ui_kit.card("Report a Problem", text, subtitle=f"About <@{member_id}>" if member_id else None,
                           footer="In a voice channel? The mods will see which one.",
                           buttons=[who, what], icon="report")

    def close_case(self, message_id: int) -> None:
        for key, case in list(self.cases.items()):
            if case.message and case.message.id == message_id:
                del self.cases[key]

    @app_commands.command(name="report", description="Report something or someone unsafe. Only the mods see it.")
    @app_commands.guild_only()
    async def report(self, interaction: discord.Interaction):
        problem = self._cant_report(interaction.guild)
        if problem:
            return await interaction.response.send_message(view=ui_kit.note(problem), ephemeral=True)
        view = self._category_picker()
        await interaction.response.send_message(view=view, files=ui_kit.files(view), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @staticmethod
    def _cant_report(guild: discord.Guild) -> str | None:
        if not db.get_settings(guild.id).configured:
            return "Lookout isn't set up here yet. Ask an admin to run /setup."
        return None

    # used by /report, the panel and both context menus
    # guild/reporter are passed in for DM reports
    async def submit(self, interaction: discord.Interaction, category: str, details: str | None,
                     user: discord.Member | None, screenshot: discord.Attachment | None = None,
                     reported: dict | None = None, by_mod: bool = False, guild: discord.Guild | None = None,
                     reporter: discord.Member | None = None):
        guild, reporter = guild or interaction.guild, reporter or interaction.user
        s = db.get_settings(guild.id)
        dm = bool(reported and reported.get("dm"))

        async def reply(text, buttons=None):
            if interaction.response.is_done():
                await interaction.followup.send(view=ui_kit.note(text, buttons), ephemeral=True)
            else:
                await interaction.response.send_message(view=ui_kit.note(text, buttons), ephemeral=True)

        problem = self._cant_report(guild)
        if problem:
            return await reply(problem)
        if user and (user.bot or user.id == reporter.id):
            user = None
        voice = None if reported else (reporter.voice.channel if reporter.voice else None)
        channel = None if dm else ((guild.get_channel(reported["channel_id"]) if reported else None)
                                   or voice or interaction.channel)
        if voice:
            key = f"voice:{voice.id}"
        elif user:
            key = f"member:{guild.id}:{user.id}"  # guild id in there so two servers never share an alert
        else:
            key = None  # gets its own alert

        now = time.time()
        # clean up so these don't grow forever
        self.cases = {k: c for k, c in self.cases.items() if not c.expired()}
        self.cooldowns = {k: t for k, t in self.cooldowns.items() if now - t < config.REPORT_COOLDOWN_SECONDS}
        wait = config.REPORT_COOLDOWN_SECONDS - (now - self.cooldowns.get((reporter.id, key or ""), 0))
        if wait > 0 and not by_mod:
            later = f"{int(wait)} seconds" if wait < 60 else f"{round(wait / 60)} minute{'' if wait < 90 else 's'}"
            return await reply(f"You already reported this. You can send another report in {later}.")
        self.cooldowns[(reporter.id, key or "")] = now

        shot_sent, shot_note, held = "", "", False
        if screenshot:
            problem = _screenshot_problem(screenshot)
            if category in NO_SCREENSHOT:
                screenshot, held = None, True
                shot_note = ("\n-# Your screenshot wasn't passed on. For this kind of problem, please report it "
                             "to Discord too: https://dis.gd/request")
            elif problem:
                screenshot, shot_note = None, f"\n-# {problem}"
            else:
                shot_sent = " Your screenshot went with it."
        if not by_mod:
            thanks = (f"**Sent to the mods of {discord.utils.escape_markdown(guild.name)}.** Only they can see your "
                      "report." if dm else "**Thanks for letting us know.** Only the mods can see your report.")
            await reply(thanks + shot_sent + ("\n-# If you feel unsafe in voice, you can leave anytime." if voice else "")
                        + shot_note, buttons=[ui_kit.block_button()])

        streamers = [m for m in voice.members if m.voice and m.voice.self_stream] if voice else []
        cameras = [m for m in voice.members if m.voice and m.voice.self_video and m not in streamers] if voice else []
        target = user or (streamers[0] if len(streamers) == 1 else None)

        label = STAFF_LABELS.get(category, category)
        reason = label + (f" · {discord.utils.escape_markdown(details)}" if details else "")
        if by_mod:
            reason = f"Opened by {reporter.mention} from a message"
        # still "vc_report" outside voice so older summaries keep working
        db.log(guild.id, target.id if target else None, "vc_report", f"{label}: {details or ''}",
               channel.id if channel else None, reporter.id)
        teen_safety = self.bot.get_cog("TeenSafety")
        if teen_safety and target:
            await teen_safety.reported(guild, reporter, target)

        # one at a time per server, otherwise two reports at once open two alerts
        async with self.locks.setdefault(guild.id, asyncio.Lock()):
            case = self.cases.get(key) if key else None
            fresh = None
            if case:
                try:
                    fresh = await case.message.channel.fetch_message(case.message.id)
                except discord.HTTPException:
                    pass  # deleted, or we can't see it anymore
                if fresh and db.is_resolved(fresh.id):
                    fresh = None
            if fresh is None:  # new alert
                case = Case(None, target.id if target else None)
                case.case_no = db.next_case_no(guild.id)
            elif target and case.target_id is None:
                case.target_id = target.id
            case.reasons.append(reason)
            case.reporters.add(reporter.id)
            if reported and reported["message_id"] not in {m["message_id"] for m in case.messages}:
                case.messages = (case.messages + [reported])[-3:]
            case.shot_held = case.shot_held or held
            if screenshot and case.shots >= MAX_SCREENSHOTS:
                screenshot = None
            if screenshot:
                case.shots += 1

            kind = "voice" if voice else "report"
            view = self._card(guild, s, channel, voice, case, streamers, cameras)
            if fresh is None:
                case.message = await self.bot.alert_staff(guild, view)
                if case.message is None:
                    if by_mod:
                        await reply("Lookout couldn't post in the mod channel. Check its permissions in /setup.")
                    return
                if key:
                    self.cases[key] = case
                db.create_case(guild.id, case.message.id, case.message.channel.id, case.case_no, kind,
                               channel.id if channel else None, case.target_id, "; ".join(case.reasons),
                               " ".join(m["jump"] for m in case.messages) or None)
            else:
                ui_kit.insert_above_actions(ui_kit.action_box(view), ui_kit.staff_notes(fresh))
                try:
                    case.message = await fresh.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    log.warning("Couldn't update the alert for case %s", case.case_no, exc_info=True)
                db.update_case(case.message.id, target_id=case.target_id, summary="; ".join(case.reasons),
                               **({"evidence": " ".join(m["jump"] for m in case.messages)} if case.messages else {}))
            db.add_reporter(case.message.id, reporter.id)
        if by_mod and case.message:
            await reply(f"**Case #{case.case_no} opened** in <#{s.staff_channel_id}> with a copy of the message.")

        if screenshot:
            await self._post_screenshot(case, screenshot)

    async def _post_screenshot(self, case: Case, shot: discord.Attachment):
        try:
            ext = shot.filename.rsplit(".", 1)[-1].lower() if "." in shot.filename else "png"
            file = discord.File(io.BytesIO(await shot.read()), spoiler=True,
                                filename=f"screenshot-{case.case_no}-{case.shots}.{ext if ext in SCREENSHOT_TYPES else 'png'}")
            view = ui_kit.card(
                ui_kit.titled("image", f"Screenshot · #{case.case_no}"),
                subtitle="Sent by a reporter. Click the picture to show it.",
                image=f"attachment://{file.filename}", spoiler=True,
                footer="If it could be illegal, report it to Discord at https://dis.gd/request and delete this "
                       "message.")
            posted = await case.message.reply(view=view, files=[file], allowed_mentions=discord.AllowedMentions.none())
            if case.shots == 1:
                db.update_case(case.message.id, screenshot_url=posted.jump_url)
        except Exception:
            log.warning("Couldn't pass on the screenshot for case %s", case.case_no, exc_info=True)

    # report panel + interactions

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith(PANEL + ":"):
            return
        parts = cid.split(":")
        action = parts[1]
        if interaction.guild is None and action not in ("dmserver", "mpick", "msend"):  # DM report steps only
            return

        if action == "dmserver":  # DM report: server picked
            message_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            values = interaction.data.get("values") or []
            saved = self.pending.get((interaction.user.id, message_id))
            guild = self.bot.get_guild(int(values[0])) if values and values[0].isdigit() else None
            if saved is None or guild is None:
                return await interaction.response.edit_message(view=ui_kit.note(
                    "That report took too long. Right-click the message and try again."))
            view = self._message_picker(saved[0], guild)
            return await interaction.response.edit_message(view=view, attachments=ui_kit.files(view),
                                                           allowed_mentions=discord.AllowedMentions.none())

        if action == "open":  # panel button
            problem = self._cant_report(interaction.guild)
            if problem:
                return await interaction.response.send_message(view=ui_kit.note(problem), ephemeral=True)
            view = self._category_picker()
            return await interaction.response.send_message(view=view, files=ui_kit.files(view), ephemeral=True)

        if action == "who":  # member picked
            values = interaction.data.get("values") or []
            member_id = int(values[0]) if values else 0
            note = None
            if member_id == interaction.user.id:
                member_id, note = 0, "You can't report yourself."
            elif member_id and (m := interaction.guild.get_member(member_id)) and m.bot:
                member_id, note = 0, "That's a bot. Pick the member who's causing the problem."
            view = self._category_picker(member_id, note)
            return await interaction.response.edit_message(view=view, attachments=ui_kit.files(view),
                                                           allowed_mentions=discord.AllowedMentions.none())

        if action == "pick":  # category picked, open the form
            category = (interaction.data.get("values") or ["other"])[0]
            member_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            if self.modal_upload and category not in NO_SCREENSHOT:
                try:
                    return await interaction.response.send_modal(_report_modal(category, True, member_id))
                except discord.HTTPException as e:
                    self.modal_upload = False
                    log.warning("Discord refused the screenshot field, using the simple form: %s", e)
            return await interaction.response.send_modal(_report_modal(category, False, member_id))

        if action == "mpick":  # message report: category picked
            category = (interaction.data.get("values") or ["other"])[0]
            to = f":{parts[3]}" if len(parts) > 3 and parts[3].isdigit() else ""  # guild id for DM reports
            modal = discord.ui.Modal(title="Report a Problem", custom_id=f"{PANEL}:msend:{category}:{parts[2]}{to}")
            modal.add_item(discord.ui.TextInput(label="Anything the mods should know? (optional)",   # 45 max
                                                custom_id="details", style=discord.TextStyle.paragraph,
                                                required=False, max_length=300))
            return await interaction.response.send_modal(modal)

        if action == "msend":  # message report: form sent
            category = parts[2] if len(parts) > 2 else "other"
            message_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            saved = self.pending.pop((interaction.user.id, message_id), None)
            if saved is None:
                return await interaction.response.send_message(view=ui_kit.note(
                    "That report took too long. Right-click the message and try again."), ephemeral=True)
            reported = saved[0]
            details = next((f.get("value") for f in _walk(interaction.data.get("components"))
                            if f.get("custom_id") == "details"), None)
            details = (details or "").strip() or None
            if len(parts) > 4 and parts[4].isdigit():  # DM report
                return await self._send_dm_report(interaction, category, details, reported, int(parts[4]))
            if interaction.guild is None:
                return
            member = await self._member(interaction.guild, reported["author_id"])
            return await self.submit(interaction, category, details, member, reported=reported)

        if action == "send":  # report form sent
            category = parts[2] if len(parts) > 2 else "other"
            member_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            member = None
            if member_id:
                member = interaction.guild.get_member(member_id)
                if member is None:
                    try:
                        member = await interaction.guild.fetch_member(member_id)
                    except discord.HTTPException:
                        pass
            details, shot = None, None
            files = ((interaction.data.get("resolved") or {}).get("attachments") or {})
            for field in _walk(interaction.data.get("components")):
                if field.get("custom_id") == "details":
                    details = (field.get("value") or "").strip() or None
                elif field.get("custom_id") == "shot":
                    raw = next((files[str(i)] for i in field.get("values") or [] if str(i) in files), None)
                    if raw:
                        shot = discord.Attachment(data=raw, state=interaction._state)
            await self.submit(interaction, category, details, member, shot)

    def _card(self, guild, s, channel, voice, case, streamers, cameras):
        target = guild.get_member(case.target_id) if case.target_id else None
        urgent = any(r.startswith(u) for r in case.reasons for u in URGENT)
        minor = any(r.startswith("Minor safety") for r in case.reasons)

        who = []
        if case.target_id:
            prior = max(db.count_flags(guild.id, case.target_id) - 1, 0)
            who.append(f"<@{case.target_id}> · {prior} earlier flag{'s' if prior != 1 else ''}")
        if streamers:
            who.append("Streaming: " + ", ".join(m.mention for m in streamers))
        if cameras:
            who.append("Camera on: " + ", ".join(m.mention for m in cameras))
        what = "\n".join(case.reasons[-8:])
        if case.shots:
            what += "\n-# Screenshot: see the reply under this alert"
        elif case.shot_held:
            what += "\n-# A reporter sent a screenshot, but it isn't shown for this kind of report"
        reported = None
        if case.messages:
            blocks = []
            for m in case.messages:
                if m.get("dm"):
                    blocks.append(f"<@{m['author_id']}> to <@{m['to_id']}> in a DM\n{ui_kit.hidden(m['content'])}\n"
                                  "-# Reported by the person who got it. It came straight from Discord, so it can't "
                                  "be faked.")
                else:
                    blocks.append((f"<@{m['author_id']}>\n" if len(case.messages) > 1 else "")
                                  + f"{ui_kit.hidden(m['content'])}\n[Jump to Message]({m['jump']})")
            reported = "\n".join(blocks)
            if any(not m.get("dm") for m in case.messages):
                reported += "\n-# Saved when it was reported, so it stays here even if it gets deleted."
        where = channel.mention if channel else "A DM between them, so there's no channel to open."
        if voice:
            people = [m.mention for m in voice.members if not m.bot]
            more = f" and {len(people) - 15} more" if len(people) > 15 else ""
            where += f" · {len(people)} {'person' if len(people) == 1 else 'people'}: " + " ".join(people[:15]) + more

        claim = db.get_claim(case.message.id) if case.message else None
        count = len(case.reporters)
        status = (f"**{'Urgent' if urgent else 'Open'}** · {f'Claimed by <@{claim[0]}>' if claim else 'Unclaimed'}"
                  f" · {count} report{'' if count == 1 else 's'}")
        title = "Urgent Report" if urgent else "Voice Report" if voice else "Report"
        buttons = action_buttons(guild.id, channel.id if channel else None, case.target_id, claim=claim)
        in_server = [m for m in case.messages if not m.get("dm")]  # can't delete DMs
        if in_server:
            last = in_server[-1]
            delete = discord.ui.Button(label="Delete Message", custom_id=_cid("delmsg", case.target_id, last["channel_id"],
                                                                            0, last["message_id"]))
            rows = [i for i, b in enumerate(buttons) if b is None]
            buttons.insert(rows[1] if len(rows) > 1 else (rows[0] if rows else len(buttons)), delete)
        return ui_kit.alert(
            ui_kit.titled("voice" if voice else "report", title + (f" · #{case.case_no}" if case.case_no else "")),
            f"{channel.mention if channel else 'Reported from a DM'} · Reported <t:{int(case.started)}:R>"
            + (f" · <@&{s.staff_role_id}>" if s.staff_role_id else ""),
            status,
            [("member", "Who", "\n".join(who)), ("report", "What Happened", what),
             ("cases", "Reported Message", reported), ("voice" if voice else "cases", "Where", where)],
            buttons=buttons,
            thumbnail=target.display_avatar.url if target else None,
            footer="A minor might be at risk. Report it to Discord too: https://dis.gd/request" if minor else None,
        )


async def setup(bot):
    await bot.add_cog(Reports(bot))
