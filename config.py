import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).parent


def _int(name, default):
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _float(name, default):
    value = os.getenv(name, "").strip()
    return float(value) if value else default


DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DEV_GUILD_ID = _int("DEV_GUILD_ID", None)  # legacy, only used for cleanup

# view channels, send messages, manage messages, attach files, read message history,
# move members, moderate members, manage nicknames
INVITE_PERMISSIONS = 1099662732288

NSFW_THRESHOLD = _float("NSFW_THRESHOLD", 0.6)
MAX_IMAGE_MB = _int("MAX_IMAGE_MB", 10)

REPORT_WINDOW_MINUTES = _int("REPORT_WINDOW_MINUTES", 10)
REPORT_COOLDOWN_SECONDS = _int("REPORT_COOLDOWN_SECONDS", 120)

RETENTION_DAYS = _int("RETENTION_DAYS", 90)

# optional, healthchecks.io ping url
HEARTBEAT_URL = os.getenv("HEARTBEAT_URL", "").strip()
# optional, posts the server count every 30 min
TOPGG_TOKEN = os.getenv("TOPGG_TOKEN", "").strip()

SUPPORT_URL = "https://discord.gg/BKedYyHgn9"
# where /totals, /post-demo and /trivia get synced, looked up from SUPPORT_URL if empty
SUPPORT_GUILD_ID = _int("SUPPORT_GUILD_ID", 0)

DB_PATH = BASE_DIR / "lookout.db"
ASSETS = BASE_DIR / "assets"


def check():
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")
