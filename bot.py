import asyncio
import hashlib
import io
import json
import logging
import math
import re

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import chart
import config
import db
import ui_kit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lookout")

EXTENSIONS = ["cogs.setup", "cogs.help", "cogs.demo", "cogs.staff", "cogs.cases", "cogs.cleanup",
              "cogs.reports", "cogs.reminder", "cogs.appeals", "cogs.summary", "cogs.imagescan", "cogs.teensafety",
              "cogs.totals", "cogs.trivia"]
# rotates every few minutes
STATUSES = ["Always on watch", "On watch · /help", "Keeping teens safe", "Guarding {servers}"]
STATUS_MINUTES = 5

ICON_EMOJIS = {"settings": "lookout_settings", "cases": "lookout_cases",
               "report": "lookout_report", "image": "lookout_image", "brand": "lookout",
               "help": "lookout_help", "voice": "lookout_voice", "staff": "lookout_staff",
               "summary": "lookout_summary", "warning": "lookout_warning", "member": "lookout_member"}
OLD_ICON_NAMES = {"brand": "lookout_mark"}  # old emoji names


# autosharded so we don't have to think about it at 2500 servers
class LookoutBot(commands.AutoShardedBot):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        intents.guild_messages = True
        intents.message_content = True  # needed for the filters and the image scan
        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            member_cache_flags=discord.MemberCacheFlags.from_intents(intents),
            activity=discord.CustomActivity(name=STATUSES[0]),
        )
        self.command_ids: dict[str, int] = {}  # name: id, for command mentions
        self._status_index = 0
        self.web: aiohttp.ClientSession | None = None  # heartbeat + top.gg

    async def setup_hook(self):
        self.web = aiohttp.ClientSession()
        ui_kit.APP_ID = self.application_id
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
            except Exception as e:
                # one broken cog shouldn't take the whole bot down
                log.error("Feature %s is OFF: %s", ext, e.__cause__ or e)
        self._format_commands()
        synced = await self._sync("commands")
        self.command_ids = {cmd.name: cmd.id for cmd in synced}
        log.info("%d commands ready", len(synced))
        await self._add_owner_commands()
        self.purge_old_logs.start()
        self.rotate_status.start()
        if config.HEARTBEAT_URL:
            self.heartbeat.start()
        # uploading emojis can hit rate limits, don't hold up connecting for it
        self._icons_task = asyncio.create_task(self.load_icons())

    async def _add_owner_commands(self):
        commands_ = [cmd for cog in self.cogs.values() if (cmd := getattr(cog, "owner_command", None))]
        if not commands_:
            return
        guild_id = config.SUPPORT_GUILD_ID
        if not guild_id:
            try:
                invite = await self.fetch_invite(config.SUPPORT_URL)
                guild_id = invite.guild.id if invite.guild else 0
            except discord.HTTPException:
                guild_id = 0
        if not guild_id:
            return log.info("Couldn't find the support server, so /totals and /post-demo are off")
        guild = discord.Object(guild_id)
        for cmd in commands_:
            self.tree.add_command(cmd, guild=guild, override=True)
        try:
            await self._sync(f"commands:{guild_id}", guild=guild)
        except discord.HTTPException as e:
            log.info("Couldn't add /totals and /post-demo to the support server: %s", e)

    # syncing on every restart is slow and gets rate limited after a few restarts, so only when something changed
    async def _sync(self, key: str, guild: discord.abc.Snowflake | None = None) -> list:
        payload = [cmd.to_dict(self.tree) for cmd in self.tree.get_commands(guild=guild)]
        code = hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        if db.get_meta(key) == code:
            return await self.tree.fetch_commands(guild=guild)
        synced = await self.tree.sync(guild=guild)
        db.set_meta(key, code)
        return synced

    async def close(self):
        self.heartbeat.cancel()
        await super().close()
        if self.web:
            await self.web.close()

    def _format_commands(self):
        def fix(value):
            if isinstance(value, app_commands.locale_str):
                return app_commands.locale_str(ui_kit.tidy(str(value)))
            return ui_kit.tidy(value) if isinstance(value, str) else value

        for cmd in self.tree.get_commands(type=discord.AppCommandType.chat_input):  # skip context menus
            cmd.description = fix(cmd.description)
            for param in getattr(cmd, "_params", {}).values():
                param.description = fix(param.description)
                for choice in param.choices or []:
                    choice.name = fix(choice.name)

    # uploads assets/*.png as app emojis, re-uploads when a file changes
    async def load_icons(self):
        try:
            existing = await self.fetch_application_emojis()
        except Exception as e:  # runs in the background, so log it instead of losing it
            return log.warning("Couldn't load icon emojis: %s", e)
        for key, name in ICON_EMOJIS.items():
            path = config.ASSETS / f"{key}.png"
            if not path.exists():
                continue
            try:
                ui_kit.ICONS[key] = str(await self._icon(existing, key, name, path.read_bytes()))
            except Exception as e:
                log.warning("Couldn't load the %s icon: %s", key, e)
        for path in sorted((config.ASSETS / "meerkat").glob("looky_*.png")):  # the meerkat pack
            try:
                ui_kit.ICONS[path.stem] = str(await self._icon(existing, path.stem, path.stem, path.read_bytes()))
            except Exception as e:
                log.warning("Couldn't load the %s emoji: %s", path.stem, e)

    async def _icon(self, existing: list[discord.Emoji], key: str, name: str, image: bytes) -> discord.Emoji:
        code = hashlib.md5(image).hexdigest()
        names = [name] + ([OLD_ICON_NAMES[key]] if key in OLD_ICON_NAMES else [])
        # older versions added a hash to the name
        ours = re.compile("|".join(rf"{re.escape(n)}(_[0-9a-f]{{6}})?" for n in names))
        saved = db.get_icon(key)
        emoji = None
        if saved and saved[0] == code:
            emoji = next((e for e in existing if e.id == saved[1]), None)
        if emoji is None:  # same picture from an older version
            emoji = next((e for e in existing if e.name in {f"{n}_{code[:6]}" for n in names}), None)
        if emoji is None:
            for e in existing:
                if ours.fullmatch(e.name) or (saved and e.id == saved[1]):
                    await e.delete()
            emoji = await self.create_application_emoji(name=name, image=image)
        elif emoji.name != name and ours.fullmatch(emoji.name):
            try:  # rename old names, keep custom ones
                emoji = await emoji.edit(name=name)
            except discord.HTTPException:
                pass
        db.save_icon(key, code, emoji.id)
        return emoji

    async def on_ready(self):
        invite = discord.utils.oauth_url(self.user.id, permissions=discord.Permissions(config.INVITE_PERMISSIONS),
                                         scopes=("bot", "applications.commands"))
        n = len(self.guilds)
        log.info("Logged in as %s in %d server%s", self.user, n, "" if n == 1 else "s")
        log.info("Invite link: %s", invite)

    async def on_guild_join(self, guild: discord.Guild):
        me = guild.me
        channels = ([guild.system_channel] if guild.system_channel else []) + list(guild.text_channels)
        channel = next((c for c in channels if c.permissions_for(me).send_messages
                        and c.permissions_for(me).view_channel), None)
        if channel is None:
            return

        def cmd(name):
            cid = self.command_ids.get(name)
            return f"</{name}:{cid}>" if cid else f"`/{name}`"

        # no attach files = no banner
        files = []
        if channel.permissions_for(me).attach_files:
            files = [discord.File(io.BytesIO(await asyncio.to_thread(chart.welcome_banner)), filename="welcome.png")]
        title = ui_kit.titled(ui_kit.mascot("wave"), "Thanks for Adding Lookout")
        view = ui_kit.boxes(title, "Setup takes about a minute.", [
            ("settings", "Get Started", "Press **Set Up** and pick your mod channel, mod role and age roles.\n"
                                        "-# Only admins can do this.",
             [discord.ui.Button(label="Set Up", custom_id="lksetup:start")]),
            ("brand", "What Lookout Does", "- Removes inappropriate messages adults send to teens\n"
                                           "- Removes contact info sent to teens\n- Sends reports privately to your mods",
             None),
            ("help", "Need Help?", f"Anyone can type {cmd('help')}. For anything else, ask in the support server.",
             [discord.ui.Button(label="Support Server", style=discord.ButtonStyle.link, url=config.SUPPORT_URL)]),
        ], image="attachment://welcome.png" if files else None,
           image_text="Making Discord safer for teens: a message filter, a contact filter and private reports.")
        try:
            await channel.send(view=view, files=files)
        except discord.HTTPException:
            pass

    async def on_guild_remove(self, guild: discord.Guild):
        db.forget_guild(guild.id)
        log.info("Removed from %s, deleted its data", guild.id)

    @tasks.loop(minutes=STATUS_MINUTES)
    async def rotate_status(self):
        servers = len(self.guilds)
        text = STATUSES[self._status_index % len(STATUSES)].format(
            servers=f"{servers} server{'' if servers == 1 else 's'}")
        await self.change_presence(activity=discord.CustomActivity(name=text))
        self._status_index += 1

    @rotate_status.before_loop
    async def _wait_until_ready(self):
        await self.wait_until_ready()

    @tasks.loop(hours=24)
    async def purge_old_logs(self):
        removed = db.purge_old()
        if removed:
            log.info("Deleted %d log entries older than %d days", removed, config.RETENTION_DAYS)

    # ping healthchecks while we're connected
    @tasks.loop(seconds=60)
    async def heartbeat(self):
        if not self.shards or any(s.is_closed() for s in self.shards.values()) or not math.isfinite(self.latency):
            return
        try:
            async with self.web.get(config.HEARTBEAT_URL, timeout=aiohttp.ClientTimeout(total=10)):
                pass
        except Exception as e:
            log.debug("Couldn't reach the uptime monitor: %s", e)

    @heartbeat.before_loop
    async def _heartbeat_ready(self):
        await self.wait_until_ready()

    @staticmethod
    def is_staff(member: discord.Member) -> bool:
        s = db.get_settings(member.guild.id)
        return member.guild_permissions.moderate_members or bool(
            s.staff_role_id and any(r.id == s.staff_role_id for r in member.roles))

    async def alert_staff(self, guild: discord.Guild, view: discord.ui.LayoutView) -> discord.Message | None:
        s = db.get_settings(guild.id)
        channel = guild.get_channel(s.staff_channel_id) if s.staff_channel_id else None
        if channel is None:
            log.warning("Server %s has no staff channel set", guild.id)
            return None
        try:
            return await channel.send(view=view, allowed_mentions=ui_kit.ping_for(s.staff_role_id))
        except discord.HTTPException as e:
            log.warning("Couldn't post alert in server %s: %s", guild.id, e)
            return None


if __name__ == "__main__":
    config.check()
    LookoutBot().run(config.DISCORD_TOKEN, log_handler=None)
