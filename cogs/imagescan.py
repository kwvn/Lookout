import asyncio
import io
import logging
import threading
from functools import partial
from urllib.parse import urlparse

import aiohttp
import discord
import numpy as np
from discord.ext import commands
from PIL import Image

import config
import db
import ui_kit
from cogs.staff import action_buttons

log = logging.getLogger("lookout.images")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
NSFW_CLASSES = {"FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
                "ANUS_EXPOSED", "BUTTOCKS_EXPOSED"}
FACE_CLASSES = {"FACE_FEMALE", "FACE_MALE"}
FACE_MIN = 0.5
MAX_GIF_FRAMES = 6
MAX_LINKS = 4  # linked images checked per message
GIF_PICKER = ("tenor.com", "giphy.com")  # discord's gif picker, already filtered on their side
TAKE_IT_DOWN = "https://takeitdown.ncmec.org"


class ImageScan(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._detector = None
        self._lock = threading.Lock()

    # (nsfw score, face score)
    def _score(self, data: bytes) -> tuple[float, float]:
        with self._lock:
            if self._detector is None:
                from nudenet import NudeDetector
                self._detector = NudeDetector()
            img = Image.open(io.BytesIO(data))
            img.draft("RGB", (1280, 1280))  # way faster for big jpegs
            # seek instead of collecting frames, pillow reuses the same image object
            frames = getattr(img, "n_frames", 1)
            best, face = 0.0, 0.0
            for i in range(0, frames, max(1, frames // MAX_GIF_FRAMES))[:MAX_GIF_FRAMES]:
                img.seek(i)
                rgb = img.convert("RGB")
                rgb.thumbnail((1280, 1280))
                bgr = np.array(rgb)[:, :, ::-1].copy()
                for d in self._detector.detect(bgr):
                    if d["class"] in NSFW_CLASSES:
                        best = max(best, d["score"])
                    elif d["class"] in FACE_CLASSES:
                        face = max(face, d["score"])
            return best, face

    async def _fetch(self, url: str) -> bytes:
        limit = config.MAX_IMAGE_MB * 1024 * 1024
        data = b""
        async with self.bot.web.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            async for chunk in r.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > limit:
                    raise ValueError("too big")
        return data

    # a pasted image link shows up as a preview. goes through discord's media proxy, not the site itself
    @staticmethod
    def _linked(message: discord.Message) -> list[str]:
        urls = []
        for embed in message.embeds:
            if embed.type not in ("image", "gifv") or (urlparse(embed.url or "").hostname or "").endswith(GIF_PICKER):
                continue
            for media in (embed.image, embed.thumbnail):
                url = getattr(media, "proxy_url", None) or getattr(media, "url", None)
                if url and url not in urls:
                    urls.append(url)
        return urls[:MAX_LINKS]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return

        def pictures(attachments):
            return [a for a in attachments
                    if ((a.content_type or "").startswith("image/") or a.filename.lower().endswith(IMAGE_EXTS))
                    and a.size <= config.MAX_IMAGE_MB * 1024 * 1024]
        own = pictures(message.attachments)
        # forwards keep their images in message_snapshots. scan those too, but no selfie tip for them
        forwarded = pictures(a for snap in getattr(message, "message_snapshots", None) or [] for a in snap.attachments)
        # (name, read, posted by them)
        sources = [(a.filename, a.read, True) for a in own] + [(a.filename, a.read, False) for a in forwarded]
        sources += [(st.name, st.read, False) for st in message.stickers
                    if st.format != discord.StickerFormatType.lottie]
        sources += [(url, partial(self._fetch, url), False) for url in self._linked(message)]
        if sources:
            await self._scan(message, sources, forwarded=bool(forwarded) and not own)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # discord adds the link preview a moment later, as an edit
        if after.author.bot or after.guild is None:
            return
        seen = set(self._linked(before))
        new = [url for url in self._linked(after) if url not in seen]
        if new:
            await self._scan(after, [(url, partial(self._fetch, url), False) for url in new])

    async def _scan(self, message: discord.Message, sources: list, forwarded: bool = False) -> None:
        s = db.get_settings(message.guild.id)
        if not s.configured or getattr(message.channel, "is_nsfw", lambda: False)():
            return  # nsfw channels are fine
        author = message.author
        teens = self.bot.get_cog("TeenSafety")
        teen = bool(s.teen_safety and teens and isinstance(author, discord.Member) and teens.is_teen(author, s))
        selfie = teen and s.selfie_guard
        if not s.image_scan:  # only the selfie tip is on, so only their own uploads matter
            sources = [src for src in sources if src[2]] if selfie else []
        if not sources:
            return

        worst, face = 0.0, 0.0
        for name, read, mine in sources:
            try:
                score, f = await asyncio.to_thread(self._score, await read())
            except Exception as e:
                log.warning("Could not scan %s: %s", name[:120], e)
                continue
            if mine:
                face = max(face, f)
            worst = max(worst, score)
            if worst >= config.NSFW_THRESHOLD:
                break
        if not s.image_scan or worst < config.NSFW_THRESHOLD:
            if selfie and face >= FACE_MIN:
                await teens.teen_photo(message)
            return

        try:
            await message.delete()
            deleted = True
        except discord.HTTPException:
            deleted = False

        db.log(message.guild.id, author.id, "image_flagged", f"Inappropriate image ({worst:.0%} sure)", message.channel.id)
        if deleted and teen:  # no callout in chat for a teen, a private message instead
            await self._support_dm(message)
        elif deleted:
            try:
                await message.channel.send(view=ui_kit.note(f"{author.mention}, your image was removed by Lookout's "
                                                            "image filter."),
                                           delete_after=10, allowed_mentions=discord.AllowedMentions(users=[author]))
            except discord.HTTPException:
                pass

        flags = db.count_flags(message.guild.id, author.id)
        what = (f"Inappropriate image ({worst:.0%} sure), removed" if deleted
                else f"Inappropriate image ({worst:.0%} sure). **Not removed.** Lookout needs Manage Messages · "
                     f"[Jump to It]({message.jump_url})")
        if forwarded:
            what += "\n-# It was forwarded."
        if teen:
            what += ("\n**Posted by a teen.** It may be a picture of themselves. Don't save or share it, not even to "
                     "report it. Talk to them privately and report it to Discord.")
        if message.content:
            what += f"\n-# Sent with it: {ui_kit.hidden(message.content, 200)}"
        voice = author.voice.channel.id if isinstance(author, discord.Member) and author.voice else None
        case_no = db.next_case_no(message.guild.id)
        ping = f" · <@&{s.staff_role_id}>" if teen and s.staff_role_id else ""
        view = ui_kit.alert(
            ui_kit.titled("image", ("Image Removed" if deleted else "Inappropriate Image Found") + f" · #{case_no}"),
            f"<t:{int(discord.utils.utcnow().timestamp())}:R>{ping}",
            f"**{'Urgent' if teen else 'Open'}** · Unclaimed · {'deleted' if deleted else 'still there'}",
            [("member", "Who", f"{author.mention} · {flags} flag{'' if flags == 1 else 's'} so far"),
             ("report", "What Happened", what), ("cases", "Where", message.channel.mention)],
            buttons=action_buttons(message.guild.id, voice, author.id, voice=bool(voice)),
            thumbnail=author.display_avatar.url,
            footer=("If someone pressured them into it, it can be sextortion. Report it to Discord at "
                    "https://dis.gd/request. Take It Down can help stop it spreading." if teen else
                    "The image isn't shown here on purpose. If it could be illegal, report it to Discord at "
                    "https://dis.gd/request"))
        alert = await self.bot.alert_staff(message.guild, view)
        if alert:
            db.create_case(message.guild.id, alert.id, alert.channel.id, case_no, "image", message.channel.id,
                           author.id, f"Inappropriate image ({worst:.0%} sure)", urgent=teen)

    # a teen's picture got removed. kind, no blame, and help if someone pushed them into it
    @staticmethod
    async def _support_dm(message: discord.Message) -> None:
        view = ui_kit.card(
            ui_kit.titled("warning", "Your Picture Was Removed"),
            f"Lookout removed a picture you posted in {message.channel.mention}. Nobody in the chat was told why. The "
            "mods know, so they can help.",
            ("You're Not in Trouble", "If someone asked you for it, offered you something for it, or is threatening "
                                      "you, that's on them, not you. Talking to an adult you trust can help."),
            ("If It's a Picture of You", "**Take It Down** can help get copies removed from sites that use it. The "
                                         "picture never leaves your phone."),
            subtitle=f"From {discord.utils.escape_markdown(message.guild.name)}",
            buttons=[discord.ui.Button(label="Take It Down", style=discord.ButtonStyle.link, url=TAKE_IT_DOWN),
                     discord.ui.Button(label="Someone's Threatening Me", custom_id="lkhelp:threat")])
        try:
            await message.author.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass    # DMs closed


async def setup(bot):
    await bot.add_cog(ImageScan(bot))
