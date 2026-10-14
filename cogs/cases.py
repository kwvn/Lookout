import io
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui_kit
from cogs.staff import LABELS

PREFIX = "lkcases"
PER_PAGE = 6
KIND = {"voice": "Voice Report", "report": "Report", "image": "Image Removed", "voicemsg": "Voice Message",
        "teen": "Teen Safety", "risk": "Warning Signs", "age": "Age Mismatch"}
STATUS_CHOICES = [app_commands.Choice(name=n, value=v) for n, v in
                  (("all", "all"), ("open", "open"), ("claimed", "claimed"), ("resolved", "resolved"))]


def _status(c: dict) -> str:
    if c["status"] == "resolved":
        return "resolved"
    return "claimed" if c["claimed_by"] else "open"


def _jump(guild_id, c):
    return f"https://discord.com/channels/{guild_id}/{c['staff_channel_id']}/{c['message_id']}"


def list_view(guild: discord.Guild, status: str, member_id: int, page: int) -> ui_kit.ui.LayoutView:
    cases, total = db.list_cases(guild.id, status, member_id or None, PER_PAGE, page * PER_PAGE)
    counts = db.case_counts(guild.id)
    pages = max(1, -(-total // PER_PAGE))
    sub = f"{counts['open']} open · {counts['claimed']} claimed · {counts['resolved']} resolved"
    filters = [status] if status != "all" else []
    if member_id:
        filters.append(f"<@{member_id}>")
    if filters:
        sub += "\nShowing: " + ", ".join(filters)

    rows = []
    for c in cases:
        meta = [f"<#{c['channel_id']}>" if c["channel_id"] else None,
                f"<@{c['target_id']}>" if c["target_id"] else None,
                discord.utils.escape_markdown((c["summary"] or "")[:80]) or None]
        rows.append(f"**#{c['case_no']} {KIND.get(c['kind'], c['kind'])}** · {_status(c)}"
                    f" · <t:{int(c['created_at'])}:R>\n-# " + " · ".join(m for m in meta if m))
    empty = "No cases match that filter." if filters else "No cases yet."
    body = "\n\n".join(rows) if rows else empty

    items = []
    if cases:
        select = discord.ui.Select(custom_id=f"{PREFIX}:open:{status}:{member_id}:{page}",
                                   placeholder="Open a case...",
                                   options=[discord.SelectOption(label=f"#{c['case_no']} {KIND.get(c['kind'], c['kind'])}",
                                                                 description=f"{_status(c)} · {(c['summary'] or '')[:60]}",
                                                                 value=str(c["case_no"])) for c in cases])
        items.append(select)
    if pages > 1:
        items += [
            discord.ui.Button(label="Previous", custom_id=f"{PREFIX}:page:{status}:{member_id}:{page - 1}",
                              disabled=page <= 0),
            discord.ui.Button(label=f"{page + 1} / {pages}", custom_id=f"{PREFIX}:noop:{page}", disabled=True),
            discord.ui.Button(label="Next", custom_id=f"{PREFIX}:page:{status}:{member_id}:{page + 1}",
                              disabled=page >= pages - 1),
        ]
    return ui_kit.card("Cases", body, subtitle=sub, buttons=items or None, icon="cases")


def detail_view(guild: discord.Guild, c: dict, back: str) -> ui_kit.ui.LayoutView:
    details = ui_kit.kv([
        ("Type", KIND.get(c["kind"], c["kind"])),
        ("Channel", f"<#{c['channel_id']}>" if c["channel_id"] else None),
        ("Member", f"<@{c['target_id']}>" if c["target_id"] else "Not named"),
        ("Summary", discord.utils.escape_markdown(c["summary"] or "") or None),
    ])
    actions = db.alert_actions(c["message_id"])
    handling = ui_kit.kv([
        ("Claimed By", f"<@{c['claimed_by']}>" if c["claimed_by"] else "Nobody yet"),
        ("Actions Taken", "\n".join(f"- {a}" for a in actions) if actions else "None yet"),
        ("Resolved By", f"<@{c['resolved_by']}> · <t:{int(c['resolved_at'])}:R>" if c["resolved_by"] else None),
        ("Note", discord.utils.escape_markdown(c["note"]) if c["note"] else None),
    ])
    buttons = [discord.ui.Button(label="Back", custom_id=f"{PREFIX}:page:{back}"),
               discord.ui.Button(label="Open Alert", style=discord.ButtonStyle.link, url=_jump(guild.id, c))]
    if c["review_url"]:
        buttons.append(discord.ui.Button(label="Voice Review", style=discord.ButtonStyle.link, url=c["review_url"]))
    if c["screenshot_url"]:
        buttons.append(discord.ui.Button(label="Screenshot", style=discord.ButtonStyle.link, url=c["screenshot_url"]))
    buttons.append(export_button(c["case_no"]))
    buttons.append(discord.ui.Button(label="Delete Case", custom_id=f"lkclean:ask:case:{c['case_no']}"))
    return ui_kit.card("Cases", ("Details", details), ("Handling", handling), icon="cases", buttons=buttons,
                       subtitle=f"**Case #{c['case_no']}** · {_status(c)} · opened <t:{int(c['created_at'])}:R>")


# export, the alert as a plain text file + the times + their history

COPIES = ("teen_flirty", "teen_contact")  # history entries that are copies of removed messages
ACTIONS = ("staff_timeout", "staff_untimeout", "staff_disconnect", "staff_delmsg")
_MENTION = re.compile(r"<@!?(\d+)>")
_TOKEN = re.compile(r"<a?:\w+:\d+>|<@!?(\d+)>|<@&(\d+)>|<#(\d+)>|<t:(\d+)(?::\w)?>|</([\w -]+):\d+>"
                    r"|\[([^\]]+)\]\(<?([^)>\s]+)>?\)")
_COPY = re.compile(r"\|\|`(.*?)`\|\|", re.S)
_STAMP_TIME = re.compile(r"\s*·\s*<t:(\d+)(?::\w)?>\s*$")


def export_button(case_no: int) -> discord.ui.Button:
    return discord.ui.Button(label="Export Case", custom_id=f"{PREFIX}:export:{case_no}")


def _when(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))


# discord markdown to plain text, message copies stay exactly as they were
def _plain(text: str, guild: discord.Guild, names: dict[int, str]) -> str:
    copies = []

    def keep(m):
        copies.append(m.group(1))
        return f"\x00{len(copies) - 1}\x00"

    def swap(m):
        user, role, chan, ts, cmd, label, url = m.groups()
        if user:
            return "@" + names.get(int(user), user)
        if role:
            r = guild.get_role(int(role))
            return "@" + (r.name if r else "deleted-role")
        if chan:
            ch = guild.get_channel_or_thread(int(chan))
            return "#" + (ch.name if ch else "deleted-channel")
        if ts:
            return _when(int(ts))
        if cmd:
            return "/" + cmd
        if label:
            return f"{label}: {url}"
        return ""  # emoji

    text = _TOKEN.sub(swap, _COPY.sub(keep, text))
    text = re.sub(r"^(?:#{1,3}|-#|>) ", "", text, flags=re.M)
    text = re.sub(r"\\([*_~|>#-])", r"\1", text.replace("**", "").replace("`", ""))
    return re.sub("\x00(\\d+)\x00", lambda m: f'"{copies[int(m.group(1))]}"', text).strip()


async def _names(bot, guild: discord.Guild, ids: set[int]) -> dict[int, str]:
    names = {}
    for uid in ids:
        user = guild.get_member(uid) or bot.get_user(uid)
        if user is None:
            try:
                user = await bot.fetch_user(uid)
            except discord.HTTPException:
                continue
        names[uid] = user.name
    return names


async def export_text(bot, guild: discord.Guild, c: dict, by: discord.Member) -> tuple[str, bool]:
    head, parts, stamps = None, [], []
    channel = guild.get_channel(c["staff_channel_id"] or 0)
    try:
        msg = await channel.fetch_message(c["message_id"]) if channel else None
        view = discord.ui.LayoutView.from_message(msg, timeout=None) if msg else None
    except Exception:  # deleted, or an alert old enough to look different
        view = None
    if view:
        if view.children and isinstance(view.children[0], discord.ui.TextDisplay):
            head = view.children[0].content
        boxes = [i for i in view.children if isinstance(i, discord.ui.Container)]
        for box in boxes:
            last = box is boxes[-1]
            for item in box.walk_children():
                if not isinstance(item, discord.ui.TextDisplay) or item.content.startswith("## Resolved"):
                    continue
                if last and (item.id or 0) >= ui_kit.STAFF_NOTE_ID:     # claimed, warned, notes...
                    stamps.append(item.content)
                elif not (last and item.content.startswith("-# ")):     # skip the footer
                    parts.append(item.content)
    if not stamps:  # no alert to read, the db still has the actions
        stamps = ([f"claimed by <@{c['claimed_by']}>"] if c["claimed_by"] else []) + db.alert_actions(c["message_id"])

    history = sorted(db.history(guild.id, c["target_id"], 50), key=lambda r: r[4]) if c["target_id"] else []
    raw = "\n".join([head or "", *parts, *stamps] + [r[1] or "" for r in history if r[0] not in COPIES])
    ids = {int(i) for i in _MENTION.findall(raw)} | {r[2] for r in history if r[2]}
    ids |= {i for i in (c["target_id"], c["claimed_by"], c["resolved_by"], by.id) if i}
    names = await _names(bot, guild, ids)
    minor = c["kind"] in ("teen", "risk") or "(under 18)" in raw

    def plain(text):
        return _plain(text, guild, names)

    title = plain(head.split("\n")[0]) if head else f"{KIND.get(c['kind'], c['kind'])} · #{c['case_no']}"
    lines = [title, f"{guild.name} ({guild.id})", f"Exported {_when(time.time())} UTC by @{by.name}"]
    if minor:
        lines.append("Has a copy of a message sent to someone under 18. Only share it with Discord or the police.")
    width = max(map(len, names.values()), default=0) + 3
    people = sorted(names.items(), key=lambda p: (p[0] != c["target_id"], p[1]))    # the member first
    lines += ["", "People"] + [f"@{name}".ljust(width) + str(uid) for uid, name in people]
    for part in parts or ([f"## Summary\n{c['summary']}"] if c["summary"] else []):
        lines += ["", plain(part)]

    lines += ["", "Log (UTC)", f"{_when(c['created_at'])}  Opened"]
    for stamp in stamps:
        first, _, rest = stamp.partition("\n")
        m = _STAMP_TIME.search(first)
        text = plain(first[:m.start()] if m else first)
        lines.append(f"{_when(int(m.group(1))) if m else '':<16}  {text[:1].upper()}{text[1:]}")
        lines += [" " * 18 + line for line in plain(rest).splitlines()]
    if c["status"] == "resolved" and c["resolved_at"]:
        lines.append(f"{_when(c['resolved_at'])}  Resolved by @{names.get(c['resolved_by'], 'a mod')}: "
                     f"{c['note'] or 'no note'}")

    if history:
        lines += ["", "History (last 90 days)"]
        for kind, detail, actor, _, created_at, _ in history:
            who = f"@{names.get(actor, actor)}"
            if kind in ACTIONS and actor and detail:    # same line the alert got, like "timed out @x for 1 hour"
                text = f"{who} {plain(detail)}"
            else:
                text = LABELS.get(kind, kind) + (f" by {who}" if actor and kind.startswith("staff_") else "")
                if detail:
                    text += f': "{" / ".join(detail.splitlines())}"' if kind in COPIES else f": {plain(detail)}"
            lines.append(f"{_when(created_at)}  {text}")
    return "\n".join(lines) + "\n", minor


class Cases(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="cases", description="Browse open and past alerts (mods only)")
    @app_commands.describe(status="Which cases to show", member="Only cases about this member")
    @app_commands.choices(status=STATUS_CHOICES)
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def cases(self, interaction: discord.Interaction, status: app_commands.Choice[str] | None = None,
                    member: discord.Member | None = None):
        if not self.bot.is_staff(interaction.user):
            return await interaction.response.send_message(view=ui_kit.note("Only mods can see cases."), ephemeral=True)
        view = list_view(interaction.guild, status.value if status else "all", member.id if member else 0, 0)
        await interaction.response.send_message(view=view, files=ui_kit.files(view), ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        data = interaction.data or {}
        parts = data.get("custom_id", "").split(":")
        if parts[0] != PREFIX or len(parts) < 3:
            return
        if not isinstance(interaction.user, discord.Member) or not self.bot.is_staff(interaction.user):
            return await interaction.response.send_message(view=ui_kit.note("Only mods can see cases."), ephemeral=True)
        if parts[1] == "export":
            return await self._export(interaction, int(parts[2]))
        if len(parts) < 5:
            return
        action, status, member_id, page = parts[1], parts[2], int(parts[3]), int(parts[4])
        guild = interaction.guild
        if action == "page":
            view = list_view(guild, status, member_id, max(0, page))
        elif action == "open":
            case = db.get_case(guild.id, int((data.get("values") or ["0"])[0]))
            if case is None:
                return await interaction.response.send_message(view=ui_kit.note("That case doesn't exist anymore."),
                                                               ephemeral=True)
            view = detail_view(guild, case, back=f"{status}:{member_id}:{page}")
        else:
            return
        await interaction.response.edit_message(view=view, attachments=ui_kit.files(view),
                                                allowed_mentions=discord.AllowedMentions.none())

    async def _export(self, interaction: discord.Interaction, case_no: int):
        c = db.get_case(interaction.guild.id, case_no)
        if c is None:
            return await interaction.response.send_message(view=ui_kit.note("That case doesn't exist anymore."),
                                                           ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        text, minor = await export_text(self.bot, interaction.guild, c, interaction.user)
        name = f"lookout-case-{case_no}.txt"
        view = ui_kit.card(
            ui_kit.titled("cases", f"Case #{case_no} Export"),
            "Here's the whole case as a text file, so you can keep it after Lookout deletes it.",
            ("Before You Share It", "It has a copy of a message sent to someone under 18. Only share it with "
                                    "**Discord's safety team** or the **police**.") if minor else None)
        view.add_item(discord.ui.File(f"attachment://{name}"))
        await interaction.followup.send(view=view, file=discord.File(io.BytesIO(text.encode()), filename=name),
                                        ephemeral=True)


async def setup(bot):
    await bot.add_cog(Cases(bot))
