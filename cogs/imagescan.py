import asyncio
import io
import logging
import threading

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


class ImageScan(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._detector = None
        self._lock = threading.Lock()

    # (nsfw score, label, face score)
    def _score(self, data: bytes) -> tuple[float, str, float]:
        with self._lock:
            if self._detector is None:
                from nudenet import NudeDetector
                self._detector = NudeDetector()
            img = Image.open(io.BytesIO(data))
            img.draft("RGB", (1280, 1280))  # way faster for big jpegs
            # seek instead of collecting frames, pillow reuses the same image object
            frames = getattr(img, "n_frames", 1)
            best, label, face = 0.0, "", 0.0
            for i in range(0, frames, max(1, frames // MAX_GIF_FRAMES))[:MAX_GIF_FRAMES]:
                img.seek(i)
                rgb = img.convert("RGB")
                rgb.thumbnail((1280, 1280))
                bgr = np.array(rgb)[:, :, ::-1].copy()
                for d in self._detector.detect(bgr):
                    if d["class"] in NSFW_CLASSES and d["score"] > best:
                        best, label = d["score"], d["class"]
                    elif d["class"] in FACE_CLASSES:
                        face = max(face, d["score"])
            return best, label, face

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        s = db.get_settings(message.guild.id)
        if not s.configured:
            return
        teens = self.bot.get_cog("TeenSafety")
        selfie = bool(s.teen_safety and s.selfie_guard and teens and isinstance(message.author, discord.Member)
                      and teens.is_teen(message.author, s))
        if not (s.image_scan or selfie):
            return
        if getattr(message.channel, "is_nsfw", lambda: False)():
            return  # nsfw channels are fine

        def pictures(attachments):
            return [a for a in attachments
                    if ((a.content_type or "").startswith("image/") or a.filename.lower().endswith(IMAGE_EXTS))
                    and a.size <= config.MAX_IMAGE_MB * 1024 * 1024]
        own = pictures(message.attachments)
        # forwards keep their images in message_snapshots. scan those too, but no selfie tip for them
        forwarded = pictures(a for snap in getattr(message, "message_snapshots", None) or [] for a in snap.attachments)
        images = [(a, True) for a in own] + [(a, False) for a in forwarded if s.image_scan]
        if not images:
            return

        worst, label, face = 0.0, "", 0.0
        for att, mine in images:
            try:
                score, lab, f = await asyncio.to_thread(self._score, await att.read())
            except Exception:
                log.warning("Could not scan %s", att.filename, exc_info=True)
                continue
            if mine:
                face = max(face, f)
            if score > worst:
                worst, label = score, lab
            if worst >= config.NSFW_THRESHOLD:
                break
        if not s.image_scan or worst < config.NSFW_THRESHOLD:
            if selfie and face >= FACE_MIN:
                await teens.teen_photo(message)
            return

        author = message.author
        try:
            await message.delete()
            deleted = True
        except discord.HTTPException:
            deleted = False

        db.log(message.guild.id, author.id, "image_flagged", f"Inappropriate image ({worst:.0%} sure)", message.channel.id)
        if deleted:
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
        if forwarded and not own:
            what += "\n-# It was forwarded."
        if message.content:
            what += f"\n-# Sent with it: {ui_kit.hidden(message.content, 200)}"
        voice = author.voice.channel.id if isinstance(author, discord.Member) and author.voice else None
        case_no = db.next_case_no(message.guild.id)
        view = ui_kit.alert(
            ui_kit.titled("image", ("Image Removed" if deleted else "Inappropriate Image Found") + f" · #{case_no}"),
            f"<t:{int(discord.utils.utcnow().timestamp())}:R>",
            f"**Open** · Unclaimed · {'deleted' if deleted else 'still there'}",
            [("member", "Who", f"{author.mention} · {flags} flag{'' if flags == 1 else 's'} so far"),
             ("report", "What Happened", what), ("cases", "Where", message.channel.mention)],
            buttons=action_buttons(message.guild.id, voice, author.id), thumbnail=author.display_avatar.url,
            footer="The image isn't shown here on purpose. If it could be illegal, report it to Discord at "
                   "https://dis.gd/request")
        alert = await self.bot.alert_staff(message.guild, view)
        if alert:
            db.create_case(message.guild.id, alert.id, alert.channel.id, case_no, "image", message.channel.id,
                           author.id, f"Inappropriate image ({worst:.0%} sure)")


async def setup(bot):
    await bot.add_cog(ImageScan(bot))
