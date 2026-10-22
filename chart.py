import io
import time
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).parent
BOLD = "InterDisplay-SemiBold.ttf"      # numbers and titles
REGULAR = "Inter-Medium.ttf"            # everything else

S = 2  # draw at 2x and scale down, smoother edges
W, PAD, GAP = 1200, 48, 20
INNER = W - 2 * PAD
ROW = 54  # list row height

# colors
BG = (18, 56, 43)
PANEL = (25, 70, 55)
TRACK = (37, 88, 70)
GHOST = (70, 120, 101)       # the "week before" bars
ACCENT = (243, 239, 230)     # this week's bars
WHITE = (243, 239, 230)
GREY = (170, 196, 184)
DIM = (112, 150, 135)


@lru_cache(maxsize=64)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(BASE / "fonts" / name), size)
    except OSError:
        return ImageFont.load_default(size)


class _Canvas:
    def __init__(self, height: int):
        self.h = height
        self.img = Image.new("RGB", (W * S, height * S), BG)
        self.d = ImageDraw.Draw(self.img)

    def box(self, x, y, w, h, fill, r=0):
        if w > 0 and h > 0:
            self.d.rounded_rectangle((x * S, y * S, (x + w) * S, (y + h) * S), radius=min(r, w / 2, h / 2) * S, fill=fill)

    def width(self, text, font, size) -> float:
        return self.d.textlength(text, font=_font(font, size * S)) / S

    # fit: shrink the font until it fits this width
    def text(self, x, y, text, font, size, fill, anchor="la", fit=None):
        while fit and size > 12 and self.width(text, font, size) > fit:
            size -= 1
        self.d.text((x * S, y * S), text, font=_font(font, size * S), fill=fill, anchor=anchor)

    def png(self) -> bytes:
        small = self.img.resize((W, self.h), Image.LANCZOS)
        corners = Image.new("L", (W * S, self.h * S), 0)
        ImageDraw.Draw(corners).rounded_rectangle((0, 0, W * S - 1, self.h * S - 1), radius=28 * S, fill=255)
        out = Image.new("RGBA", (W, self.h))
        out.paste(small, (0, 0), corners.resize((W, self.h), Image.LANCZOS))
        buf = io.BytesIO()
        out.save(buf, "PNG", optimize=True)
        return buf.getvalue()


@lru_cache(maxsize=2048)
def _has_glyph(char: str, font: str) -> bool:
    f = _font(font, 40)
    return bytes(f.getmask(char)) != bytes(f.getmask("\uffff"))


def can_draw(text: str, font: str = REGULAR) -> bool:
    return all(ch.isspace() or _has_glyph(ch, font) for ch in text)


def _header(c: _Canvas, heading: str, title: str):
    c.text(PAD + 2, PAD + 6, heading, REGULAR, 24, GREY)
    c.text(PAD, PAD + 42, title, BOLD, 66, WHITE, fit=INNER - 190)
    mark = BASE / "assets" / "eye.png"  # logo, top right
    if mark.exists():
        eye = Image.open(mark).convert("RGBA")
        w = 132 * S
        eye = eye.resize((w, round(eye.height * w / eye.width)), Image.LANCZOS)
        c.img.paste(eye, ((W - PAD - 6) * S - w, (PAD + 62) * S - eye.height // 2), eye)


def _duration(seconds: float) -> str:
    minutes = max(1, int(seconds // 60))
    if minutes >= 1440:
        return f"{minutes // 1440}d {minutes % 1440 // 60}h"
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m"


def _tile(c: _Canvas, x, y, w, h, label, number, note):
    c.box(x, y, w, h, PANEL, r=22)
    c.text(x + 28, y + 26, label, REGULAR, 22, GREY)
    c.text(x + 28, y + 60, number, BOLD, 70, WHITE, fit=w - 56)
    # skip the note if it doesn't fit next to the number
    if note and min(c.width(number, BOLD, 70), w - 56) + c.width(note, REGULAR, 23) + 24 <= w - 56:
        c.text(x + w - 28, y + h - 30, note, REGULAR, 23, DIM, anchor="rs")


def _bars(c: _Canvas, x, y, w, h, days: list[str], now: list[int], before: list[int], legend: str):
    c.box(x, y, w, h, PANEL, r=22)
    c.text(x + 28, y + 26, "REPORTS PER DAY", REGULAR, 22, GREY)
    # legend, right side
    lx = x + w - 28
    for label, color in (("WEEK BEFORE", GHOST), (legend, ACCENT)):
        lx -= c.width(label, REGULAR, 19)
        c.text(lx, y + 28, label, REGULAR, 19, GREY)
        lx -= 26
        c.box(lx, y + 30, 16, 16, color, r=4)
        lx -= 28

    top, bottom = y + 112, y + h - 66  # chart area
    c.box(x + 28, bottom, w - 56, 2, TRACK)
    if not any(now) and not any(before):
        c.text(x + w / 2, (top + bottom) / 2 - 16, "No reports this week", REGULAR, 26, DIM, anchor="ma")
    peak = max(max(now), max(before), 4)  # so a quiet week doesn't get huge bars
    slot = (w - 56) / len(days)
    for i, day in enumerate(days):
        cx = x + 28 + slot * i + slot / 2
        old_h = (bottom - top) * before[i] / peak
        new_h = (bottom - top) * now[i] / peak
        if before[i]:
            c.box(cx - 50, bottom - max(old_h, 6), 40, max(old_h, 6), GHOST, r=8)
        if now[i]:
            c.box(cx - 4, bottom - max(new_h, 6), 54, max(new_h, 6), ACCENT, r=10)
            c.text(cx + 23, bottom - max(new_h, 6) - 38, str(now[i]), BOLD, 27, WHITE, anchor="ma")
        c.text(cx, bottom + 20, day.upper(), REGULAR, 22, GREY if now[i] else DIM, anchor="ma")


def _types(c: _Canvas, x, y, w, h, types: list[tuple[str, int]]):
    c.box(x, y, w, h, PANEL, r=22)
    c.text(x + 28, y + 26, "WHAT WAS REPORTED", REGULAR, 22, GREY)
    if not types:
        c.text(x + w / 2, y + h / 2, "Nothing reported", REGULAR, 24, DIM, anchor="ma")
        return
    if len(types) > 5:  # only 5 fit, the rest goes into Other
        named = [(label, n) for label, n in types if label != "Other"]
        rest = sum(n for _, n in types) - sum(n for _, n in named[:4])
        types = named[:4] + [("Other", rest)]
    peak = max(n for _, n in types)
    for i, (label, n) in enumerate(types):
        ry = y + 80 + i * ROW
        c.text(x + 28, ry - 2, label, REGULAR, 24, WHITE, fit=w - 130)
        c.text(x + w - 28, ry - 4, str(n), BOLD, 27, WHITE, anchor="ra")
        c.box(x + 28, ry + 36, w - 56, 6, TRACK, r=3)
        c.box(x + 28, ry + 36, max((w - 56) * n / peak, 10), 6, ACCENT, r=3)


def _actions(c: _Canvas, x, y, w, h, rows: list[tuple[str, int]]):
    c.box(x, y, w, h, PANEL, r=22)
    c.text(x + 28, y + 26, "WHAT THE MODS DID", REGULAR, 22, GREY)
    for i, (label, n) in enumerate(rows):
        ry = y + 80 + i * ROW
        if i:
            c.box(x + 28, ry - 14, w - 56, 2, TRACK)
        c.text(x + 28, ry - 2, label, REGULAR, 24, WHITE if n else DIM)
        c.text(x + w - 28, ry - 4, str(n), BOLD, 27, WHITE if n else DIM, anchor="ra")


def summary_image(stats: dict, before: dict, heading: str, dates: str, days: list[str],
                  legend: str = "THIS WEEK") -> bytes:
    tile_h, chart_h, lower_h = 160, 400, 80 + 5 * ROW + 6
    tiles_y = 196
    chart_y = tiles_y + 2 * tile_h + 2 * GAP
    lower_y = chart_y + chart_h + GAP
    c = _Canvas(lower_y + lower_h + PAD)

    _header(c, heading, dates)

    # the four big numbers
    half = (INNER - GAP) / 2
    reports = "Same as the week before" if stats["reports"] == before["reports"] else f"Week before: {before['reports']}"
    faster = f"Week before: {_duration(before['avg_seconds'])}" if before["avg_seconds"] else "On average"
    tiles = [
        ("REPORTS", str(stats["reports"]), reports if stats["reports"] or before["reports"] else ""),
        ("CASES RESOLVED", str(stats["resolved"]), f"{stats['opened']} opened"),
        ("TIME TO RESOLVE", _duration(stats["avg_seconds"]) if stats["avg_seconds"] else "-",
         faster if stats["avg_seconds"] else "No cases resolved"),
        ("STILL OPEN", str(stats["still_open"]), "Waiting for the mods" if stats["still_open"] else "All clear"),
    ]
    for i, (label, number, note) in enumerate(tiles):
        _tile(c, PAD + (i % 2) * (half + GAP), tiles_y + (i // 2) * (tile_h + GAP), half, tile_h, label, number, note)

    _bars(c, PAD, chart_y, INNER, chart_h, days, stats["daily"], before["daily"], legend)
    _types(c, PAD, lower_y, half, lower_h, stats["types"])
    _actions(c, PAD + half + GAP, lower_y, half, lower_h, [
        ("Warnings", stats["warnings"]), ("Timeouts", stats["timeouts"]), ("Disconnects", stats["disconnects"]),
        ("Images removed", stats["images"]),
    ])
    return c.png()


# mod stats

STAFF_ROW = 76


def _avatar(c: _Canvas, x, y, size, data: bytes | None, name: str):
    box = (int(x * S), int(y * S), int((x + size) * S), int((y + size) * S))
    try:
        if not data:
            raise ValueError
        img = Image.open(io.BytesIO(data)).convert("RGB").resize((size * S, size * S), Image.LANCZOS)
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, img.width - 1, img.height - 1), fill=255)
        c.img.paste(img, box[:2], mask)
    except Exception:
        c.d.ellipse(box, fill=TRACK)
        letter = next((ch for ch in name if ch.isalnum() and _has_glyph(ch, BOLD)), "?").upper()
        c.text(x + size / 2, y + size / 2, letter, BOLD, 24, GREY, anchor="mm")


def staff_image(rows: list[dict], totals: dict, period: str, heading: str = "MOD STATS") -> bytes:
    rows = rows[:10]
    tiles_y, tile_h = 196, 160
    table_y = tiles_y + tile_h + GAP
    table_h = 96 + max(len(rows), 1) * STAFF_ROW + 14
    c = _Canvas(table_y + table_h + PAD)
    _header(c, heading, period)

    third = (INNER - 2 * GAP) / 3
    tiles = [
        ("CASES RESOLVED", str(totals["resolved"]), f"{totals['opened']} opened"),
        ("TIME TO CLAIM", _duration(totals["claim_avg"]) if totals["claim_avg"] else "-",
         "On average" if totals["claim_avg"] else "No claims yet"),
        ("TIME TO RESOLVE", _duration(totals["resolve_avg"]) if totals["resolve_avg"] else "-",
         "On average" if totals["resolve_avg"] else "No cases resolved"),
    ]
    for i, (label, number, note) in enumerate(tiles):
        _tile(c, PAD + i * (third + GAP), tiles_y, third, tile_h, label, number, note)

    # the table
    x, w = PAD, INNER
    c.box(x, table_y, w, table_h, PANEL, r=22)
    name_x, bar_x, bar_w = x + 136, x + 470, 250
    time_x, actions_x = x + w - 190, x + w - 28
    for label, lx, anchor in (("MOD", x + 28, "la"), ("CASES", bar_x, "la"),
                              ("TIME TO RESOLVE", time_x, "ra"), ("ACTIONS", actions_x, "ra")):
        c.text(lx, table_y + 28, label, REGULAR, 20, GREY, anchor=anchor)
    c.box(x + 28, table_y + 70, w - 56, 2, TRACK)
    if not rows:
        c.text(x + w / 2, table_y + 96 + STAFF_ROW / 2 - 16, "No mod activity in this time", REGULAR, 24, DIM, anchor="ma")
    peak = max([r["cases"] for r in rows] + [1])
    for i, r in enumerate(rows):
        ry = table_y + 86 + i * STAFF_ROW
        mid = ry + STAFF_ROW / 2
        if i:
            c.box(x + 28, ry, w - 56, 2, TRACK)
        c.text(x + 28, mid, str(i + 1), BOLD, 25, ACCENT if i == 0 else DIM, anchor="lm")
        _avatar(c, x + 70, mid - 26, 52, r["avatar"], r["name"])
        c.text(name_x, mid, r["name"], REGULAR, 26, WHITE, anchor="lm", fit=bar_x - name_x - 24)
        c.box(bar_x, mid - 7, bar_w, 14, TRACK, r=7)
        if r["cases"]:
            c.box(bar_x, mid - 7, max(bar_w * r["cases"] / peak, 14), 14, ACCENT, r=7)
        c.text(bar_x + bar_w + 18, mid, str(r["cases"]), BOLD, 27, WHITE if r["cases"] else DIM, anchor="lm")
        c.text(time_x, mid, _duration(r["avg_seconds"]) if r["avg_seconds"] else "-", REGULAR, 24,
               WHITE if r["avg_seconds"] else DIM, anchor="rm")
        c.text(actions_x, mid, str(r["actions"]), BOLD, 27, WHITE if r["actions"] else DIM, anchor="rm")
    return c.png()


# welcome banner

def welcome_banner() -> bytes:
    c = _Canvas(400)
    c.text(PAD + 2, PAD + 8, "LOOKOUT", REGULAR, 26, GREY)
    c.text(PAD, PAD + 50, "Making Discord", BOLD, 78, WHITE)
    c.text(PAD, PAD + 140, "safer for teens", BOLD, 78, WHITE)
    for i, word in enumerate(["Message filter", "Contact filter", "Private reports"]):
        x = PAD + i * 250
        c.box(x, 300, 236, 52, PANEL, r=26)
        c.text(x + 118, 312, word, REGULAR, 24, WHITE, anchor="ma")
    mark = BASE / "assets" / "eye.png"
    if mark.exists():
        eye = Image.open(mark).convert("RGBA")
        w = 300 * S
        eye = eye.resize((w, round(eye.height * w / eye.width)), Image.LANCZOS)
        c.img.paste(eye, ((W - PAD - 20) * S - w, 170 * S - eye.height // 2), eye)
    return c.png()


# command banners

MINT = (127, 184, 154)  # the LOOKOUT label
# which looky goes on which banner, by icon. anything else gets the binoculars
LOOKY = {"settings": "code", "help": "think", "report": "knight", "warning": "thumbsup", "brand": "popcorn",
         "cases": "noted"}


# just the light part of an icon, without the tile behind it
def _glyph(path: Path, size: int) -> Image.Image:
    src = Image.open(path).convert("RGBA").resize((size * S, size * S), Image.LANCZOS)
    light = src.convert("L").point(lambda v: max(0, min(255, (v - 150) * 3)))
    out = Image.new("RGBA", src.size, WHITE + (255,))
    out.putalpha(Image.composite(light, Image.new("L", src.size, 0), src.getchannel("A")))
    return out


# icon, LOOKOUT, the title, an optional line under it (like the server and what needs fixing), looky on the right
# None if the icon is missing or the font can't draw the title
@lru_cache(maxsize=64)
def banner(icon: str, title: str, line: str | None = None) -> bytes | None:
    path = BASE / "assets" / f"{icon}.png"
    if not path.exists() or not can_draw(title, BOLD):
        return None
    if line:  # drop what the font can't draw, like emojis in a server name
        line = " ".join("".join(ch for ch in line if ch.isspace() or _has_glyph(ch, REGULAR)).split()).strip(" ·")
    c = _Canvas(180)
    glyph = _glyph(path, 115)
    c.img.paste(glyph, (38 * S, 32 * S), glyph)
    looky = BASE / "assets" / "meerkat" / f"looky_{LOOKY.get(icon, 'watching')}.png"
    if looky.exists():  # peeking up from the bottom edge
        pic = Image.open(looky).convert("RGBA").resize((168 * S, 168 * S), Image.LANCZOS)
        c.img.paste(pic, ((W - 204) * S, 26 * S), pic)
    x, room = 182, W - 182 - 230
    top = 26 if line else 44
    for ch in "LOOKOUT":
        c.text(x, top, ch, BOLD, 23, MINT)
        x += c.width(ch, BOLD, 23) + 3.5
    c.text(182, top + 27, title, BOLD, 55, WHITE, fit=room)
    if line:
        c.text(182, 120, line, REGULAR, 32, GREY, fit=room)
    return c.png()


# /totals

REVIEW_USERS = 10_000  # message content intent gets reviewed at this many users


def _minutes(m: float | None) -> str:
    if m is None:
        return "None yet"
    m = max(1, round(m))
    return f"{m} min" if m < 60 else f"{m // 60}h {m % 60}m"


def totals(servers: int, users: int, t: dict[str, int], claim_min: float | None) -> bytes:
    c = _Canvas(640)
    since = time.localtime(t.get("since") or time.time())
    c.text(PAD + 2, PAD + 6, "LOOKOUT", REGULAR, 24, GREY)
    c.text(PAD, PAD + 40, "Totals", BOLD, 64, WHITE)
    c.text(W - PAD, PAD + 70, f"Since {time.strftime('%B', since)} {since.tm_mday}, {since.tm_year}", REGULAR, 24,
           GREY, anchor="ra")
    alerts, wrong = t.get("filter_alerts", 0), t.get("false_alarms", 0)
    tiles = [(f"{servers:,}", "server" if servers == 1 else "servers"),
             (f"{t.get('removed', 0):,}", "messages to teens removed"),
             (f"{t.get('contact', 0):,}", "contact info removed"),
             (f"{t.get('handled', 0):,}", "cases handled"),
             (f"{wrong / alerts:.1%}" if alerts else "None yet",
              f"False Alarms ({wrong:,} of {alerts:,} alerts)" if alerts else "False Alarms"),
             (_minutes(claim_min), "average time to claim a case")]
    tw, th = (INNER - 2 * GAP) / 3, 132
    for i, (number, label) in enumerate(tiles):
        x, y = PAD + (i % 3) * (tw + GAP), 170 + (i // 3) * (th + GAP)
        c.box(x, y, tw, th, PANEL, r=18)
        c.text(x + 28, y + 24, number, BOLD, 52, WHITE, fit=tw - 56)
        c.text(x + 28, y + 90, label, REGULAR, 22, GREY, fit=tw - 56)
    y = 170 + 2 * (th + GAP) + 14
    c.text(PAD + 2, y, "Users · Discord reviews Message Content at 10,000", REGULAR, 22, GREY)
    c.text(W - PAD, y, f"{users:,} of {REVIEW_USERS:,}" if users < REVIEW_USERS else f"{users:,}, past {REVIEW_USERS:,}",
           BOLD, 22, WHITE, anchor="ra")
    c.box(PAD, y + 40, INNER, 14, TRACK, r=7)
    c.box(PAD, y + 40, max(INNER * min(users / REVIEW_USERS, 1), 14), 14, ACCENT, r=7)
    c.text(PAD + 2, y + 74, "Counts only. No names, IDs or messages are kept for these.", REGULAR, 20, DIM)
    return c.png()
