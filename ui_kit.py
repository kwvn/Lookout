import datetime
import io
import re

import discord
from discord import ui

import chart
import config

# icon emojis, filled in on startup
ICONS: dict[str, str] = {}

# discord help pages
BLOCK_URL = "https://support.discord.com/hc/en-us/articles/217916488"
SAFETY_CENTER_URL = "https://discord.com/safety"

# set on startup. USER_INSTALL means user installs are on in the dev portal (needed for DM reports)
APP_ID: int | None = None
USER_INSTALL = False

# ids from here up are the "staff did X" lines on alerts
STAFF_NOTE_ID = 900

# stuff tidy() never touches
_PROTECT = re.compile(r"(```[\s\S]*?```|</[\w -]+:\d+>|<[^>\s]+>|https?://\S+|`[^`]*`|@[\w.]+)")
_ACRONYMS = re.compile(r"\b(nsfw|vc|dm|id)\b", re.IGNORECASE)
_LEADING_EMOJI = re.compile(r"^[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\s]+")


# capitalize line and sentence starts, and after "label:" and " · "
def tidy(text: str | None) -> str | None:
    if not text:
        return text
    out, start = [], True
    for part in _PROTECT.split(text):
        if not part:
            continue
        if _PROTECT.fullmatch(part):
            out.append(part)
            start = False
            continue
        chars = list(_ACRONYMS.sub(lambda m: m.group(1).upper(), part))
        for i, ch in enumerate(chars):
            if ch == "\n":
                start = True
            elif ch == "(":
                start = False  # keep "(optional)" lowercase like discord does
            elif start and ch.isalnum():
                chars[i] = ch.upper()
                start = False
            elif ch == " " and i:
                j = i - 1
                while j > 0 and chars[j] == "*":
                    j -= 1
                if chars[j] in ".!?:·":
                    start = True
        out.append("".join(chars))
    return "".join(out)


# lookout's meerkat, only for the friendly messages. falls back to a normal icon if it isn't loaded
def mascot(mood: str, fallback: str = "") -> str:
    key = f"looky_{mood}"
    return key if key in ICONS else fallback


def titled(key: str, text: str) -> str:
    icon = ICONS.get(key)
    return f"{icon} {text}" if icon else text


def kv(pairs: list[tuple[str, str | None]]) -> str:
    rows = []
    for label, value in pairs:
        if value:
            rows.append(f"**{label}:**\n{value}" if "\n" in str(value) else f"**{label}:** {value}")
    return "\n".join(rows)


def member_line(member) -> str:
    parts = [f"@{member.name}", f"`{member.id}`"]
    if getattr(member, "joined_at", None):
        parts.append(f"joined <t:{int(member.joined_at.timestamp())}:R>")
    if getattr(member, "created_at", None):
        parts.append(f"account created <t:{int(member.created_at.timestamp())}:R>")
    return " · ".join(parts[:2]) + ("\n" + " · ".join(parts[2:]) if parts[2:] else "")


def _grey(text: str) -> str:
    return "\n".join(line if line.startswith("-# ") or not line.strip() else f"-# {line}"
                     for line in text.split("\n"))


def _style(item):
    if isinstance(item, ui.Button):
        if item.label:
            item.label = tidy(item.label)
        if item.style is not discord.ButtonStyle.link:
            item.style = discord.ButtonStyle.secondary
        item.emoji = None
    elif getattr(item, "placeholder", None):
        item.placeholder = tidy(item.placeholder)
    return item


def _banner_name(title: str) -> str:
    return "banner-" + (re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "lookout") + ".png"


# a subtitle as plain text for the banner. None if it has mentions, links, more lines or is too long
def _plain(text: str) -> str | None:
    text = re.sub(r"<a?:\w+:\d+>", "", text)  # custom emojis
    if "\n" in text or re.search(r"<[@#t/]|\]\(", text):
        return None
    text = re.sub(r"\\([*_~`|>\\])", r"\1", text.replace("**", "").replace("__", ""))
    text = tidy(" ".join(text.removeprefix("-#").split()))
    return text if text and chart.line_fits(text) else None


# banner picture if there's an icon (send files(view) with it), otherwise a # title
# the subtitle goes in the banner when it's plain enough, otherwise under it. line: drawn in the banner instead,
# the subtitle then only shows if the banner can't be drawn
def head(view: ui.LayoutView, title: str, subtitle: str | None = None, icon: str | None = None,
         thumbnail: str | None = None, banner: bool = True, line: str | None = None) -> None:
    if line is None and subtitle and icon and banner:
        line = _plain(subtitle)
    data = chart.banner(icon, title, line) if icon and banner else None
    if data:
        name = _banner_name(title)
        view.add_item(ui.MediaGallery(discord.MediaGalleryItem(
            f"attachment://{name}", description=f"{title} · {line}" if line else title)))
        view.banner = (name, data)
        if subtitle and not line:
            view.add_item(ui.TextDisplay(tidy(_grey(subtitle))))
        return
    text = ui.TextDisplay(tidy(f"# {titled(icon, title) if icon else title}" + (f"\n{_grey(subtitle)}" if subtitle else "")))
    view.add_item(ui.Section(text, accessory=ui.Thumbnail(thumbnail)) if thumbnail else text)


# new File every time, a File can only be sent once
def files(view: ui.LayoutView) -> list[discord.File]:
    banner = getattr(view, "banner", None)
    return [discord.File(io.BytesIO(banner[1]), filename=banner[0])] if banner else []


# title, a box per section, then footer + buttons, then the brand line
# a section is text, an item or (label, body), body can be a list
# None in buttons starts a new row
def card(title: str | None, *sections, subtitle: str | None = None, thumbnail: str | None = None,
         image: str | list[str] | None = None, footer: str | None = None, buttons: list | None = None,
         brand: bool = False, spoiler: bool = False, image_text: str | list[str] | None = None,
         icon: str | None = None, banner: bool = True, line: str | None = None) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    if image:
        images = image if isinstance(image, list) else [image]
        texts = image_text if isinstance(image_text, list) else [image_text] * len(images)
        view.add_item(ui.MediaGallery(*[discord.MediaGalleryItem(url, description=text, spoiler=spoiler)
                                        for url, text in zip(images, texts)]))
    if title:
        head(view, title, subtitle, icon, thumbnail, banner, line)

    for section in sections:
        if not section:
            continue
        box = ui.Container()
        view.add_item(box)
        label, body = section if isinstance(section, tuple) else (None, section)
        tag = f"## {label}" if label else ""
        if isinstance(body, list):
            if tag:
                box.add_item(ui.TextDisplay(tidy(tag)))
            for item in body:
                if isinstance(item, str):
                    box.add_item(ui.TextDisplay(tidy(item)[:4000]))
                elif isinstance(item, ui.Section):
                    if isinstance(item.accessory, ui.Button):
                        _style(item.accessory)
                    box.add_item(item)
                elif isinstance(item, (ui.Button, ui.Select, ui.RoleSelect, ui.ChannelSelect, ui.UserSelect)):
                    box.add_item(ui.ActionRow(_style(item)))
                else:
                    box.add_item(item)
        elif isinstance(body, ui.Item):
            if tag:
                box.add_item(ui.TextDisplay(tidy(tag)))
            box.add_item(body)
        else:
            box.add_item(ui.TextDisplay(tidy((f"{tag}\n" if tag else "") + str(body))[:4000]))

    if buttons:
        box = ui.Container()
        if footer:
            box.add_item(ui.TextDisplay(tidy(_grey(footer))))
        _add_buttons(box, buttons)
        view.add_item(box)
    elif footer:
        view.add_item(ui.TextDisplay(tidy(_grey(footer))))
    if brand:
        year = datetime.date.today().year
        mark = f"{ICONS['brand']} " if "brand" in ICONS else ""
        view.add_item(ui.TextDisplay(f"-# {mark}Lookout {year} · [Support Server](<{config.SUPPORT_URL}>)"))
    return view


# a row of buttons outside any box
def button_row(*buttons: ui.Button) -> ui.ActionRow:
    return ui.ActionRow(*(_style(b) for b in buttons))


# 5 per row, menus get their own row
def _add_buttons(box: ui.Container, buttons: list) -> None:
    row = []
    for item in buttons:
        if item is not None and isinstance(item, ui.Button):
            row.append(_style(item))
            if len(row) < 5:
                continue
        if row:
            box.add_item(ui.ActionRow(*row))
            row = []
        if item is not None and not isinstance(item, ui.Button):
            box.add_item(ui.ActionRow(_style(item)))
    if row:
        box.add_item(ui.ActionRow(*row))


def boxes(title: str, subtitle: str | None, items: list, image: str | None = None,
          image_text: str | None = None, icon: str | None = None) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    if image:
        view.add_item(ui.MediaGallery(discord.MediaGalleryItem(image, description=image_text)))
    head(view, title, subtitle, icon)
    for icon, heading, text, buttons in items:
        box = ui.Container(ui.TextDisplay(tidy(f"## {heading}\n{text}")[:4000]))     # icons only go in titles
        if buttons:
            _add_buttons(box, buttons)
        view.add_item(box)
    return view


# title + status line, a box per part, buttons in the last box
def alert(title: str, subtitle: str, status: str, parts: list, buttons: list | None = None,
          thumbnail: str | None = None, footer: str | None = None, icon: str | None = None) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    if icon:
        head(view, title, f"{subtitle}\n-# {status}", icon)
    else:
        view.add_item(ui.TextDisplay(tidy(f"# {title}\n{_grey(subtitle)}\n-# {status}")))
    for icon, heading, text in parts:
        if not text:
            continue
        body = ui.TextDisplay(tidy(f"## {heading}\n{text}")[:4000])     # icons only go in titles
        view.add_item(ui.Container(ui.Section(body, accessory=ui.Thumbnail(thumbnail)) if thumbnail else body))
        thumbnail = None
    if footer or buttons:
        box = ui.Container()
        if footer:
            box.add_item(ui.TextDisplay(tidy(_grey(footer))))
        _add_buttons(box, buttons or [])
        view.add_item(box)
    return view


# the last box (older alerts only have one)
def action_box(view: ui.LayoutView) -> ui.Container:
    return [i for i in view.children if isinstance(i, ui.Container)][-1]


def set_status(view: ui.LayoutView, state: str | None = None, who: str | None = None) -> None:
    head = view.children[0] if view.children else None
    if not isinstance(head, ui.TextDisplay):
        return
    lines = head.content.split("\n")
    if not lines[-1].startswith("-# **"):
        return
    parts = lines[-1][3:].split(" · ")
    if state:
        parts[0] = f"**{state}**"
    if who:
        parts[1:2] = [who]
    lines[-1] = "-# " + " · ".join(parts)
    head.content = tidy("\n".join(lines))


def block_button() -> ui.Button:
    return ui.Button(label="How to Block Someone", style=discord.ButtonStyle.link, url=BLOCK_URL)


def add_app_url() -> str | None:
    if not (USER_INSTALL and APP_ID):
        return None
    return f"https://discord.com/oauth2/authorize?client_id={APP_ID}&integration_type=1&scope=applications.commands"


def add_app_button() -> ui.Button | None:
    url = add_app_url()
    return ui.Button(label="Add to My Apps", style=discord.ButtonStyle.link, url=url) if url else None


# spoilered one-line code copy, nothing inside can break out
def hidden(text: str | None, limit: int = 1000) -> str:
    flat = " / ".join(line.strip() for line in (text or "").splitlines() if line.strip()) or "(no text)"
    flat = re.sub(r"\|(?=\|)", "| ", flat.replace("`", "'"))[:limit]
    return f"||`{flat}`||"


def note(text: str, buttons: list | None = None) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(ui.TextDisplay(tidy(_LEADING_EMOJI.sub("", text))[:4000])))
    if buttons:
        box = ui.Container()
        _add_buttons(box, buttons)
        view.add_item(box)
    return view


def staff_notes(message: discord.Message) -> list[ui.TextDisplay]:
    try:
        old = ui.LayoutView.from_message(message, timeout=None)
    except Exception:
        return []
    return [ui.TextDisplay(i.content, id=i.id) for i in old.walk_children()
            if isinstance(i, ui.TextDisplay) and (i.id or 0) >= STAFF_NOTE_ID]


def insert_above_actions(box: ui.Container, items: list[ui.Item]) -> None:
    kids = list(box.children)
    idx = next((i for i, k in enumerate(kids) if isinstance(k, ui.ActionRow)), len(kids))
    if 0 < idx < len(kids) and isinstance(kids[idx - 1], ui.Separator):
        idx -= 1
    box.clear_items()
    for k in kids[:idx] + items + kids[idx:]:
        box.add_item(k)


def remove_actions(box: ui.Container) -> None:
    kids = list(box.children)
    first = next((i for i, k in enumerate(kids) if isinstance(k, ui.ActionRow)), None)
    if first is None:
        return
    for i, item in enumerate(kids):
        if isinstance(item, ui.ActionRow) or (i == first - 1 and isinstance(item, ui.Separator)):
            box.remove_item(item)


# the "Take action…" menu on alerts. each option's value is the custom id of the button it replaced,
# so the cogs handle a pick the same way they handled the button
ACTION_MENU = "lkact"


def custom_id(interaction: discord.Interaction) -> str:
    data = interaction.data or {}
    cid = data.get("custom_id", "")
    return (data.get("values") or [""])[0] if cid == ACTION_MENU else cid


def add_action(items: list, label: str, description: str, cid: str) -> None:
    menu = next((i for i in items if isinstance(i, ui.Select) and i.custom_id == ACTION_MENU), None)
    if menu is None:
        menu = ui.Select(custom_id=ACTION_MENU, placeholder="Take action…")
        items += [None, menu]
    menu.add_option(label=label, description=description, value=cid)


# only ever ping that one role
def ping_for(role_id: int | None) -> discord.AllowedMentions:
    return discord.AllowedMentions(everyone=False, users=False,
                                   roles=[discord.Object(role_id)] if role_id else False)
