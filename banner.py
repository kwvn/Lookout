import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path(__file__).parent / "fonts"
W, H = 1200, 520
AVATAR = 196
TITLE_FONT = "InterDisplay-SemiBold.ttf"
SMALL_FONT = "Inter-Medium.ttf"
SHADE = (14, 40, 31)  # dark green overlay


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default(size)


def _center_text(draw, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def profile_banner(avatar_bytes: bytes, name: str, username: str, user_id: int, meta: str = "") -> bytes:
    line2 = f"@{username}  ·  {user_id}"
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")

    # blurred avatar as the background
    bg = avatar.resize((W, W)).crop((0, (W - H) // 2, W, (W + H) // 2)).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", (W, H), SHADE), 0.72)
    shade = Image.linear_gradient("L").resize((W, H))   # darker toward the bottom
    bg = Image.composite(Image.new("RGB", (W, H), SHADE), bg, shade.point(lambda p: p * 0.55))

    # avatar
    av = avatar.resize((AVATAR * 2, AVATAR * 2), Image.LANCZOS)
    mask = Image.new("L", av.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, *av.size), fill=255)
    av = av.resize((AVATAR, AVATAR), Image.LANCZOS)
    mask = mask.resize((AVATAR, AVATAR), Image.LANCZOS)
    ax, ay = (W - AVATAR) // 2, 70
    bg.paste(av, (ax, ay), mask)

    d = ImageDraw.Draw(bg)
    title = _font(TITLE_FONT, 58)
    if d.textlength(name[:28], font=title) > W - 120:   # shrink long names to fit
        title = _font(TITLE_FONT, 42)
    _center_text(d, ay + AVATAR + 26, name[:28], title, (243, 239, 230))
    _center_text(d, ay + AVATAR + 104, line2, _font(SMALL_FONT, 25), (196, 212, 204))
    if meta:
        _center_text(d, ay + AVATAR + 148, meta, _font(SMALL_FONT, 20), (138, 166, 154))

    # rounded corners
    corners = Image.new("L", (W, H), 0)
    ImageDraw.Draw(corners).rounded_rectangle((0, 0, W - 1, H - 1), radius=28, fill=255)
    out = Image.new("RGBA", (W, H))
    out.paste(bg, (0, 0), corners)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def ago(dt, now) -> str:
    s = int((now - dt).total_seconds())
    for unit, secs in (("year", 31536000), ("month", 2592000), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= secs:
            n = s // secs
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"
