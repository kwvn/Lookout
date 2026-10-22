import asyncio
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
import db
import ui_kit

try:
    import wordlists
except ImportError:  # not public, see wordlists_example.py
    import wordlists_example as wordlists

PREFIX = "lksetup"  # custom_id prefix
TEST_DAYS = 7  # warning signs test mode, in days
MEERKATS = len(list((config.ASSETS / "meerkat").glob("looky_*.png")))  # how many emojis Add Emojis gives a server

REMINDERS = [(0, "Off"), (5, "One ping after 5 minutes"), (10, "One ping after 10 minutes"),
             (15, "One ping after 15 minutes"), (30, "One ping after 30 minutes")]
AGE_MODES = [("tbd", "Teen by Default (safest)"), ("roles", "Teen and Adult Roles")]
ADULT_TIP = ("**Tip:** make an age-restricted channel with a reaction role for 18+. Discord only lets verified adults "
             "into age-restricted channels, so only real adults can get the role.")
PROTECTIONS = [
    ("flirty_filter", "Message Filter", "Removes inappropriate messages adults send to teens."),
    ("contact_filter", "Contact Info Filter", "Removes phone numbers, emails and socials sent to teens."),
    ("teen_restrict", "Keep Away After 2 Reports", "If 2 teens report the same adult, their replies to teens get "
                                                   "removed until the mods decide. Teens who joined this week or have "
                                                   "a new account don't count."),
    ("nick_check", "Nickname Check", "Teens get a tip when their nickname shows their age, school or city."),
    ("tips_dm", "Safety Tips DM", "Teens get the safety tips once, the first time Lookout sees them."),
    ("alt_catcher", "Alt Catcher", "Lets the mods know when someone who left during a case is back on a new account."),
]
TOGGLES = {name: (title, text) for name, title, text in PROTECTIONS} | {
    "network": ("Lookout Network", "Lets the mods know when someone Lookout caught sending inappropriate messages to "
                                   "teens in other servers shows up here. Turning it off also stops sharing."),
    "age_check": ("Age Mismatch", "Lets the mods know when someone says an age that doesn't match their age role."),
    "report_updates": ("Reporter Updates", "Reporters get a DM when the mods close their case."),
    "image_scan": ("Image Filter", "Removes inappropriate images in channels teens can see, as soon as they're "
                                   "posted."),
    "weekly_summary": ("Weekly Summary", "Posts the week's numbers in the mod channel every Monday."),
}
PAGE_OF = {"filter_languages": "langs", "staff_channel": "staff", "staff_role": "staff", "reminder": "staff",
           "young_roles": "ages", "adult_roles": "ages", "no_age_role": "ages", "age_check": "ages", "age_mode": "ages",
           "adult_button": "ages",
           "post_panel": "reports", "post_tips": "reports", "report_updates": "reports",
           "image_scan": "extras", "weekly_summary": "extras", "trivia_channel": "extras", "network": "extras"}
# channel names that look like dating or rating each other's looks, for the Safety Checkup
DATING = re.compile(r"dating|e-?date|couples?|selfies?|rate-?me|smash-?or-?pass|hot-?or-?not|lf-?(?:bf|gf)|"
                    r"looking-?for-?(?:love|a-?bf|a-?gf)", re.I)
VERIFICATION = {
    discord.VerificationLevel.none: "None. Anyone can chat right away.",
    discord.VerificationLevel.low: "Low. Members only need a verified email.",
    discord.VerificationLevel.medium: "Medium. Brand-new accounts have to wait 5 minutes before they can chat.",
    discord.VerificationLevel.high: "High. New members have to wait 10 minutes before they can chat.",
    discord.VerificationLevel.highest: "Highest. Members need a verified phone number.",
}


def permission_report(guild: discord.Guild, s: db.GuildSettings) -> tuple[str, bool]:
    me = guild.me
    gp = me.guild_permissions
    staff = guild.get_channel(s.staff_channel_id) if s.staff_channel_id else None
    sp = staff.permissions_for(me) if staff else None
    checks = [
        (bool(sp and sp.view_channel and sp.send_messages), "Send Messages", "mod alerts"),
        (bool(sp and sp.attach_files), "Attach Files", "screenshots and summary pictures"),
        (gp.read_message_history, "Read Message History", "replies to alerts"),
        (gp.move_members, "Move Members", "the Disconnect button"),
        (gp.moderate_members, "Moderate Members", "the Timeout button"),
        (gp.manage_messages, "Manage Messages", "removing inappropriate messages, contact info and images"),
    ]
    missing = [(label, why) for ok, label, why in checks if not ok]
    risky = [n for n, v in (("Administrator", gp.administrator), ("Manage Server", gp.manage_guild),
                            ("Manage Roles", gp.manage_roles), ("Ban Members", gp.ban_members),
                            ("Kick Members", gp.kick_members), ("Manage Webhooks", gp.manage_webhooks),
                            ("Mention Everyone", gp.mention_everyone)) if v]
    if not s.staff_channel_id:
        text = "**Not checked yet.** Pick a mod channel first."
    elif missing:
        text = f"{ui_kit.kv(missing)}\n-# {len(missing)} missing. Add them to Lookout's role or to the mod channel."
    else:
        text = "Lookout has every permission it needs."
    if risky:
        text += f"\n**Not Needed:** {', '.join(risky)}\n-# Lookout doesn't use these. Removing them keeps your server safer."
    return text, not risky and not missing


def _button(label: str, action: str, arg: str = "") -> discord.ui.Button:
    return discord.ui.Button(label=label, custom_id=f"{PREFIX}:{action}:{arg}")


def _roles(s: db.GuildSettings, *fields: str) -> str:
    ids = set().union(*(s.role_ids(f) for f in fields))
    return " ".join(f"<@&{r}>" for r in sorted(ids)) if ids else "Not set"


# one line of a page: **Title** · status, a small line under it, one button on the right
def _row(title: str, status: str | None, text: str, button: discord.ui.Button) -> discord.ui.Section:
    return discord.ui.Section(f"**{title}**" + (f" · {status}" if status else "") + f"\n-# {text}", accessory=button)


def _toggle(s: db.GuildSettings, field: str) -> discord.ui.Section:
    title, text = TOGGLES[field]
    on = getattr(s, field)
    return _row(title, "On" if on else "Off", text, _button("Turn Off" if on else "Turn On", "toggle", field))


# a channel mention, None if it isn't set or was deleted
def _channel(guild, channel_id: int | None) -> str | None:
    return f"<#{channel_id}>" if channel_id and guild.get_channel(channel_id) else None


# rows in one box with thin lines between them. a row can be a list, like a text and its menu
def _list(*rows) -> list:
    body = []
    for row in rows:
        if not row:
            continue
        if body:
            body.append(discord.ui.Separator())
        body.extend(row if isinstance(row, list) else [row])
    return body


def _risk_text(s: db.GuildSettings) -> str:
    mode = s.risk_now()
    if mode == "test":
        days = max(1, round((s.risk_test_until - time.time()) / 86400)) if s.risk_test_until else TEST_DAYS
        return f"Test mode, {days} day{'' if days == 1 else 's'} left"
    return "On" if mode == "on" else "Off"


def _select(field: str, choices: list, current, placeholder: str) -> discord.ui.Select:
    return discord.ui.Select(custom_id=f"{PREFIX}:set:{field}", placeholder=placeholder, options=[
        discord.SelectOption(label=label, value=str(value), default=str(value) == str(current))
        for value, label in choices])


# banner with the server and status drawn in, each box is a list of rows, nav buttons under the boxes
def _card(guild, title: str, status: str, *boxes, nav=(), footer=None):
    view = ui_kit.card(title, *(_list(*box) for box in boxes if box), footer=footer, icon="settings",
                       subtitle=f"{discord.utils.escape_markdown(guild.name)} · {status}",
                       line=f"{guild.name} · {status}")
    if nav:
        view.add_item(ui_kit.button_row(*nav))
    return view


# panel

# a home block: a heading, the details under it, one button on the right
def _block(title: str, details: list, button: discord.ui.Button, note: str | None = None) -> discord.ui.Section:
    return discord.ui.Section(f"### {title}\n{ui_kit.kv(details)}" + (f"\n{note}" if note else ""), accessory=button)


def home(guild: discord.Guild, s: db.GuildSettings, note: str | None = None):
    perms, ok = permission_report(guild, s)
    mods = _channel(guild, s.staff_channel_id)
    reminder = dict(REMINDERS).get(s.reminder_minutes, f"One ping after {s.reminder_minutes} minutes")
    staff = _block("Mods", [("Mod Channel", mods or "Not set"),
                            ("Mod Role", f"<@&{s.staff_role_id}>" if s.staff_role_id else "Anyone with Timeout Members"),
                            ("Claim Reminder", reminder)],
                   _button("Edit" if mods else "Set Up", "page", "staff"))

    on = sum(getattr(s, f) for f, _, _ in PROTECTIONS) + (s.risk_now() != "off")
    if s.teen_safety:
        teen = _block("Teen Safety", [
            ("Status", f"On, {on} of {len(PROTECTIONS) + 1} protections"),
            ("Teens", f"Everyone without {_roles(s, 'adult_roles')} (Teen by Default)") if s.teen_default else
            ("Teen Roles", _roles(s, "young_roles", "older_roles")),
            ("Adult Roles", None if s.teen_default else _roles(s, "adult_roles")),
            ("Warning Signs", _risk_text(s))], _button("Edit", "page", "teen"))
    else:
        teen = _block("Teen Safety", [("Status", "Off")], _button("Set Up", "page", "teen"),
                      "Pick your age roles to turn on the filters.")

    panel, tips = _channel(guild, s.panel_channel_id), _channel(guild, s.tips_channel_id)
    reports = _block("Reports", [("Report Button", f"Posted in {panel}" if panel else "Not posted yet"),
                                 ("Safety Tips", f"Posted in {tips}" if tips else "Not posted yet"),
                                 ("Reporter Updates", "On" if s.report_updates else "Off")],
                     _button("Edit" if panel else "Post It", "page", "reports"))

    left = None if s.checkup_score is None else max(0, len(CHECKS) - s.checkup_score)
    if left is None:
        checkup = _block("Safety Checkup", [("Status", "Not run yet")], _button("Run It", "checkup"),
                         "Checks a few of your server's own Discord settings in one go.")
    elif not left:
        checkup = _block("Safety Checkup", [("Status", f"All good, {len(CHECKS)} of {len(CHECKS)}")],
                         _button("Check Again", "checkup"), "Your server's own settings look good.")
    else:
        checkup = _block("Safety Checkup", [("Status", f"{left} to fix")], _button("Review", "checkup"),
                         "One of your server's own settings could keep teens safer." if left == 1 else
                         "A few of your server's own settings could keep teens safer.")

    trivia = _channel(guild, s.trivia_channel_id)
    extra = _block("Extras", [("Image Filter", "On" if s.image_scan else "Off"),
                              ("Lookout Network", "On" if s.network else "Off"),
                              ("Weekly Summary", "On" if s.weekly_summary else "Off"),
                              ("Trivia", trivia or "Not set")], _button("Open", "page", "extras"))

    broken = bool(mods) and not ok  # missing permissions, or ones lookout doesn't need
    # name the sections, a count read like the Safety Checkup's own number
    fix = [name for name, bad in (("Mods", not mods), ("Teen Safety", not s.teen_safety), ("Permissions", broken),
                                  ("Safety Checkup", bool(left))) if bad]
    if not fix:
        status = "Everything's working"
    elif len(fix) == 1:
        status = f"{fix[0]} needs a fix"
    else:
        status = f"{', '.join(fix[:-1])} and {fix[-1]} need a fix"
    return _card(guild, "Settings", status,
                 [staff, teen, reports, f"### Permissions\n{perms}", checkup, extra], footer=note)


def staff_page(guild, s: db.GuildSettings):
    channel = discord.ui.ChannelSelect(custom_id=f"{PREFIX}:set:staff_channel", channel_types=[discord.ChannelType.text],
                                       placeholder="Pick the mod channel...",
                                       default_values=[discord.Object(s.staff_channel_id)] if s.staff_channel_id else [])
    role = discord.ui.RoleSelect(custom_id=f"{PREFIX}:set:staff_role", placeholder="Pick the mod role...",
                                 default_values=[discord.Object(s.staff_role_id)] if s.staff_role_id else [])
    return _card(guild, "Mods", "Where alerts go and who handles them",
                 [["**Mod Channel**\n-# The private channel where Lookout sends every alert", channel],
                  ["**Mod Role**\n-# Gets pinged for urgent alerts and can use the alert buttons", role],
                  ["**Claim Reminder**\n-# If nobody claims an urgent alert, Lookout pings the mods one more time",
                   _select("reminder", REMINDERS, s.reminder_minutes, "Claim reminder")]],
                 nav=[_button("Back", "page", "home")])


def teen_page(guild, s: db.GuildSettings):
    on = sum(getattr(s, f) for f, _, _ in PROTECTIONS) + (s.risk_now() != "off")
    if not s.teen_safety:
        who = (("Pick your 18+ role" if s.teen_default else "Pick at least one teen role")
               + ". Until then, nothing below is working yet")
    elif s.teen_default:
        who = f"Everyone without {_roles(s, 'adult_roles')} (Teen by Default)"
        adult = _channel(guild, s.adult_channel_id)
        if adult:
            who += f" · 18+ button in {adult}"
    else:
        who = f"Teen roles: {_roles(s, 'young_roles', 'older_roles')} · Adult roles: {_roles(s, 'adult_roles')}"
    ages = _row("Who Counts as a Teen", None, who,
                _button("Change" if s.teen_safety else "Pick Age Roles", "page", "ages"))
    risk = _row("Warning Signs", _risk_text(s), "Adds up red flags in what adults say to teens over 30 days",
                _button("Open", "page", "risk"))
    nicknames = guild.me.guild_permissions.manage_nicknames
    return _card(guild, "Teen Safety", f"On, {on} of {len(PROTECTIONS) + 1} protections" if s.teen_safety else "Off",
                 [ages, *(_toggle(s, f) for f, _, _ in PROTECTIONS), risk],
                 footer=None if nicknames else "Nickname Check can only fix a nickname for a teen if Lookout has "
                                               "**Manage Nicknames**.",
                 nav=[_button("Back", "page", "home"), _button("Languages", "page", "langs")])


# finding age roles

_NOT_AGE = re.compile(r"\b(?:mods?|moderators?|admins?|staff|helpers?|bots?|levels?|lvl)\b", re.I)
_TEEN_WORDS = re.compile(r"\b(?:minors?|under\s*18|u18|teens?|underage)\b", re.I)
_ADULT_WORDS = re.compile(r"\b(?:adults?|over\s*18|of\s*age)\b|(?<!\d)18\s*(?:\+|plus\b)", re.I)
_AGE_NUMBER = re.compile(r"(?:age\s*)?(\d\d)(?:\s*[-–]\s*\d\d)?\s*(?:\+|yo|y/o|yrs?|years?(?: old)?)?", re.I)


# "teen", "adult" or None from a role name
def age_role_kind(name: str) -> str | None:
    name = re.sub(r"[^\w\s+\-–/]", " ", name).strip()  # strip emojis and decorations
    if _NOT_AGE.search(name):
        return None
    if _TEEN_WORDS.search(name):
        return "teen"
    if _ADULT_WORDS.search(name):
        return "adult"
    number = _AGE_NUMBER.fullmatch(name)
    if number:
        age = int(number.group(1))
        return "teen" if 13 <= age <= 17 else "adult" if age >= 18 else None
    return None


def guess_age_roles(guild: discord.Guild) -> tuple[list[int], list[int]]:
    teen, adult = [], []
    for role in getattr(guild, "roles", []):
        if role.is_default() or role.managed:
            continue
        kind = age_role_kind(role.name)
        (teen if kind == "teen" else adult if kind == "adult" else []).append(role.id)
    return teen[:10], adult[:10]


# onboarding answers like "I'm 13-17" or "18 or older"
_AGE_QUESTION = re.compile(r"\bage\b|\bhow old\b|\bold are\b|\byears? old\b|\b18\b", re.I)
_TEEN_ANSWER = re.compile(r"\b(?:under|younger than)\s*18\b|\b17\s*(?:or|and)\s*(?:under|younger|below)\b"
                          r"|(?<!\d)1[3-7](?:\s*[-–]\s*1[3-7])?(?!\d)", re.I)
_ADULT_ANSWER = re.compile(r"\b(?:over|older than)\s*1[78]\b|\b18\s*(?:or|and)\s*(?:over|older|up|above)\b"
                           r"|(?<!\d)(?:1[89]|[2-9]\d)\s*(?:\+|plus\b)|\badults?\b", re.I)


def answer_kind(title: str) -> str | None:
    kind = age_role_kind(title)
    if kind:
        return kind
    return "adult" if _ADULT_ANSWER.search(title) else "teen" if _TEEN_ANSWER.search(title) else None


# guild id: (teen, adult, onboarding question) from the last lookup
_FOUND: dict[int, tuple[list[int], list[int], str | None]] = {}


# onboarding first (works whatever the roles are called), then role names
async def find_age_roles(guild: discord.Guild) -> tuple[list[int], list[int], str | None]:
    try:
        onboarding = await asyncio.wait_for(guild.onboarding(), timeout=2)
    except Exception:  # no onboarding, no access, or too slow
        onboarding = None
    roles = {r.id: r for r in getattr(guild, "roles", []) if not r.is_default() and not r.managed}
    found = None
    try:
        for prompt in (onboarding.prompts if onboarding else []):
            title = getattr(prompt, "title", None) or ""
            kind_of = answer_kind if _AGE_QUESTION.search(title) else age_role_kind
            teen, adult = [], []
            for option in prompt.options:
                kind = kind_of(getattr(option, "title", None) or "")
                ids = [r for r in getattr(option, "role_ids", ()) if r in roles]
                (teen if kind == "teen" else adult if kind == "adult" else []).extend(ids)
            if teen:
                by_name = guess_age_roles(guild)[1]
                found = (list(dict.fromkeys(teen))[:10], (list(dict.fromkeys(adult)) or by_name)[:10], title or None)
                break
    except Exception:  # weird onboarding data, use role names
        found = None
    _FOUND[guild.id] = found or (*guess_age_roles(guild), None)
    return _FOUND[guild.id]


def _found_box(guild, action: str):
    teen, adult, question = _FOUND.get(guild.id) or (*guess_age_roles(guild), None)
    if not teen:
        return None
    how = (f"From your Onboarding question: **{discord.utils.escape_markdown(question)}**" if question else
           "Lookout looked for names like Minor, Under 18, 13-17, Adult and 18+.")
    text = ui_kit.kv([("Teen Roles", " ".join(f"<@&{r}>" for r in teen)),
                      ("Adult Roles", " ".join(f"<@&{r}>" for r in adult) or "None found")])
    return ("Found Your Age Roles", [text + f"\n-# {how}", _button("Use These", action)])


def languages_page(guild, s: db.GuildSettings):
    menu = discord.ui.Select(custom_id=f"{PREFIX}:set:filter_languages", placeholder="Languages", min_values=1,
                             max_values=len(wordlists.NAMES), options=[
                                 discord.SelectOption(label=name, value=code, default=code in s.languages)
                                 for code, name in wordlists.NAMES.items()])
    picked = ", ".join(wordlists.NAMES[c] for c in s.languages if c in wordlists.NAMES)
    return _card(guild, "Languages", f"Message Filter: {picked}",
                 [["**Message Filter Languages**\n-# Pick the languages people use here. Lookout checks messages to "
                   "teens in all of them, with the same spelling-trick cleanup.", menu,
                   "-# Each extra language adds a few false alarms, so only pick the ones your server uses."]],
                 nav=[_button("Back", "page", "teen")])


def ages_page(guild, s: db.GuildSettings):
    def roles(field, placeholder, *also):
        ids = set().union(s.role_ids(field), *(s.role_ids(f) for f in also))
        return discord.ui.RoleSelect(custom_id=f"{PREFIX}:set:{field}", placeholder=placeholder,
                                     min_values=0, max_values=10, default_values=[discord.Object(r) for r in ids])
    mode = ["**How Ages Work**\n-# Teen by Default protects everyone who doesn't have an 18+ role",
            _select("age_mode", AGE_MODES, "tbd" if s.teen_default else "roles", "How ages work")]
    if s.teen_default:
        adult = _channel(guild, s.adult_channel_id)
        where = f"Posted in {adult} · {len(db.verified_adults(s.guild_id))} adults so far" if adult else "Not posted yet"
        post = discord.ui.ChannelSelect(custom_id=f"{PREFIX}:set:adult_button",
                                        channel_types=[discord.ChannelType.text],
                                        placeholder="Post it in an age-restricted channel...")
        return _card(guild, "Age Roles", "Who Lookout treats as a teen",
                     [mode, [f"**Adult Roles**\n-# {ADULT_TIP}", roles("adult_roles", "Pick the 18+ roles...")],
                      [f"**Invisible 18+** · {where}\n-# No role at all. Adults press a button in an age-restricted "
                       "channel, and Lookout remembers them privately. Nobody can see who pressed it.", post],
                      _toggle(s, "age_check")],
                     nav=[_button("Back", "page", "teen")])
    no_role = [("adult", "Count them as 18+"), ("ignore", "Leave them out")]
    found = None if s.teen_safety else _found_box(guild, "found")
    return _card(guild, "Age Roles", "Who Lookout treats as a teen",
                 [[f"**{found[0]}**\n{found[1][0]}", found[1][1]] if found else None, mode,
                  ["**Teen Roles**\n-# Everyone under 18, like 13-15 and 16-17",
                   roles("young_roles", "Pick the teen roles...", "older_roles")],
                  ["**Adult Roles**", roles("adult_roles", "Pick the 18+ roles...")],
                  ["**No Age Role**\n-# Members who haven't picked an age role",
                   _select("no_age_role", no_role, s.no_age_role, "Members without an age role")],
                  _toggle(s, "age_check")],
                 nav=[_button("Back", "page", "teen")])


def reports_page(guild, s: db.GuildSettings):
    panel, tips_channel = _channel(guild, s.panel_channel_id), _channel(guild, s.tips_channel_id)
    post = discord.ui.ChannelSelect(custom_id=f"{PREFIX}:set:post_panel", channel_types=[discord.ChannelType.text],
                                    placeholder="Post it in a channel...")
    tips = discord.ui.ChannelSelect(custom_id=f"{PREFIX}:set:post_tips", channel_types=[discord.ChannelType.text],
                                    placeholder="Post it in a channel...")
    return _card(guild, "Reports", "How members report and what they hear back",
                 [[f"**Report Button** · {f'Posted in {panel}' if panel else 'Not posted yet'}\n"
                   "-# Members press it to report a problem without typing a command", post],
                  [f"**Safety Tips** · {f'Posted in {tips_channel}' if tips_channel else 'Not posted yet'}\n"
                   "-# Safety tips for teens, with a Report a Problem button", tips],
                  _toggle(s, "report_updates")],
                 nav=[_button("Back", "page", "home")])


def extras_page(guild, s: db.GuildSettings):
    where = _channel(guild, s.trivia_channel_id)
    trivia = discord.ui.ChannelSelect(custom_id=f"{PREFIX}:set:trivia_channel", channel_types=[discord.ChannelType.text],
                                      placeholder="Pick your general chat...",
                                      default_values=[discord.Object(s.trivia_channel_id)] if where else [])
    looky = "".join(ui_kit.ICONS.get(f"looky_{m}", "") for m in ("wave", "sus", "love", "watching"))
    return _card(guild, "Extras", "Other protections and reports for the mods",
                 [_toggle(s, "image_scan"), _toggle(s, "network"), _toggle(s, "weekly_summary"),
                  [f"**Trivia** · {where or 'Not set'}\n-# Mods can host safety trivia games with /trivia, only in "
                   "this channel", trivia],
                  _row("Meerkat Emojis", None, f"Adds {MEERKATS} Looky emojis your members can use {looky}",
                       _button("Add Emojis", "emojis")),
                  f"**Your Data**\n-# Cases and history: kept {config.RETENTION_DAYS} days\n"
                  f"-# Warning signs: kept {db.SIGNAL_DAYS} days\n"
                  f"-# Lookout Network: kept {db.NETWORK_DAYS // 30} months\n"
                  "-# Older records are deleted automatically. Everything except Lookout Network records is deleted "
                  "if Lookout leaves the server."],
                 nav=[_button("Back", "page", "home")])


def risk_page(guild, s: db.GuildSettings):
    mode = s.risk_now()
    rows = [f"<@{f['user_id']}> · {f['score']} points · <t:{int(f['flagged_at'])}:R>"
            + (" · test mode, the mods weren't told" if f["test"] else "") for f in db.risk_flags(guild.id)]
    switch = _button({"off": "Start Test", "test": "Turn On Now", "on": "Turn Off"}[mode], "risk",
                     {"off": "test", "test": "on", "on": "off"}[mode])
    return _card(guild, "Warning Signs", _risk_text(s),
                 [_row("Warning Signs", _risk_text(s), "Adds up red flags in what adults say to teens, and teen "
                                                       "reports, over 30 days", switch),
                  "**Flagged in the Last 30 Days**\n" + ("\n".join(rows) or "-# Nobody yet."),
                  "**How It Works**\n-# Lookout adds these up over 30 days: inappropriate messages to teens, contact "
                  "info sent to teens, reports from teens, and adults asking a teen's age, school or where they live, "
                  "or asking them to move to DMs, another app or a call. Those last two are never removed and can't "
                  "flag anyone by themselves. Normal chat never counts. One sign never flags anyone. A case only "
                  "opens when several add up."],
                 nav=[_button("Back", "page", "teen")])


PAGES = {"home": home, "staff": staff_page, "teen": teen_page, "ages": ages_page, "reports": reports_page,
         "extras": extras_page, "risk": risk_page, "langs": languages_page,
         "more": teen_page}     # "more" is on old messages


# safety checkup

CHECKS = ("Image Filter", "Verification Level", "Age Question", "Age-Restricted Channels", "Dating Channels",
          "Lookout's Role", "Mod 2FA")


# (name, ok, text) for each check, failed ones first
async def safety_checkup(guild: discord.Guild, s: db.GuildSettings) -> list[tuple[str, bool, str]]:
    age_roles = [r for r in (guild.get_role(i) for i in s.role_ids("young_roles") | s.role_ids("older_roles")
                             | s.role_ids("adult_roles")) if r]
    teen_roles = [r for r in age_roles if r.id in s.role_ids("young_roles") | s.role_ids("older_roles")]
    results = []

    ok = guild.explicit_content_filter == discord.ContentFilter.all_members
    results.append(("Image Filter", ok, "Discord scans images from everyone." if ok else
                    "Discord's own image filter doesn't scan everyone right now. Set it to scan all members, so "
                    "inappropriate images get removed before a teen sees them.\n-# **Server Settings** > "
                    "**Safety Setup**"))

    level = guild.verification_level
    ok = level >= discord.VerificationLevel.medium
    results.append(("Verification Level", ok, VERIFICATION.get(level, str(level)) if ok else
                    f"{VERIFICATION.get(level, str(level))} Set it to Medium or higher, so brand-new accounts can't "
                    "jump straight in.\n-# **Server Settings** > **Safety Setup**"))

    asked = None
    try:
        onboarding = await guild.onboarding()
        # teen by default: the 18+ role shouldn't be something anyone can pick
        ids = s.role_ids("adult_roles") if s.teen_default else {r.id for r in age_roles}
        asked = onboarding.enabled and any(ids & set(option.role_ids) for prompt in onboarding.prompts
                                           for option in prompt.options)
    except (discord.HTTPException, AttributeError):
        pass
    if s.teen_default:
        ok = not asked
        results.append(("Age Question", ok, "Your 18+ role isn't something anyone can pick in Onboarding." if ok else
                        "Anyone can pick your 18+ role in Onboarding, teens too. Give it out in an age-restricted "
                        "channel instead, so only adults Discord has verified can get it.\n"
                        "-# **Server Settings** > **Onboarding**"))
    elif asked is None:
        results.append(("Age Question", False, "Lookout couldn't read your Onboarding. Make sure new members get "
                                               "asked their age there, with options that give out your age roles.\n"
                                               "-# **Server Settings** > **Onboarding** · Needs Community turned on"))
    else:
        results.append(("Age Question", asked, "New members pick an age role in Onboarding." if asked else
                        "New members aren't asked their age, so Lookout can't tell who's a teen until they pick a "
                        "role. Add a question to Onboarding that gives out your age roles.\n"
                        "-# **Server Settings** > **Onboarding** · Needs Community turned on"))

    seen = [c for c in guild.channels if getattr(c, "nsfw", False)
            and any(c.permissions_for(r).view_channel for r in teen_roles)]
    if s.teen_default:  # no teen roles to check, discord's own age check keeps teens out
        seen = []
    results.append(("Age-Restricted Channels", not seen, "Teens can't see any of them." if not seen else
                    f"Teen roles can see {' '.join(c.mention for c in seen[:5])}. Take View Channel away from your "
                    "teen roles there.\n-# **Edit Channel** > **Permissions**"))

    # discord doesn't allow dating spaces for anyone under 18, a server can be taken down for one
    viewers = teen_roles or ([guild.default_role] if s.teen_default else [])
    dating = [c for c in guild.text_channels if DATING.search(c.name) and not getattr(c, "nsfw", False)
              and any(c.permissions_for(r).view_channel for r in viewers)]
    results.append(("Dating Channels", not dating, "Teens can't see any dating or selfie channels." if not dating else
                    f"Teens can see {' '.join(c.mention for c in dating[:5])}. Discord doesn't allow dating spaces "
                    "for anyone under 18, and a server can be taken down for them. Selfie and rating channels draw in "
                    "people looking for teens too. Delete them, or take **View Channel** away from your teen roles "
                    "there.\n-# **Edit Channel** > **Permissions**"))

    me = guild.me
    ok = not age_roles or all(me.top_role > r for r in age_roles)
    results.append(("Lookout's Role", ok, "It's above your age roles, so timeouts work." if ok else
                    "It's below some of your age roles, so the Timeout button won't work on those members. Drag "
                    "Lookout's role above them.\n-# **Server Settings** > **Roles**"))

    ok = guild.mfa_level == discord.MFALevel.require_2fa
    results.append(("Mod 2FA", ok, "Mods need two-factor authentication to delete messages or ban." if ok else
                    "Mods don't need two-factor authentication. Turn it on, so a hacked mod account can't wreck the "
                    "server.\n-# **Server Settings** > **Safety Setup**"))
    return sorted(results, key=lambda r: r[1])


def checkup_page(guild, s: db.GuildSettings, results: list[tuple[str, bool, str]]):
    passed = sum(ok for _, ok, _ in results)
    fixes = [f"**{name}** · Needs a fix\n{text}" for name, ok, text in results if not ok]
    good = "\n".join(f"**{name}** · Good\n-# {text}" for name, ok, text in results if ok)
    status = f"{passed} of {len(results)} good" + (f" · {len(fixes)} to fix" if fixes else "")
    if not fixes:
        return _card(guild, "Safety Checkup", status, [good],
                     footer="Lookout checks again every Monday and lets you know if something changes.",
                     nav=[_button("Back", "page", "home")])
    return _card(guild, "Safety Checkup", status, fixes, [good] if good else None,
                 footer="Changed something? Check again to update the list.",
                 nav=[_button("Back", "page", "home"), _button("Check Again", "checkup")])


# first-time setup

def _step(number: int, label: str, text: str, items: list, note: str | None = None):
    if note:
        text += f"\n\n**{note}**"
    step = f"Step {number} of 4"
    return ui_kit.card("Set Up Lookout", (label, text), subtitle=step, line=step, buttons=items, icon="settings")


def step_channel(note: str | None = None):
    menu = discord.ui.ChannelSelect(custom_id=f"{PREFIX}:channel", channel_types=[discord.ChannelType.text],
                                    placeholder="Pick the mod channel...")
    return _step(1, "Mod Channel",
                 "Pick the channel where alerts should go. Keep it private, so only your mods can see it.\n"
                 "-# No mod channel yet? Create a private one, then press Set Up again.", [menu], note)


def step_role(s: db.GuildSettings, note: str | None = None):
    menu = discord.ui.RoleSelect(custom_id=f"{PREFIX}:role", placeholder="Pick the mod role...")
    return _step(2, "Mod Role",
                 f"Alerts will go to <#{s.staff_channel_id}>. Now pick the role that gets pinged for alerts "
                 "and can use the alert buttons.\n"
                 "-# If you skip this, anyone with Timeout Members counts as a mod and nobody gets pinged.",
                 [menu, _button("Skip", "skiprole")], note)


def step_ages(s: db.GuildSettings, guild=None, manual: bool = False):
    found = None if (manual or s.teen_safety or guild is None) else _found_box(guild, "gfound")
    if found:
        label, (text, use) = found
        return ui_kit.card("Set Up Lookout", (label, text), subtitle="Step 3 of 4", line="Step 3 of 4", icon="settings",
                           buttons=[use, _button("Pick Different Roles", "gmanual")])

    def roles(field, placeholder, *also):
        ids = set().union(s.role_ids(field), *(s.role_ids(f) for f in also))
        return discord.ui.RoleSelect(custom_id=f"{PREFIX}:gset:{field}", placeholder=placeholder, min_values=0,
                                     max_values=10, default_values=[discord.Object(r) for r in ids])
    done = s.teen_safety
    return _step(3, "Age Roles",
                 "Pick your server's age roles, so Lookout knows who the teens are.\n"
                 "-# No age roles yet? Create them first, or use an age verification bot. You can always do this "
                 "later in /setup.",
                 [roles("young_roles", "Pick the teen roles...", "older_roles"),
                  roles("adult_roles", "Pick the 18+ roles..."), _button("Next" if done else "Skip", "gnext")])


def step_age_mode():
    tbd = discord.ui.Section("**Teen by Default** (safest)\n-# Pick only your 18+ role. Everyone without it is "
                             "protected as a teen, so nobody needs a role that shows they're under 18.",
                             accessory=_button("Use This", "gmode", "tbd"))
    roles = discord.ui.Section("**Teen and Adult Roles**\n-# Pick both. Use this if your server already has roles "
                               "like 13-17 and 18+.", accessory=_button("Use These", "gmode", "roles"))
    return ui_kit.card("Set Up Lookout", ("Age Roles", ["How does your server mark ages?", tbd, roles]),
                       subtitle="Step 3 of 4", line="Step 3 of 4", icon="settings")


def step_adult(s: db.GuildSettings):
    menu = discord.ui.RoleSelect(custom_id=f"{PREFIX}:gset:adult_roles", placeholder="Pick the 18+ role...",
                                 min_values=0, max_values=10,
                                 default_values=[discord.Object(r) for r in s.role_ids("adult_roles")])
    return ui_kit.card("Set Up Lookout",
                       ("Your 18+ Role", ["Pick the role only adults have. Everyone without it is protected as a teen.",
                                          menu, f"-# {ADULT_TIP}\n-# Don't want a role at all? Skip this, then post "
                                                "the invisible 18+ button in **/setup** > **Age Roles**."]),
                       subtitle="Step 3 of 4 · Teen by Default", line="Step 3 of 4 · Teen by Default", icon="settings",
                       buttons=[_button("Back", "gmode", "back"),
                                _button("Next" if s.teen_safety else "Skip", "gnext")])


def step_panel(note: str | None = None):
    menu = discord.ui.ChannelSelect(custom_id=f"{PREFIX}:panel", channel_types=[discord.ChannelType.text],
                                    placeholder="Pick a channel your members can see...")
    return _step(4, "Report Button",
                 "Pick a channel your members can see. Lookout posts a Report a Problem button there, so members can "
                 "report without typing a command.", [menu, _button("Skip", "skippanel")], note)


# the end of the first-time steps. the role tip only shows up when it's actually needed
def all_set(guild, s: db.GuildSettings, demo: str):
    on = [line for ok, line in ((s.teen_safety and s.flirty_filter, "Removing inappropriate messages adults send to teens"),
                                (s.teen_safety and s.contact_filter, "Removing contact info sent to teens"),
                                (s.image_scan, "Removing explicit pictures"),
                                (s.staff_channel_id, f"Sending alerts to <#{s.staff_channel_id}>")) if ok]
    roles = [r for r in (guild.get_role(i) for i in s.role_ids("young_roles") | s.role_ids("older_roles")
                         | s.role_ids("adult_roles")) if r]
    low = [r for r in roles if guild.me.top_role <= r]
    buttons = [_button("Open Settings", "page", "home")]
    if not s.panel_channel_id:
        buttons.append(_button("Post the Report Button", "page", "reports"))
    return ui_kit.card(
        ui_kit.titled(ui_kit.mascot("party"), "You're All Set"),
        ("Lookout Is Now", "\n".join(f"- {line}" for line in on)),
        ("Lookout's Role", f"**It's below {' '.join(r.mention for r in low[:3])}.** Drag it above them in **Server "
                           "Settings** > **Roles**, so the Timeout button works on those members.") if low else None,
        ("Try It", f"See what an alert looks like with {demo}. Only you see it."),
        buttons=buttons)


class Setup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tasks: set[asyncio.Task] = set()

    @app_commands.command(name="setup", description="Set up Lookout and change its settings")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup_cmd(self, interaction: discord.Interaction):
        # servers can change who sees the command in Integrations, so check here too
        user = interaction.user
        if not isinstance(user, discord.Member) or not user.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                view=ui_kit.note("Only admins can set up Lookout. You need the **Manage Server** permission."),
                ephemeral=True)
        s = db.get_settings(interaction.guild.id)
        view = home(interaction.guild, s) if s.configured else step_channel()
        await interaction.response.send_message(view=view, files=ui_kit.files(view), ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        data = interaction.data or {}
        parts = data.get("custom_id", "").split(":") + [""]
        if parts[0] != PREFIX:
            return
        action, arg, guild, user = parts[1], parts[2], interaction.guild, interaction.user
        if not isinstance(user, discord.Member) or not user.guild_permissions.manage_guild:
            return await interaction.response.send_message(
                view=ui_kit.note("Only admins can set up Lookout. You need the **Manage Server** permission."),
                ephemeral=True)
        s = db.get_settings(guild.id)
        values = data.get("values") or []

        async def show(view):
            await interaction.response.edit_message(view=view, attachments=ui_kit.files(view),
                                                    allowed_mentions=discord.AllowedMentions.none())

        async def tell(text):
            await interaction.response.send_message(view=ui_kit.note(text), ephemeral=True)

        def saved(what):
            db.save_settings(s)
            db.log(guild.id, None, "settings_changed", what, actor_id=user.id)
            joins = self.bot.get_cog("JoinLog")
            if joins:  # updates the /join-log post for this server
                task = asyncio.create_task(joins.progress(guild, s))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

        def can_post(channel) -> bool:
            perms = channel.permissions_for(guild.me) if channel else None
            return bool(perms and perms.view_channel and perms.send_messages)

        if action == "start":  # welcome message button
            view = home(guild, s) if s.configured else step_channel()
            return await interaction.response.send_message(view=view, files=ui_kit.files(view), ephemeral=True,
                                                           allowed_mentions=discord.AllowedMentions.none())

        # first-time steps
        if action == "channel":
            channel = guild.get_channel(int(values[0])) if values else None
            if not can_post(channel):
                return await show(step_channel("Lookout can't post in that channel. Give it View Channel and Send "
                                               "Messages, or pick a different one."))
            s.staff_channel_id = channel.id
            saved("mod channel, in the setup steps")
            public = channel.permissions_for(guild.default_role).view_channel
            return await show(step_role(s, "Everyone can see that channel. Alerts should go to a private mod "
                                           "channel." if public else None))
        if not s.configured:  # old setup message, start over
            return await show(step_channel())
        if action in ("role", "skiprole"):
            if action == "role":
                role = guild.get_role(int(values[0])) if values else None
                if role is None or role.is_default() or role.managed:
                    return await show(step_role(s, "Pick your actual mod role, not @everyone or a bot's role."))
                s.staff_role_id = role.id
                saved("mod role, in the setup steps")
            if s.teen_safety:
                return await show(step_adult(s) if s.teen_default else step_ages(s, guild, manual=True))
            return await show(step_age_mode())
        if action == "gmode":
            if arg == "back":
                return await show(step_age_mode())
            self._set_mode(s, arg)
            saved(f"age mode: {arg}, in the setup steps")
            if arg == "tbd":
                return await show(step_adult(s))
            if not s.teen_safety:
                await find_age_roles(guild)
            return await show(step_ages(s, guild))
        if action == "gset":
            self._set_roles(s, arg, values)
            saved(f"{arg}, in the setup steps")
            return await show(step_adult(s) if s.teen_default else step_ages(s, guild, manual=True))
        if action == "gmanual":
            return await show(step_ages(s, guild, manual=True))
        if action in ("gfound", "found"):  # "Use These" on the found age roles
            teen, adult, _ = _FOUND.get(guild.id) or (*guess_age_roles(guild), None)
            self._set_roles(s, "young_roles", [str(r) for r in teen])
            self._set_roles(s, "adult_roles", [str(r) for r in adult])
            saved("age roles, from the ones Lookout found")
            return await show(step_ages(s, guild, manual=True) if action == "gfound" else teen_page(guild, s))
        if action == "gnext":
            return await show(step_panel())
        if action in ("panel", "skippanel"):
            if action == "panel":
                channel = guild.get_channel(int(values[0])) if values else None
                if not await self._post_panel(channel, can_post):
                    return await show(step_panel("Lookout can't post in that channel. Pick a different one, or skip "
                                                 "this step."))
                s.panel_channel_id = channel.id
                saved("report button, in the setup steps")
            demo = self.bot.command_ids.get("demo")
            return await show(all_set(guild, s, f"</demo:{demo}>" if demo else "`/demo`"))

        # panel
        if action == "page":
            if arg == "ages" and not s.teen_safety:
                await find_age_roles(guild)
            return await show(PAGES.get(arg, home)(guild, s))
        if action == "emojis":
            return await self._add_emojis(interaction, guild)
        if action == "checkup":
            # reading onboarding is an api call, defer first
            await interaction.response.defer()
            results = await safety_checkup(guild, s)
            s.checkup_score = sum(ok for _, ok, _ in results)
            db.save_settings(s)
            view = checkup_page(guild, s, results)
            return await interaction.edit_original_response(view=view, attachments=ui_kit.files(view),
                                                            allowed_mentions=discord.AllowedMentions.none())
        if action == "toggle" and arg in TOGGLES:
            setattr(s, arg, not getattr(s, arg))
            saved(f"{arg}: {'on' if getattr(s, arg) else 'off'}")
            if arg == "network" and not s.network:
                db.stop_sharing(guild.id)
            return await show(PAGES[PAGE_OF.get(arg, "teen")](guild, s))
        if action == "risk" and arg in ("off", "test", "on"):
            s.risk_mode = arg
            if arg == "test":
                s.risk_test_until = time.time() + TEST_DAYS * 86400
            saved(f"warning signs: {arg}")
            return await show(risk_page(guild, s))
        if action != "set":  # old button, just show the panel
            return await show(home(guild, s))

        if arg == "staff_channel":
            channel = guild.get_channel(int(values[0])) if values else None
            if not can_post(channel):
                return await tell("Lookout can't post in that channel. Give it View Channel and Send Messages there.")
            s.staff_channel_id = channel.id
        elif arg == "staff_role":
            role = guild.get_role(int(values[0])) if values else None
            if role is None or role.is_default() or role.managed:
                return await tell("Pick your actual mod role, not @everyone or a bot's role.")
            s.staff_role_id = role.id
        elif arg == "reminder" and values:
            s.reminder_minutes = int(values[0])
        elif arg in ("young_roles", "older_roles", "adult_roles"):
            self._set_roles(s, arg, values)
        elif arg == "filter_languages":
            s.filter_languages = ",".join(v for v in values if v in wordlists.NAMES) or "en"
        elif arg == "no_age_role" and values:
            s.no_age_role = values[0]
        elif arg == "age_mode" and values:
            self._set_mode(s, values[0])
        elif arg == "adult_button":
            channel = guild.get_channel(int(values[0])) if values else None
            if channel is None or not channel.is_nsfw():
                return await tell("That channel isn't age-restricted. Turn on **Age-Restricted Channel** in its "
                                  "settings first, so only adults Discord has verified can see it.")
            if not can_post(channel) or not await self._post_adult_button(channel):
                return await tell("Lookout can't post in that channel. Pick a different one.")
            was_on = s.teen_safety
            s.adult_channel_id = channel.id
            if s.teen_safety and not was_on and s.risk_mode == "test":
                s.risk_test_until = time.time() + TEST_DAYS * 86400
        elif arg == "post_panel":
            channel = guild.get_channel(int(values[0])) if values else None
            if not await self._post_panel(channel, can_post):
                return await tell("Lookout can't post in that channel. Pick a different one.")
            s.panel_channel_id = channel.id
        elif arg == "post_tips":
            channel = guild.get_channel(int(values[0])) if values else None
            if not can_post(channel) or not await self._post_tips(channel):
                return await tell("Lookout can't post in that channel. Pick a different one.")
            s.tips_channel_id = channel.id
        elif arg == "trivia_channel":
            channel = guild.get_channel(int(values[0])) if values else None
            if not can_post(channel):
                return await tell("Lookout can't post in that channel. Give it View Channel and Send Messages there.")
            s.trivia_channel_id = channel.id
        else:  # old menu (night hours etc)
            return await show(home(guild, s, note="That setting isn't in Lookout anymore. Here's the panel instead."))
        saved(arg)
        await show(PAGES[PAGE_OF.get(arg, "home")](guild, s))

    # teen by default or teen and adult roles
    @staticmethod
    def _set_mode(s: db.GuildSettings, mode: str) -> None:
        was_on = s.teen_safety
        if mode == "tbd":
            s.no_age_role = "teen"
        elif s.teen_default:
            s.no_age_role = "adult"
        if s.teen_safety and not was_on and s.risk_mode == "test":
            s.risk_test_until = time.time() + TEST_DAYS * 86400

    @staticmethod
    def _set_roles(s: db.GuildSettings, field: str, values: list[str]) -> None:
        if field not in ("young_roles", "older_roles", "adult_roles"):
            return
        was_on = s.teen_safety
        if field == "adult_roles":
            s.adult_roles = ",".join(values) or None
        else:  # old versions split teens into 13-15 and 16-17
            s.young_roles, s.older_roles = ",".join(values) or None, None
        if s.teen_safety and not was_on and s.risk_mode == "test":
            s.risk_test_until = time.time() + TEST_DAYS * 86400  # test week starts now

    @staticmethod
    async def _post_tips(channel) -> bool:
        from cogs.help import tips_message
        try:
            view = tips_message(banner=channel.permissions_for(channel.guild.me).attach_files)
            await channel.send(view=view, files=ui_kit.files(view))
            return True
        except discord.HTTPException:
            return False

    @staticmethod
    async def _post_adult_button(channel) -> bool:
        view = ui_kit.card(ui_kit.titled("brand", "Are You 18+?"),
                           "Press **I'm 18+** so Lookout knows you're an adult in this server.\n"
                           "-# You don't get a role, and nobody can see who pressed it. Pressed it by mistake? "
                           "Press **Undo**.",
                           buttons=[discord.ui.Button(label="I'm 18+", custom_id="lkadult:yes"),
                                    discord.ui.Button(label="Undo", custom_id="lkadult:no")])
        try:
            await channel.send(view=view)
            return True
        except discord.HTTPException:
            return False

    # the meerkat pack as server emojis, skips the ones that are already there
    @staticmethod
    async def _add_emojis(interaction: discord.Interaction, guild: discord.Guild):
        perms = guild.me.guild_permissions
        if not (perms.create_expressions or perms.manage_expressions):
            return await interaction.response.send_message(view=ui_kit.note(
                "Lookout needs the **Create Expressions** permission for this. Turn it on in **Server Settings** > "
                "**Roles** > **Lookout**, then press **Add Emojis** again."), ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        have = {e.name for e in guild.emojis}
        free = guild.emoji_limit - sum(not e.animated for e in guild.emojis)
        added, left = [], 0
        for path in sorted((config.ASSETS / "meerkat").glob("looky_*.png")):
            if path.stem in have:
                continue
            if free <= 0:
                left += 1
                continue
            try:
                emoji = await guild.create_custom_emoji(name=path.stem, image=path.read_bytes(),
                                                       reason=f"Lookout's meerkat emojis, added by {interaction.user}")
            except discord.HTTPException:
                left += 1
                continue
            added.append(str(emoji))
            free -= 1
        if added:
            text = f"**Added {len(added)} meerkat emoji{'' if len(added) == 1 else 's'}.** {' '.join(added)}"
        elif left:
            text = "**The meerkats didn't fit.** Your server is out of emoji slots."
        else:
            text = "**The meerkats are already here.**"
        if added and left:
            text += f"\n-# {left} more didn't fit. Your server is out of emoji slots."
        await interaction.followup.send(view=ui_kit.note(text), ephemeral=True)

    @staticmethod
    async def _post_panel(channel, can_post) -> bool:
        from cogs.reports import panel_view
        if not can_post(channel):
            return False
        try:
            view = panel_view(banner=channel.permissions_for(channel.guild.me).attach_files)
            await channel.send(view=view, files=ui_kit.files(view))
            return True
        except discord.HTTPException:
            return False


async def setup(bot):
    await bot.add_cog(Setup(bot))
