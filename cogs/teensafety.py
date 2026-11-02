import asyncio
import io
import logging
import re
import time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

import discord
from discord.ext import commands, tasks
from PIL import Image

import db
import ui_kit
from cogs.staff import _cid, action_buttons

try:
    import wordlists
except ImportError:  # the real patterns aren't public, see wordlists_example.py
    import wordlists_example as wordlists

log = logging.getLogger("lookout.teens")
if wordlists.__name__ == "wordlists_example":
    log.warning("wordlists.py is missing, the filters only have the few patterns in wordlists_example.py")
PREFIX = "lkteen"
TEEN, ADULT = 1, 3
NEW_ACCOUNT_DAYS = 30
DM_EVERY = 600  # min seconds between the same DM
FLIRTY_CASE_EVERY = 600  # messages within this go on the same case
MAX_CASE_NOTES = 5  # copy this many, then just count
RISK_THRESHOLD = 8
ALT_DAYS = 30  # alt check window
NEARBY_SECONDS = 120  # an adult's message this soon after a teen's counts as talking to them
NEARBY_MESSAGES = 5
REPORTER_DAYS = 7  # a teen's report only counts toward Keep Away after a week in the server
NETWORK_JOINED_DAYS = 14  # a server only shares with the lookout network after having lookout this long
NETWORK_MEMBERS = 50  # and with at least this many members
MIN_SIGNUP_AGE = 9  # almost nobody makes an account younger, so an older account than that allows isn't a teen's
FISHING_SERVERS = 2  # lookout network: warning signs in at least this many other servers
FISHING_TEENS = 3    # toward at least this many different teens
FISHING_LABELS = {"probe": "Asked teens their age, school or where they live",
                  "private": "Asked teens to move to DMs, another app or a call",
                  "gift": "Offered teens Robux or money in return for something",
                  "contact": "Sent teens contact info",
                  "flirty": "Sent teens inappropriate messages, which Lookout removed",
                  "teen_report": "Got reported by teens"}

# safety scan: these wait for the report instead of going to the mods ("" = already saved when it would have
# been removed). threats to share pictures and anything a member asked for still go right away
SCAN_FINDS = {"Inappropriate Message": "", "Inappropriate Request to a Teen": "", "Warning Signs": "warning_signs",
              "Not Really a Teen?": "fake_teen", "Said Two Different Ages": "two_ages", "Age Mismatch": "age_mismatch",
              "Possible Alt Account": "alt", "Caught in Other Servers": "network",
              "Going After Teens in Other Servers": "fishing", "Kept Away From Teens": "keep_away"}

# robux, nitro or money for something: "ill give you 1000 robux if you...", "robux if u...", "ill pay you to...".
# gifting a friend robux is normal, so it only counts when it's in return for something.
# a server can swap it for its own in wordlists.py (GIFT)
_YOU = r"(?:you|u|ya)\b(?!['’])"
GIFT = getattr(wordlists, "GIFT", None) or re.compile(
    rf"\b(?:give|gift|send|buy)\w*\s+{_YOU}\s*(?:\d[\d,.]*\s*k?\s*)?(?:robux|rbx|nitro|gift\s*cards?|v-?bucks|money|"
    rf"cash)?\s*(?:if|for)\s+{_YOU}"
    rf"|\b(?:robux|rbx|nitro|money|cash)\s+(?:if|for)\s+{_YOU}"
    rf"|\bi'?ll\s+pay\s+{_YOU}(?!\s+back)", re.I)

# nickname info. cities only count next to an age or school, never as the first word (Austin, Dallas...)
# "mia 14" and "14yo" are ages, "Sniper13" isn't
AGE_IN_NAME = re.compile(r"(?<![\w])1[3-7](?![\w])|(?<!\d)1[3-7]\s*(?:yo|y/o|yrs?|years?(?: old)?)\b", re.I)
SCHOOL_IN_NAME = re.compile(r"\b(?:middle|high|elementary)\s*school\b|\bjr\.?\s*high\b|\b(?:[6-9]|1[0-2])(?:th)?\s*grade\b"
                            r"|\bclass\s*of\s*20\d\d\b|\bc/o\s*20\d\d\b", re.I)
CITY_IN_NAME = re.compile(
    r"\b(?:new york|nyc|los angeles|chicago|houston|phoenix|philadelphia|philly|san antonio|san diego|dallas|"
    r"austin|jacksonville|columbus|charlotte|indianapolis|seattle|denver|boston|detroit|nashville|portland|"
    r"las vegas|memphis|baltimore|milwaukee|atlanta|miami|orlando|tampa|london|manchester|birmingham|"
    r"liverpool|glasgow|leeds|toronto|vancouver|montreal|calgary|sydney|melbourne|brisbane|perth)\b", re.I)


# birth years of 13 to 17 year olds, "mia 2011" or "jake '10". worked out each time so it moves with the year
def birth_year_in_name() -> re.Pattern:
    year = time.localtime().tm_year
    years = "|".join(str(y)[2:] for y in range(year - 18, year - 12))
    return re.compile(rf"(?<![\w])(?:20(?:{years})|'(?:{years}))(?![\w])")

# what someone says about their own age: "im 14", "i'm 15 btw", "im a 16 year old", "age: 17".
# only if nothing that sounds like a unit comes next, so "im 15 minutes away" and "im 14 kills in" don't count
_AGE_END = (r"(?:\s*(?:yo|y/o|yrs?(?:\s+old)?|years?(?:\s+old)?))?"
            r"(?=[ \t]*(?:$|[.,!?)~]|:\)|<3|(?:lol|lmao|lmfao|btw|and|but|so|rn|too|haha+|bro|dude|tho|though|ok|"
            r"okay|now|today|already|myself|also|irl|xd|fr|ngl|tbh|lowkey|why|wbu|hbu|just|actually)\b))")
_NOT_ME = r"(?<!\bif )(?<!\bwhen )(?<!\blike )(?<!\bthink )(?<!\bthought )(?<!\bsaid )(?<!\bpretend )"
SAYS_AGE = re.compile(
    rf"{_NOT_ME}\b(?:i'?m|i am)\s+(?:only\s+|just\s+|literally\s+|actually\s+)?(\d{{1,2}}){_AGE_END}"
    rf"|{_NOT_ME}\b(?:i'?m|i am)\s+(?:only\s+|just\s+)?an?\s+(\d{{1,2}})[\s-]*(?:yo|y/o|years?[\s-]old|yr[\s-]old)\b"
    rf"|\bmy age(?: is|'s)\s+(\d{{1,2}}){_AGE_END}"
    rf"|\bi (?:just )?turned\s+(\d{{1,2}}){_AGE_END}"
    r"|^[ \t]*age[ \t]*[:=\-][ \t]*(\d{1,2})\b", re.I | re.M)

# school years and birth years: "im in 8th grade", "i'm a sophomore in high school", "im in year 9", "i was born in 2011"
_IM = rf"{_NOT_ME}\b(?:i'?m|i am)\s+(?:only\s+|just\s+|currently\s+|still\s+)?"
SAYS_GRADE = re.compile(rf"{_IM}in\s+(?:the\s+)?(?:([6-9]|1[0-2])(?:st|nd|rd|th)\s+grade|grade\s+([6-9]|1[0-2])\b"
                        r"|year\s+([7-9]|1[0-3])\b)", re.I)
SAYS_SCHOOL = re.compile(rf"{_IM}(?:in\s+(middle school|junior high|jr\.? high|high school)\b"
                         r"|an?\s+(freshman|sophomore|junior)\s+in\s+(?:high school|hs)\b)", re.I)
SAYS_BORN = re.compile(rf"{_NOT_ME}\bi\s+was\s+born\s+in\s+((?:19|20)\d\d)\b", re.I)
SCHOOL_AGES = {"middle school": 13, "junior high": 13, "jr high": 13, "jr. high": 13, "high school": 15,
               "freshman": 14, "sophomore": 15, "junior": 16}
# a name that says they're a teen, for auto-detect. stricter than the nickname tip: "mia 14yo" and "jake 2011" count,
# "Agent 17" doesn't
NAME_AGE = re.compile(r"(?<!\d)(1[3-7])\s*(?:yo|y/o|y\.o\.?|yrs?(?:\s*old)?|years?\s*old)\b", re.I)

# kind: (points, max points, label). probe and private only reach 6 together, so it takes a third kind of sign,
# like offering robux, before someone gets flagged
WEIGHTS = {
    "flirty": (3, 9, "Sent an inappropriate message to a teen"),
    "contact": (3, 6, "Sent contact info to a teen"),
    "teen_report": (3, 6, "Reported by a teen"),
    "probe": (2, 3, "Asked a teen their age, school or where they live"),
    "private": (2, 3, "Asked a teen to move to DMs, another app or a call"),
    "gift": (2, 4, "Offered a teen Robux or money in return for something"),
}


NUMBER_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                "seven": "7", "eight": "8", "nine": "9"}
_NUMBER_WORD = re.compile(rf"\b(?:{'|'.join(NUMBER_WORDS)})\b", re.I)


def _counting(digits: str) -> bool:
    return digits in "01234567890123456789" or digits in "98765432109876543210"


def has_contact(text: str, own: bool = False) -> bool:
    # "five five five 123 4567" is a phone number too
    spelled = _NUMBER_WORD.sub(lambda m: NUMBER_WORDS[m.group().lower()], text)
    # 9-15 digits, longer is probably a discord id
    # skip round numbers like "100 200 300", those are scores, and counting like "1 2 3 4 5 6 7 8 9"
    phone = any(9 <= sum(c.isdigit() for c in m.group()) <= 15 and not wordlists.DATE.search(m.group())
                and not wordlists.IP.fullmatch(m.group())
                and not all(g.endswith("00") for g in re.findall(r"\d+", m.group()))
                and not _counting("".join(re.findall(r"\d", m.group())))
                for m in wordlists.PHONE.finditer(spelled))
    contact = wordlists.OWN_CONTACT if own else wordlists.CONTACT
    # same tricks as the message filter, "sn@p" and "s.n.a.p" are still snap
    return phone or bool(wordlists.EMAIL.search(text)) or any(contact.search(v) for v in variants(text))


# the text plus versions with the tricks undone, [3] is the most readable
def variants(text: str) -> list[str]:
    out = [text]
    clean = unicodedata.normalize("NFKC", wordlists.INVISIBLE.sub("", text))
    clean = "".join(c for c in unicodedata.normalize("NFKD", clean) if not unicodedata.combining(c))
    clean = clean.translate(wordlists.LOOKALIKES)
    out.append(clean)
    clean = wordlists.MIXED.sub(lambda m: m.group().translate(wordlists.LEET), clean)
    out.append(clean)
    clean = wordlists.DOTTED.sub(lambda m: re.sub(r"[.\-_*]", "", m.group()), clean)
    clean = wordlists.SPACED.sub(lambda m: m.group().replace(" ", ""), clean)
    out.append(clean)
    out.append(wordlists.LONG_REPEATS.sub(r"\1\1", clean))
    out.append(wordlists.REPEATS.sub(r"\1", clean))
    return out


def _patterns(langs) -> list[tuple[str, re.Pattern, re.Pattern]]:
    out = [("en", wordlists.EN_SEVERE, wordlists.EN_INAPPROPRIATE)] if "en" in langs else []
    return out + [(lang, *wordlists.LISTS[lang]) for lang in langs if lang in wordlists.LISTS]


def _accentless(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# (kind, read_as, lang), kind is "severe", "inappropriate" or None
# taking accents off doesn't count as a trick
def check_text(text: str, langs=("en",)) -> tuple[str | None, str | None, str | None]:
    versions, patterns = variants(text), _patterns(langs)
    for version in versions:
        for lang, severe, inappropriate in patterns:
            kind = "severe" if severe.search(version) else "inappropriate" if inappropriate.search(version) else None
            if kind:
                readable = versions[3] if versions[3] != text else version
                tricked = readable not in (text, _accentless(text)) and readable.lower() != _accentless(text).lower()
                return kind, (readable if tricked else None), lang
    return None, None, None


def flirty_kind(text: str, langs=("en",)) -> str | None:
    return check_text(text, langs)[0]


# threatening to share someone's pictures. older wordlists.py files don't have it
def has_threat(text: str) -> bool:
    threat = getattr(wordlists, "THREAT", None)
    return bool(threat) and any(threat.search(v) for v in variants(text))


# asking a teen for nudes, sexting or erotic roleplay. checked even when a teen asks, see wordlists.NUDE_ASK
def has_nude_ask(text: str) -> bool:
    ask = getattr(wordlists, "NUDE_ASK", None)
    return bool(ask) and any(ask.search(v) for v in variants(text))


# "probe" (age, school, where they live), "private" (dms, other apps, calls) or "gift" (robux or money for
# something). warning signs only
def soft_sign(text: str) -> str | None:
    for kind, rx in (("probe", getattr(wordlists, "PROBE", None)), ("private", getattr(wordlists, "PRIVATE", None)),
                     ("gift", GIFT)):
        if rx and any(rx.search(v) for v in variants(text)):
            return kind
    return None


# a teen reporting twice counts once, same for a burst of messages on one case
def score(guild_id: int, user_id: int) -> tuple[int, list[str]]:
    signals = db.signals(guild_id, user_id)
    counts = Counter(sig["kind"] for sig in signals if sig["kind"] not in ("teen_report", "flirty"))
    counts["teen_report"] = len({sig["other_id"] for sig in signals if sig["kind"] == "teen_report"})
    counts["flirty"] = len({(sig["detail"] or "").split(":")[0] for sig in signals if sig["kind"] == "flirty"})
    points, reasons = 0, []
    for kind, (each, most, label) in WEIGHTS.items():
        n = counts[kind]
        if n:
            points += min(n * each, most)
            reasons.append(f"{label} · {n} time{'' if n == 1 else 's'}")
    return points, reasons


def nickname_info(nick: str) -> tuple[list[str], str]:
    found, rest = [], nick
    for what, rx in (("age", AGE_IN_NAME), ("age", birth_year_in_name()), ("school", SCHOOL_IN_NAME)):
        if rx.search(rest):
            if what not in found:
                found.append(what)
            rest = rx.sub(" ", rest)
    if found:
        rest = rest.strip()
        cities = [m for m in CITY_IN_NAME.finditer(rest) if m.start() > 0]
        if cities:
            found.append("city")
            for m in reversed(cities):
                rest = rest[:m.start()] + " " + rest[m.end():]
    clean = re.sub(r"\s+", " ", re.sub(r"\s*[|,•·/]+\s*", " ", rest)).strip(" -_.")
    return found, clean


# small dhash, the same picture gives (almost) the same hash
def avatar_print(data: bytes) -> str:
    px = list(Image.open(io.BytesIO(data)).convert("L").resize((9, 8)).getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = bits << 1 | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def same_picture(a: str | None, b: str | None) -> bool:
    return bool(a and b) and bin(int(a, 16) ^ int(b, 16)).count("1") <= 6


def similar_name(a: str | None, b: str | None) -> bool:
    a, b = (re.sub(r"[^a-z0-9]", "", (x or "").lower()) for x in (a, b))
    if len(a) < 4 or len(b) < 4:
        return False
    return a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.8


def _gap(seconds: float) -> str:
    hours = max(1, round(seconds / 3600))
    if hours < 48:
        return f"{hours} hour{'' if hours == 1 else 's'}"
    return f"{round(hours / 24)} days"


def _quote(text: str) -> str:
    return "```\n" + (text or "").replace("```", "'''")[:1000] + "\n```"


# their words exactly as typed, on one line
def _said(text: str) -> str:
    flat = " / ".join(line.strip() for line in (text or "").splitlines() if line.strip())
    return f"`{flat.replace('`', chr(39))[:300]}`"


# their own words, without code and quotes of someone else
def _own_words(text: str) -> str:
    text = re.sub(r"```.*?```|`[^`]*`", " ", text or "", flags=re.S).replace("’", "'")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


# 13 to 40, younger or older is almost always a joke ("im 12 and what is this", "im 69 lol")
def stated_age(text: str) -> int | None:
    m = SAYS_AGE.search(_own_words(text))
    age = int(next(g for g in m.groups() if g)) if m else 0
    return age if 13 <= age <= 40 else None


# (age, source) from an age, a school year or a birth year. a school year gives the usual age for it, at least 13.
# 10 to 12 counts here too, roblox servers are full of kids that age. just not the "im 12 and what is this" joke
def told_age(text: str) -> tuple[int, str] | None:
    age = stated_age(text)
    if age:
        return age, "said"
    text = _own_words(text)
    m = SAYS_AGE.search(text)
    age = int(next(g for g in m.groups() if g)) if m else 0
    if 10 <= age <= 12 and not re.search(r"\bwhat is this\b", text, re.I):
        return age, "said"
    m = SAYS_GRADE.search(text)
    if m:
        grade, year = m.group(1) or m.group(2), m.group(3)
        return max(13, min(17, int(grade) + 5 if grade else int(year) + 4)), "school"
    m = SAYS_SCHOOL.search(text)
    if m:
        return SCHOOL_AGES[" ".join((m.group(1) or m.group(2)).lower().split())], "school"
    m = SAYS_BORN.search(text)
    age = time.localtime().tm_year - int(m.group(1)) if m else 0
    return (age, "born") if 13 <= age <= 40 else None


# the age a display name gives away, None if it doesn't
def name_age(name: str) -> int | None:
    m = NAME_AGE.search(name or "")
    if m:
        return int(m.group(1))
    m = birth_year_in_name().search(name or "")  # their birthday might not have come yet this year
    return max(13, time.localtime().tm_year - 2001 - int(m.group()[-2:])) if m else None


_hidden = ui_kit.hidden


class TeenSafety(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last: dict[tuple, float] = {}  # rate limits
        self._looked: set[tuple[int, int]] = set()  # checked since restart
        self._nicks: dict[tuple[int, int], str] = {}             # last nickname checked
        self._checked: dict[tuple[int, int], float] = {}  # last leave check per member
        # (guild, adult): (alert id, last message time, first message id, count)
        self._flirty_cases: dict[tuple[int, int], tuple[int, float, int, int]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._adults: dict[int, set[int]] = {}  # pressed the 18+ button, per server
        self._aged: set[tuple[int, int]] = set()  # account age checked since restart
        self._ages: dict[int, dict[int, tuple]] = {}  # auto-detect: ages members said, per server
        self._multi: set[int] | None = None  # warning signs in 2+ servers lately, see _fishing_check
        self._shared: tuple[float, set[int]] = (0, set())  # servers the network counts, and when that was worked out
        self.leave_check.start()

    def cog_unload(self):
        self.leave_check.cancel()

    def _background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # a warning sign. someone with signs in 2 servers gets looked up on the lookout network when they talk
    def _signal(self, guild_id: int, user_id: int, kind: str, other_id: int | None = None, detail: str = "") -> None:
        db.add_signal(guild_id, user_id, kind, other_id, detail)
        multi = self._multi_members()
        if user_id not in multi and len({g for g, _, _ in db.network_signals(user_id)}) >= 2:
            multi.add(user_id)

    def _multi_members(self) -> set[int]:
        if self._multi is None:
            self._multi = db.multi_server_members()
        return self._multi

    # servers the lookout network counts: network on, at least 50 members, and lookout there for 2 weeks,
    # so a server made yesterday can't put anyone on it
    @staticmethod
    def _can_share(guild: discord.Guild) -> bool:
        me = guild.me
        return ((guild.member_count or 0) >= NETWORK_MEMBERS and me is not None and me.joined_at is not None
                and (discord.utils.utcnow() - me.joined_at).days >= NETWORK_JOINED_DAYS)

    def _sharing(self) -> set[int]:
        at, shared = self._shared
        if time.time() - at > 600:
            shared = {g.id for g in self.bot.guilds if self._can_share(g) and db.get_settings(g.id).network}
            self._shared = (time.time(), shared)
        return shared

    # roles

    def verified(self, guild_id: int) -> set[int]:
        if guild_id not in self._adults:
            self._adults[guild_id] = db.verified_adults(guild_id)
        return self._adults[guild_id]

    def ages(self, guild_id: int) -> dict[int, tuple]:
        if guild_id not in self._ages:
            self._ages[guild_id] = db.seen_ages(guild_id)
        return self._ages[guild_id]

    # how old they are now from what they said (it moves with time), or from their name. None if there's no telling
    def seen_age(self, member, s: db.GuildSettings) -> int | None:
        seen = self.ages(s.guild_id).get(member.id)
        if seen and time.time() - seen[3] < db.AGE_DAYS * 86400:
            return seen[0] + int((time.time() - seen[3]) / (365.25 * 86400))
        return name_age(getattr(member, "display_name", ""))

    @staticmethod
    def _role_band(member, s: db.GuildSettings) -> int | None:
        ids = {r.id for r in getattr(member, "roles", [])}
        if ids & (s.role_ids("young_roles") | s.role_ids("older_roles")):
            return TEEN
        return ADULT if ids & s.role_ids("adult_roles") else None

    # TEEN or ADULT. the 18+ button beats roles, discord checked their age.
    # no age role counts as TEEN with teen by default. with auto-detect it's what they said or their name shows.
    # otherwise ADULT unless the server ignores those
    def band(self, member: discord.Member, s: db.GuildSettings, explicit: bool = False) -> int | None:
        if member.id in self.verified(s.guild_id):
            return ADULT
        role = self._role_band(member, s)
        if role:
            return role
        if s.teen_default:
            return TEEN
        age = self.seen_age(member, s) if s.auto_ages else None
        if age is not None:
            return TEEN if age < 18 else ADULT
        return None if explicit or s.no_age_role == "ignore" else ADULT

    # warning signs (asking a teen's age, to DM, robux for something) are normal between kids. with auto-detect most
    # members' ages aren't known, and in a roblox server those are mostly kids, so the signs only count from someone
    # known to be an adult: an adult role, the 18+ button, an adult age they said, or caught posing as a teen
    def _sure_adult(self, member, s: db.GuildSettings) -> bool:
        if not s.auto_ages or member.id in self.verified(s.guild_id) or self._role_band(member, s) == ADULT:
            return True
        age = self.seen_age(member, s)
        return (age is not None and age >= 18) or db.fake_teen(s.guild_id, member.id) is not None

    # "(under 18)" on alerts. with auto-detect it says how lookout knows, since there's no role to show it
    def _under_18(self, member, s: db.GuildSettings) -> str:
        if not s.auto_ages or self._role_band(member, s):
            return "(under 18)"
        place = (self.ages(s.guild_id).get(member.id) or (0, "", None))[2]
        ch, _, mid = (place or "").partition(":")
        if mid:
            return f"(under 18, [said so](https://discord.com/channels/{s.guild_id}/{ch}/{mid}))"
        if member.id not in self.ages(s.guild_id) and name_age(member.display_name):
            return "(under 18, their name says so)"
        return "(under 18)"

    def exempt(self, member) -> bool:
        return (member.bot or member.id == member.guild.owner_id
                or (isinstance(member, discord.Member) and self.bot.is_staff(member)))

    def is_teen(self, member, s: db.GuildSettings) -> bool:
        return not self.exempt(member) and self.band(member, s, explicit=True) == TEEN

    def _once(self, key: tuple, seconds: float = DM_EVERY) -> bool:
        now = time.time()
        if now - self._last.get(key, 0) < seconds:
            return False
        if len(self._last) > 10000:  # cleanup
            self._last = {k: t for k, t in self._last.items() if now - t < 3600}
        self._last[key] = now
        return True

    async def _dm(self, user, view, key: tuple) -> None:
        if not self._once(("dm",) + key):
            return
        try:
            await user.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass    # DMs closed

    # messages

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        if message.guild is None or author.bot or not isinstance(author, discord.Member):
            return
        s = db.get_settings(message.guild.id)
        if not s.configured or self.exempt(author):
            return
        if s.network and db.has_network_catches(author.id):
            self._background(self._network_check(message, author, s))
        if s.network and author.id in self._multi_members():
            self._background(self._fishing_check(message, author, s))
        if not s.teen_safety:
            return
        text = message.content or ""
        told = told_age(text) if s.auto_ages or s.age_check or s.nick_check else None
        if told and s.auto_ages:
            self._learn_age(message, author, *told, s)
        mine = self.band(author, s)
        if told and told[0] < 18 and mine == TEEN:
            self._age_shared(message, author, s)  # before _look, so the safety tips come in the same DM
        self._background(self._look(author, s))
        if told and told[1] == "said" and s.age_check:
            self._background(self._check_age(message, author, told[0], s))

        # sextortion usually comes from someone pretending to be a teen, so this one isn't only for adults
        if mine != ADULT and s.flirty_filter and has_threat(text):
            return await self._from_anyone(message, author, s, threat=True)
        # discord doesn't allow asking a minor for nudes even when it's another teen asking
        if mine != ADULT and s.flirty_filter and has_nude_ask(text):
            return await self._from_anyone(message, author, s, threat=False)

        # probably not a teen (said an adult age, account too old, on the network): checked like an adult from here.
        # messages to them still count as messages to a teen
        if mine == TEEN and await self._fake_teen(author, s):
            mine = ADULT
        if mine == TEEN:
            if s.contact_filter and has_contact(text, own=True):
                if await self._remove(message, f"Your message in {message.channel.mention} had contact info in it, so "
                                               "Lookout removed it. Everyone in the server can see your messages, so "
                                               "it's safer to keep it private.", "own_contact", mistake=False):
                    db.bump("contact")
            return
        if mine != ADULT:
            return
        kind, read_as, lang = check_text(text, s.languages) if s.flirty_filter else (None, None, None)
        contact = s.contact_filter and has_contact(text)
        restricted = db.restricted(message.guild.id, author.id)
        soft = None if kind or contact or restricted or not self._sure_adult(author, s) else soft_sign(text)
        if not (kind or contact or restricted or soft):
            return  # nothing to do
        teens = await self._teens_reached(message, s)
        nearby = not teens and bool(kind or contact)
        if nearby:  # no reply or mention, but a teen just said something in here
            teens = await self._teen_nearby(message, s)
        if not teens:
            return

        guild = message.guild
        where = message.channel.mention
        if soft:  # nothing gets removed, it only adds to Warning Signs. once a day per teen
            if self._once(("soft", guild.id, author.id, teens[0].id, soft), 86400):
                self._signal(guild.id, author.id, soft, teens[0].id, f"{message.channel.id}:{message.id}")
                await self._check_risk(guild, author, s)
            return
        if kind:
            if contact:
                self._signal(guild.id, author.id, "contact", teens[0].id)
            db.log(guild.id, author.id, "teen_flirty", text[:200], message.channel.id, message_id=message.id)
            removed = await self._remove(message, f"Your {'reply' if message.reference else 'message'} to a member "
                                                  f"under 18 in {where} was removed. Inappropriate messages to teens "
                                                  "aren't allowed here, and the mods have been told.",
                                         "threat" if has_threat(text) else "inappropriate", teens[0].id)
            threat = has_threat(text)
            await self._flirty_alert(message, author, teens[0], kind, read_as, removed, s, lang, threat)
            if removed:  # only DM the teen if it actually got deleted
                db.bump("removed")
                if threat or has_nude_ask(text):
                    db.bump("threats")
                if not nearby:
                    self._share(message, author, teens[0], s)
                for teen in teens:
                    await self._heads_up(message, author, teen, threat)
        elif restricted and not nearby:
            await self._remove(message, f"Your message in {where} was removed. You can't reply to or mention "
                                        "members under 18 here until the mods review a report about you.",
                               "restricted", teens[0].id)
            return
        else:
            self._signal(guild.id, author.id, "contact", teens[0].id)
            db.log(guild.id, author.id, "teen_contact", text[:200], message.channel.id, message_id=message.id)
            if await self._remove(message, f"Your message in {where} was removed. This server doesn't allow sending "
                                           "contact info to members under 18.", "contact", teens[0].id):
                db.bump("contact")
                for teen in teens:
                    await self._heads_up(message, author, teen, contact=True)
        await self._check_risk(guild, author, s)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # people send "hi" and edit it later, so edits get checked too
        old, new = before.content or "", after.content or ""
        guild = getattr(after, "guild", None)
        langs = db.get_settings(guild.id).languages if guild else ("en",)
        if (has_contact(new) and not has_contact(old)) or (flirty_kind(new, langs) and not flirty_kind(old, langs)):
            await self.on_message(after)

    async def _teens_reached(self, message: discord.Message, s) -> list[discord.Member]:
        found = {m.id: m for m in message.mentions if isinstance(m, discord.Member)}
        ref = message.reference.resolved if message.reference else None
        if isinstance(ref, discord.Message) and ref.author.id not in found:
            uid = ref.author.id
            member = message.guild.get_member(uid)
            if member is None:
                try:
                    member = await message.guild.fetch_member(uid)
                except discord.HTTPException:
                    member = None
            if member:
                found[uid] = member
        found.pop(message.author.id, None)
        return [m for m in found.values() if not m.bot and not self.exempt(m) and self.band(m, s) == TEEN]

    # the teen who talked in this channel right before, or whose thread it is. only looked up for flagged messages
    async def _teen_nearby(self, message: discord.Message, s) -> list[discord.Member]:
        guild, cutoff = message.guild, message.created_at.timestamp() - NEARBY_SECONDS
        ids = []
        try:
            async for m in message.channel.history(limit=NEARBY_MESSAGES, before=message):
                if m.created_at.timestamp() < cutoff:
                    break
                if not m.author.bot and m.author.id != message.author.id and m.author.id not in ids:
                    ids.append(m.author.id)
        except discord.HTTPException:
            pass
        owner = getattr(message.channel, "owner_id", None)
        if owner and owner != message.author.id and owner not in ids:
            ids.append(owner)
        for uid in ids:
            # history gives users, not members, so get the member to see their roles
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except discord.HTTPException:
                    continue
            if not member.bot and not self.exempt(member) and self.band(member, s) == TEEN:
                return [member]
        return []

    # find: what it is in a safety scan report, teen: who it was sent to
    async def _remove(self, message: discord.Message, why: str, find: str, teen: int | None = None,
                      mistake: bool = True) -> bool:
        if db.get_settings(message.guild.id).scanning:  # only watching: saved for the report, left where it is
            db.add_scan_find(message.guild.id, find, message.author.id, teen, message.jump_url)
            return False
        try:
            await message.delete()
        except discord.HTTPException:
            log.info("Couldn't remove a message in %s (missing Manage Messages?)", message.channel.id)
            return False
        view = ui_kit.card("Message Removed", why + ("\n-# Think it was a mistake? Let the mods know." if mistake else ""),
                           subtitle=f"From {discord.utils.escape_markdown(message.guild.name)}")
        await self._dm(message.author, view, (message.guild.id, message.author.id, "removed"))
        return True

    # repeats go on the same case, signals use the first message id
    # so False Alarm can undo all of them
    async def _flirty_alert(self, message: discord.Message, adult: discord.Member, teen: discord.Member, kind: str,
                            read_as: str | None, removed: bool, s: db.GuildSettings, lang: str | None = "en",
                            threat: bool = False, sexual: bool = False) -> None:
        guild = message.guild
        ref = message.reference.resolved if message.reference else None
        jump = ref.jump_url if isinstance(ref, discord.Message) else message.channel.jump_url
        key, now = (guild.id, adult.id), time.time()
        recent = self._flirty_cases.get(key)
        if recent and now - recent[1] < FLIRTY_CASE_EVERY:
            alert_id, _, first, count = recent
            case = db.case_by_message(alert_id)
            if case and case["status"] == "open":
                self._signal(guild.id, adult.id, "flirty", teen.id, f"{first}:{message.id}")
                self._flirty_cases[key] = (alert_id, now, first, count + 1)
                if count < MAX_CASE_NOTES:
                    await self._note_on_case(guild, case, f"**Another one** to <@{teen.id}> in {message.channel.mention}"
                                                          f" · <t:{int(now)}:R>\n{_hidden(message.content)}")
                elif count == MAX_CASE_NOTES:
                    await self._note_on_case(guild, case, "-# More messages were removed after this. Each one is in "
                                                          "their /history.")
                return
        self._signal(guild.id, adult.id, "flirty", teen.id, str(message.id))
        done = ("Lookout removed it from chat. This copy stays here for the mods." if removed else
                f"**Not removed,** Lookout is only running a Safety Scan here. [Jump to It]({message.jump_url})"
                if s.scanning else
                f"**Lookout couldn't remove it.** Give Lookout **Manage Messages**. [Jump to It]({message.jump_url})")
        if read_as:
            done = f"**Spelled to get past filters.** Lookout read it as {_hidden(read_as, 300)}\n-# " + done
        buttons = action_buttons(guild.id, message.channel.id, adult.id)
        buttons.insert(2, self._btn("False Alarm", "falsealarm", adult.id, message.id))
        under = self._under_18(teen, s)
        if threat:
            title, what = ("Threat to Share Pictures",
                           f"Threatened to share pictures of <@{teen.id}> {under}. This is how sextortion starts.")
        elif sexual:  # sent by a teen or someone without an age role
            title, what = ("Inappropriate Request to a Teen",
                           f"Asked <@{teen.id}> {under} for pictures or something inappropriate. Discord doesn't "
                           "allow this even between two teens.")
        else:
            title, what = "Inappropriate Message", f"Sent an inappropriate message to <@{teen.id}> {under}."
        alert_id = await self._case(
            guild, s, adult.id, "teen", title, [
                ("member", "Who", ui_kit.member_line(adult)),
                ("report", "What Happened", what
                                            + (f" **{wordlists.NAMES[lang]}.**" if lang and lang != "en" else "") + "\n"
                                            f"{_hidden(message.content)}\n-# {done}"),
                ("cases", "Where", f"{message.channel.mention} · [Jump to Conversation]({jump})"),
            ], footer="False Alarm closes the case and takes it off their Warning Signs.",
            buttons=buttons, urgent=kind == "severe", thumbnail=adult.display_avatar.url,
            evidence=jump, summary=f"Inappropriate message to a member under 18: {(message.content or '')[:150]}")
        if alert_id:
            self._flirty_cases[key] = (alert_id, now, message.id, 1)
            db.bump("filter_alerts")

    async def _heads_up(self, message: discord.Message, adult: discord.Member, teen: discord.Member,
                        threat: bool = False, contact: bool = False) -> None:
        if not self._once(("headsup", message.guild.id, adult.id, teen.id)):
            return
        how = ("replied to you" if message.reference else "mentioned you" if teen in message.mentions
               else "wrote to you right after you talked")
        add_app = ui_kit.add_app_button()
        name = discord.utils.escape_markdown(adult.name)
        if threat:
            view = ui_kit.card(
                ui_kit.titled("warning", "A Threat to You Was Removed"),
                f"**@{name}** {how} in {message.channel.mention} with a threat about your pictures, so Lookout "
                "removed it. The mods know about it.",
                ("Good to Know", "**You're not in trouble.** Don't pay and don't send anything else. Paying almost "
                                 "never makes it stop."),
                ("Get Help", "Press **Someone's Threatening Me** to see what to do next. Talking to an adult you trust "
                             "helps too."),
                subtitle=f"From {discord.utils.escape_markdown(message.guild.name)}",
                buttons=[self._btn("Tell the Mods", "tellmore", message.guild.id, adult.id),
                         discord.ui.Button(label="Someone's Threatening Me", custom_id="lkhelp:threat"),
                         ui_kit.block_button()])
        else:
            what = ("their contact info. Adults sharing socials or numbers with teens isn't allowed here, so Lookout "
                    "removed it." if contact else "something that wasn't okay, so Lookout removed it. The mods know "
                                                "about it.")
            view = ui_kit.card(
                ui_kit.titled("warning", "A Message to You Was Removed"),
                f"**@{name}** {how} in {message.channel.mention} with {what}",
                ("Good to Know", "**You didn't do anything wrong.** You don't have to reply, and it's okay to block "
                                 "them.\n-# Want to add something? Press **Tell the Mods**. They won't say it was you."),
                ("If They DM You", "You can report that to the mods too. Add Lookout to your apps, then right-click the "
                                   "message > **Apps** > **Report Message to Mods**.") if add_app else None,
                subtitle=f"From {discord.utils.escape_markdown(message.guild.name)}",
                buttons=[self._btn("Tell the Mods", "tellmore", message.guild.id, adult.id), ui_kit.block_button()]
                + ([add_app] if add_app else []))
        try:
            await teen.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            if not contact:  # contact info has no case to note it on
                await self._dm_failed(message.guild, adult.id, teen.id)

    # DMs closed: say so on the case, so the mods know the teen didn't get the heads-up
    async def _dm_failed(self, guild: discord.Guild, adult_id: int, teen_id: int) -> None:
        recent = self._flirty_cases.get((guild.id, adult_id))
        case = db.case_by_message(recent[0]) if recent else None
        if case:
            await self._note_on_case(guild, case, f"-# Couldn't DM <@{teen_id}>, their DMs are closed. Check in "
                                                  "with them another way.")

    # a threat to share pictures, or asking for nudes, sent to a teen by someone who isn't an adult here
    # (adults go through on_message)
    async def _from_anyone(self, message: discord.Message, author: discord.Member, s: db.GuildSettings,
                           threat: bool) -> None:
        teens = await self._teens_reached(message, s) or await self._teen_nearby(message, s)
        if not teens:
            return
        guild = message.guild
        db.log(guild.id, author.id, "teen_flirty", (message.content or "")[:200], message.channel.id,
               message_id=message.id)
        why = ("Threatening to share someone's pictures isn't allowed here" if threat else
               "Asking anyone under 18 for inappropriate pictures or chats isn't allowed, even between teens")
        removed = await self._remove(message, f"Your message in {message.channel.mention} was removed. {why}, and the "
                                              "mods have been told.", "threat" if threat else "pics", teens[0].id)
        await self._flirty_alert(message, author, teens[0], "severe", None, removed, s, threat=threat,
                                 sexual=not threat)
        if removed:
            db.bump("removed")
            db.bump("threats")  # threats and picture requests, for /totals
            for teen in teens:
                await self._heads_up(message, author, teen, threat=threat)
        await self._check_risk(guild, author, s)

    # keep away after 2 reports

    async def reported(self, guild: discord.Guild, reporter: discord.Member, target: discord.Member) -> None:
        s = db.get_settings(guild.id)
        if not s.teen_safety or self.band(reporter, s) != TEEN or self.band(target, s) != ADULT:
            return
        if self.exempt(target):
            return
        # alts made to fake-report someone are new, so they don't count toward keeping anyone away
        now = discord.utils.utcnow()
        new = (not reporter.joined_at or (now - reporter.joined_at).days < REPORTER_DAYS
               or (now - reporter.created_at).days < NEW_ACCOUNT_DAYS)
        self._signal(guild.id, target.id, "teen_report", reporter.id, "new" if new else "")
        if (s.teen_restrict and not db.restricted(guild.id, target.id)
                and len(db.teen_reporters(guild.id, target.id, settled_only=True)) >= 2):
            if not s.scanning:  # a scan only notes that it would have
                db.set_restricted(guild.id, target.id, True)
                db.log(guild.id, target.id, "teen_restricted", "Reported by 2 different teens")
            await self._case(guild, s, target.id, "teen", "Kept Away From Teens", [
                ("member", "Who", ui_kit.member_line(target)),
                ("report", "What Happened", f"<@{target.id}> was reported by **2 different teens** in the last 30 "
                                            "days, so Lookout is keeping them away from teens until you decide. Their "
                                            "replies to teens and mentions of teens get removed."),
            ], footer="Lift it if the reports were wrong. Keep it while you look into it.",
               buttons=[self._btn("Lift", "lift", target.id), self._btn("Keep", "keep", target.id)], urgent=True,
               status="**Urgent** · Waiting for the mods")
        await self._check_risk(guild, target, s)

    # warning signs

    async def _check_risk(self, guild: discord.Guild, member, s: db.GuildSettings) -> None:
        mode = s.risk_now()
        if mode == "off":
            return
        points, reasons = score(guild.id, member.id)
        if points < RISK_THRESHOLD:
            return
        test = mode == "test"
        flag = db.risk_flag(guild.id, member.id)
        if flag and (test or not flag["test"]):
            return      # already flagged in the last 30 days
        db.set_risk_flag(guild.id, member.id, points, test)
        if test:
            return  # test mode: only shows up in /setup
        # those messages stay up, so the mods can read them where they were said
        said = {"probe": "Asked a teen's age, school or where they live", "private": "Asked a teen to talk in private",
                "gift": "Offered a teen Robux or money for something"}
        links = [f"- {said[sig['kind']]} · <#{ch}> · [Jump to It](https://discord.com/channels/{guild.id}/{ch}/{mid})"
                 for sig in db.signals(guild.id, member.id) if sig["kind"] in said
                 for ch, _, mid in [(sig["detail"] or "").partition(":")] if ch.isdigit() and mid.isdigit()]
        await self._case(guild, s, member.id, "risk", "Warning Signs", [
            ("member", "Who", ui_kit.member_line(member)),
            ("warning", "Warning Signs, Last 30 Days", "\n".join(f"- {r}" for r in reasons)),
            ("cases", "Where to Look", "\n".join(links[-5:])),  # skipped when empty
        ], footer="One sign never flags anyone. Several added up, so take a look before acting.",
           buttons=action_buttons(guild.id, None, member.id))

    async def _case(self, guild, s, target_id: int, kind: str, title: str, parts, footer, buttons, urgent=False,
                    thumbnail=None, evidence=None, status=None, ping=None, summary=None, report=True) -> int | None:
        if s.scanning and title in SCAN_FINDS:  # saved for the scan report, the mods aren't told yet
            if SCAN_FINDS[title]:
                db.add_scan_find(guild.id, SCAN_FINDS[title], target_id, None, evidence)
            return None
        case_no = db.next_case_no(guild.id)
        if report:
            buttons = list(buttons)
            ui_kit.add_action(buttons, "Report to Discord", "A ready-made report for Discord's safety team",
                              f"{PREFIX}:tns:{target_id}")
        ping = urgent if ping is None else ping
        view = ui_kit.alert(
            ui_kit.titled("staff", f"{title} · #{case_no}"),
            "Teen Safety" + (f" · <@&{s.staff_role_id}>" if s.staff_role_id and ping else ""),
            status or f"**{'Urgent' if urgent else 'Open'}** · Unclaimed", parts,
            buttons=buttons, thumbnail=thumbnail, footer=footer)
        msg = await self.bot.alert_staff(guild, view)
        if msg:
            db.create_case(guild.id, msg.id, msg.channel.id, case_no, kind, None, target_id, summary or title, evidence,
                           urgent)
            return msg.id
        return None

    # first time we see someone

    # alt check + tips DM once per member per restart, the db stops repeats
    async def _look(self, member: discord.Member, s: db.GuildSettings) -> None:
        key = (member.guild.id, member.id)
        try:
            if key not in self._looked:
                self._looked.add(key)
                db.forget_leaver(member.guild.id, member.id)     # back on the same account
                if s.alt_catcher:
                    await self._check_alt(member, s)
                # a safety scan doesn't message anyone
                if s.tips_dm and not s.scanning and self.is_teen(member, s) and db.first_nudge(member.guild.id, member.id):
                    await self._tips_dm(member, s)
            if (s.nick_check and not s.scanning and self.is_teen(member, s)
                    and self._nicks.get(key) != member.display_name):
                self._nicks[key] = member.display_name
                await self._check_nick(member)
        except Exception:
            log.warning("Couldn't finish the checks for %s", member.id, exc_info=True)

    # the safety tips as (sections, buttons), the "you shared your age" DM carries them too
    @staticmethod
    def _tips_parts() -> tuple[list, list]:
        buttons = [discord.ui.Button(label="Safety Center", style=discord.ButtonStyle.link, url=ui_kit.SAFETY_CENTER_URL)]
        add_app = ui_kit.add_app_button()
        if add_app:
            buttons.append(add_app)
        return [
            ("Control Who Can DM You", "Click the server's name, open **Privacy Settings** and turn off **Direct "
                                       "Messages**. People can still talk to you in the server, where the mods can "
                                       "see it.\n-# DMs from people who aren't your friends go to **Message Requests** "
                                       "first. You don't have to open them."),
            ("Red Flags", "Be careful if someone you only know online asks your age or if you're alone, offers you "
                          "gifts, or wants to move the chat to another app.\n-# If Discord warns you about a friend "
                          "request, there's a reason. You can ignore it."),
            ("If Something Feels Off", "Type **/report** in the server, or right-click them, then **Apps** > **Report "
                                       "to Mods**. Only the mods see it."
                                       + ("\n-# Not sure about a DM? Add Lookout to your apps, then right-click it > "
                                          "**Apps** > **Is this safe?** or **Report Message to Mods**." if add_app
                                          else "")),
        ], buttons

    async def _tips_dm(self, member: discord.Member, s: db.GuildSettings) -> None:
        name = discord.utils.escape_markdown(member.guild.name)
        sections, buttons = self._tips_parts()
        why = (f"You have a teen role in **{name}**" if self._role_band(member, s) == TEEN
               else f"Lookout looks out for teens in **{name}**")
        view = ui_kit.card(
            ui_kit.titled(ui_kit.mascot("wave", "brand"), "Before You Start Chatting"),
            f"{why}, so here are a few things that help you stay safe there.", *sections,
            subtitle=f"From {name}", footer="You'll only get this message once.", buttons=buttons)
        try:
            await member.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass    # DMs closed

    async def _check_nick(self, member: discord.Member) -> None:
        nick = member.display_name
        found, clean = nickname_info(nick)
        if not found or db.nick_tipped(member.guild.id, member.id, nick):
            return
        guild = member.guild
        name = discord.utils.escape_markdown(guild.name)
        shows = " and ".join(", ".join(f"your {what}" for what in found).rsplit(", ", 1))
        me = guild.me
        can_fix = bool(clean and clean != nick and me.guild_permissions.manage_nicknames
                       and member.id != guild.owner_id and me.top_role > member.top_role)
        buttons = [self._btn(f"Change It to {clean}"[:80], "nick", guild.id, member.id),
                   self._btn("Keep It", "nickkeep", guild.id, member.id)] if can_fix else None
        view = ui_kit.card(
            ui_kit.titled(ui_kit.mascot("watching", "warning"), "Your Nickname Shares a Lot"),
            f"Your nickname in **{name}** is **{discord.utils.escape_markdown(nick)}**. Everyone there can see {shows}.",
            ("Good to Know", "A nickname without your age, school or city is safer. You can change it anytime in your "
                             "server profile" + (", or let Lookout do it for you." if can_fix else ".")),
            subtitle=f"From {name}", buttons=buttons)
        try:
            await member.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    # members who leave during a case

    # no members intent so we can't see leaves, check the people we care
    # about instead (each one at most every 30 min)
    @tasks.loop(minutes=10)
    async def leave_check(self):
        now = time.time()
        for guild_id, user_id in db.watch_list():
            guild, key = self.bot.get_guild(guild_id), (guild_id, user_id)
            if guild is None or guild.get_member(user_id) or now - self._checked.get(key, 0) < 1800:
                continue
            self._checked[key] = now
            try:
                await guild.fetch_member(user_id)
                continue
            except discord.NotFound:
                pass
            except discord.HTTPException:
                continue
            try:
                await self._left(guild, user_id)
            except Exception:
                log.warning("Couldn't save that %s left %s", user_id, guild_id, exc_info=True)
            await asyncio.sleep(1)

    @leave_check.before_loop
    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

    async def _left(self, guild: discord.Guild, user_id: int) -> None:
        user, picture = None, None
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user.avatar:
                picture = avatar_print(await user.display_avatar.replace(size=64, static_format="png").read())
        except (discord.HTTPException, OSError):
            pass
        cases = db.mark_left(guild.id, user_id, user.name if user else None, user.global_name if user else None,
                             picture)
        # recheck everyone, an alt might already be here
        self._looked = {key for key in self._looked if key[0] != guild.id}
        if cases:
            db.log(guild.id, user_id, "teen_left", f"Left the server while case #{cases[-1]['case_no']} was open")
        for case in cases:
            await self._note_on_case(guild, case, f"-# <@{user_id}> left the server while this case was open · "
                                                  f"<t:{int(time.time())}:R>")

    async def _note_on_case(self, guild: discord.Guild, case: dict, text: str) -> None:
        channel = guild.get_channel(case["staff_channel_id"])
        if channel is None:
            return
        try:
            msg = await channel.fetch_message(case["message_id"])
            view = discord.ui.LayoutView.from_message(msg, timeout=None)
            box = ui_kit.action_box(view)
            used = [i.id or 0 for i in view.walk_children()]
            line = discord.ui.TextDisplay(ui_kit.tidy(text), id=max([ui_kit.STAFF_NOTE_ID - 1, *used]) + 1)
            ui_kit.insert_above_actions(box, [line])
            await msg.edit(view=view, allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, StopIteration):
            log.info("Couldn't add a line to case %s", case["case_no"])

    # the 18+ button. it only works in age-restricted channels, discord keeps everyone it hasn't verified out of those

    @commands.Cog.listener("on_interaction")
    async def adult_button(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if cid not in ("lkadult:yes", "lkadult:no"):
            return
        if not getattr(interaction.channel, "is_nsfw", lambda: False)():
            return await interaction.response.send_message(view=ui_kit.note(
                "This button only works in an age-restricted channel."), ephemeral=True)
        on = cid == "lkadult:yes"
        guild, user = interaction.guild, interaction.user
        db.set_verified_adult(guild.id, user.id, on)
        (self.verified(guild.id).add if on else self.verified(guild.id).discard)(user.id)
        await interaction.response.send_message(view=ui_kit.note(
            "**Saved.** Lookout treats you as an adult in this server now. Nobody can see that you pressed this."
            if on else "**Removed.** Lookout won't treat you as an adult here anymore."), ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        self._adults.pop(guild.id, None)  # the db rows go in forget_guild
        self._ages.pop(guild.id, None)

    # not really a teen. an adult who picks a teen role, or just never takes the 18+ one with teen by default,
    # would skip every adult check. lookout can't change roles, so it checks their messages to teens like an adult's

    # the oldest age their teen role allows, 17 if the name doesn't say. with auto-detect, the age they said
    # (a school year can be a year off). None without either
    def _claimed_oldest(self, member: discord.Member, s: db.GuildSettings) -> int | None:
        teen = s.role_ids("young_roles") | s.role_ids("older_roles")
        roles = [r for r in member.roles if r.id in teen]
        if not roles:
            age = self.seen_age(member, s) if s.auto_ages and not self._role_band(member, s) else None
            if age is None or age >= 18:
                return None
            # kids under 13 often signed up younger than they should have, so the account check starts at 13
            return max(13, age + (self.ages(s.guild_id).get(member.id, (0, ""))[1] == "school"))
        ages = [int(n) for r in roles for n in re.findall(r"\d+", r.name) if 10 <= int(n) <= 17]
        return max(ages) if ages else 17

    # an account older than a teen could have, like 7 years on someone in 13-15
    def _too_old(self, member: discord.Member, s: db.GuildSettings) -> str | None:
        oldest = self._claimed_oldest(member, s) or 17
        years = int((discord.utils.utcnow() - member.created_at).days / 365.25)
        return f"account:{years}:{oldest}" if oldest - years < MIN_SIGNUP_AGE else None

    async def _fake_teen(self, member: discord.Member, s: db.GuildSettings) -> bool:
        if db.fake_teen(s.guild_id, member.id):
            return True
        key = (s.guild_id, member.id)
        if key in self._aged:
            return False
        self._aged.add(key)
        # lookout network: an adult age they gave in another server. the 18+ button there counts too
        elsewhere = db.adult_elsewhere(member.id, s.guild_id, self._sharing()) if s.network else None
        reasons = [r for r in (f"elsewhere:{elsewhere}" if elsewhere else None, self._too_old(member, s)) if r]
        if not reasons or not db.flag_fake_teen(s.guild_id, member.id, reasons[0]):  # or a mod cleared them
            return False
        # a teen role or a teen age they said is worth a look. teen by default, it's just an unverified adult
        if self._claimed_oldest(member, s):
            self._background(self._fake_teen_case(member, s, reasons))
        return True

    @staticmethod
    def _fake_why(reason: str, message: discord.Message | None = None) -> str:
        kind, _, rest = reason.partition(":")
        if kind == "age":
            where = f" in {message.channel.mention} · [Jump to Message]({message.jump_url})" if message else ""
            return f"- Said they're {rest}{where}"
        if kind == "elsewhere":
            return (f"- Said they're {rest} in another server that uses Lookout" if rest != "18" else
                    "- Said they're 18 or older in another server that uses Lookout")
        if kind == "account":
            years, oldest = (int(x) for x in rest.split(":"))
            return (f"- Their account is {years} years old. If they're {oldest} or younger, they made it at "
                    f"{oldest - years} or younger.")
        return "- Lookout caught them sending inappropriate messages to teens in other servers."

    async def _fake_teen_case(self, member: discord.Member, s: db.GuildSettings, reasons: list[str],
                              message: discord.Message | None = None) -> None:
        guild = member.guild
        if db.has_case(guild.id, member.id, "age"):
            return
        teen = s.role_ids("young_roles") | s.role_ids("older_roles")
        role = next((r for r in member.roles if r.id in teen), None)
        who = ui_kit.member_line(member).split("\n", 1)
        who[0] += f" · has {role.mention}" if role else f" · {self._under_18(member, s)[1:-1]}" if s.auto_ages else ""
        buttons = action_buttons(guild.id, message.channel.id if message else None, member.id)
        buttons.insert(0, self._btn("They're a Teen", "realteen", member.id))
        await self._case(guild, s, member.id, "age", "Not Really a Teen?", [
            ("member", "Who", "\n".join(who)),
            ("warning", "Why Lookout Thinks So", "\n".join(self._fake_why(r, message) for r in reasons)),
            ("staff", "What Changes Now", "Lookout checks their messages to teens like an adult's. Nothing looks "
                                          "different to them."),
        ], footer="If they really are a teen, press They're a Teen. Lookout can't change roles.", buttons=buttons,
            thumbnail=member.display_avatar.url, evidence=message.jump_url if message else None,
            summary="Has a teen role but probably isn't a teen")

    # auto-detect. members without an age role who say their age get counted as it. saying an adult age sticks:
    # an adult who later says they're 15 stays an adult, and the mods hear about it
    def _learn_age(self, message: discord.Message, member: discord.Member, age: int, source: str, s) -> None:
        if member.id in self.verified(s.guild_id) or self._role_band(member, s):
            return  # their role or the 18+ button already says
        ages = self.ages(s.guild_id)
        old, before = ages.get(member.id), self.seen_age(member, s)
        if old and old[1] == "mod":
            return  # a mod already decided
        if age < 18 and old and before is not None and before >= 18:
            if s.age_check:
                self._background(self._two_ages(message, member, old, age, s))
            return
        place = f"{message.channel.id}:{message.id}"
        ages[member.id] = (age, source, place, db.save_seen_age(s.guild_id, member.id, age, source, place))
        if age < 18 and (before is None or before >= 18):
            db.bump("teens_spotted")
            if message.reference:
                self._background(self._asked_age(message, member, s))

    # they said their age in a reply to someone asking it. that's a warning sign for whoever asked
    async def _asked_age(self, message: discord.Message, teen: discord.Member, s) -> None:
        ref = message.reference.resolved if message.reference else None
        if not isinstance(ref, discord.Message) or ref.author.bot or ref.author.id == teen.id:
            return
        if soft_sign(ref.content or "") != "probe":
            return
        guild = message.guild
        asker = await self._fetch(guild, ref.author.id)
        if asker is None or self.exempt(asker) or self.band(asker, s) != ADULT or not self._sure_adult(asker, s):
            return
        if self._once(("soft", guild.id, asker.id, teen.id, "probe"), 86400):
            self._signal(guild.id, asker.id, "probe", teen.id, f"{ref.channel.id}:{ref.id}")
            await self._check_risk(guild, asker, s)

    async def _two_ages(self, message: discord.Message, member: discord.Member, old: tuple, age: int, s) -> None:
        guild = message.guild
        if not self._once(("twoages", guild.id, member.id), 86400) or db.has_case(guild.id, member.id, "age"):
            return
        old_age, _, place, said_at = old
        ch, _, mid = (place or "").partition(":")
        before = f"- Said they're **{old_age}** <t:{int(said_at)}:R>" + (
            f" in <#{ch}> · [Jump to It](https://discord.com/channels/{guild.id}/{ch}/{mid})" if mid else "")
        buttons = action_buttons(guild.id, message.channel.id, member.id)
        buttons.insert(0, self._btn("They're a Teen", "realteen", member.id, age))
        await self._case(guild, s, member.id, "age", "Said Two Different Ages", [
            ("member", "Who", ui_kit.member_line(member)),
            ("report", "What They Said", f"{before}\n- Now says they're **{age}** in {message.channel.mention} · "
                                         f"[Jump to It]({message.jump_url})"),
            ("warning", "Why It Matters", "Adults sometimes say they're younger to get close to teens. Lookout keeps "
                                          "checking their messages to teens like an adult's."),
        ], footer="If they really are a teen, press They're a Teen.", buttons=buttons,
            thumbnail=member.display_avatar.url, evidence=message.jump_url,
            summary=f"Said they're {old_age}, then said they're {age}")

    # a teen said their age in chat. one DM per server, with a button to remove that message
    def _age_shared(self, message: discord.Message, member: discord.Member, s) -> None:
        if not s.nick_check or s.scanning or not db.age_tip_once(s.guild_id, member.id):
            return
        tips = s.tips_dm and db.first_nudge(s.guild_id, member.id)  # one DM with both, not two
        self._background(self._age_tip(message, member, s, tips))

    async def _age_tip(self, message: discord.Message, member: discord.Member, s, tips: bool) -> None:
        guild = message.guild
        name = discord.utils.escape_markdown(guild.name)
        can_remove = message.channel.permissions_for(guild.me).manage_messages
        buttons = [self._btn("Remove It", "agedel", guild.id, message.channel.id, message.id, member.id),
                   self._btn("Keep It", "agekeep", guild.id, member.id)] if can_remove else []
        sections, more = self._tips_parts() if tips else ([], [])
        view = ui_kit.card(
            ui_kit.titled(ui_kit.mascot("watching", "warning"), "You Shared Your Age"),
            f"You said your age in {message.channel.mention}. Everyone in **{name}** can see it, and search for it.",
            ("Good to Know", "Some people search servers for messages like that to find teens. It's safer to keep "
                             "your age between you and people you trust."
             + (" Lookout can remove it for you." if can_remove else
                " You can delete it: hover over the message and press the trash icon.")),
            *sections, subtitle=f"From {name}", footer="You'll only get this message once.", buttons=buttons + more)
        try:
            await member.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass    # DMs closed

    # age mismatch, once per member
    async def _check_age(self, message: discord.Message, member: discord.Member, age: int, s) -> None:
        guild = message.guild
        band = self._role_band(member, s)
        if age >= 18 and band != ADULT and self.band(member, s) == TEEN:
            if not db.flag_fake_teen(guild.id, member.id, f"age:{age}"):
                return
            if band == TEEN:  # with teen by default it's just an adult who didn't verify, no need to tell the mods
                old = self._too_old(member, s)
                await self._fake_teen_case(member, s, [f"age:{age}"] + ([old] if old else []), message)
            return
        if not (band == ADULT and age <= 17):
            return
        if not self._once(("age", guild.id, member.id), 86400) or db.has_case(guild.id, member.id, "age"):
            return
        role = next((r for r in member.roles if r.id in s.role_ids("adult_roles")), None)
        who = ui_kit.member_line(member).split("\n", 1)
        who[0] += f" · has {role.mention}" if role else ""
        buttons = action_buttons(guild.id, message.channel.id, member.id)
        buttons = buttons[:buttons.index(None)]  # a teen with the wrong role doesn't need warn or timeout
        buttons.insert(2, self._btn("False Alarm", "agefalse", member.id))
        await self._case(guild, s, member.id, "age", "Age Mismatch", [
            ("member", "Who", "\n".join(who)),
            ("report", "What They Said", f"{_said(message.content)}\n-# In {message.channel.mention} · "
                                         f"[Jump to Message]({message.jump_url})"),
            ("warning", "Why It Matters", f"If they're {age}, Lookout treats them as an adult, so messages adults send "
                                          "them aren't checked. Ask them, and switch their role if it's wrong."),
        ], footer="Lookout can't change roles. False Alarm closes this.", buttons=buttons,
            thumbnail=member.display_avatar.url, evidence=message.jump_url, report=False,
            summary=f"Says they're {age} but has an adult role")

    # alts

    async def _check_alt(self, member: discord.Member, s: db.GuildSettings) -> None:
        joined = member.joined_at.timestamp() if member.joined_at else 0
        # leaves get noticed late, so the alt can look like it joined first
        leavers = [old for old in db.recent_leavers(member.guild.id, time.time() - ALT_DAYS * 86400)
                   if old["user_id"] != member.id and joined > old["left_at"] - 3600]
        if not leavers:
            return
        picture = None
        if member.guild_avatar or member.avatar:
            try:
                picture = avatar_print(await member.display_avatar.replace(size=64, static_format="png").read())
            except (discord.HTTPException, OSError):
                pass
        new_account = (discord.utils.utcnow() - member.created_at).days < NEW_ACCOUNT_DAYS
        for old in leavers:
            why = []
            if same_picture(picture, old["avatar"]):
                why.append("Same profile picture")
            if any(similar_name(a, b) for a in (member.name, member.global_name, member.nick)
                   for b in (old["name"], old["display"])):
                why.append(f"Similar name: @{old['name']} and @{member.name}")
            if not why or (why == [why[0]] and why[0].startswith("Similar") and not new_account):
                continue
            if db.alt_alert_once(member.guild.id, member.id):
                gap = joined - old["left_at"]
                why.append(f"Joined {_gap(gap)} after <@{old['user_id']}> left" if gap > 3600
                           else f"Joined around when <@{old['user_id']}> left")
                await self._alt_case(member, s, old, why)
            return

    async def _alt_case(self, member: discord.Member, s: db.GuildSettings, old: dict, why: list[str]) -> None:
        left = f"left <t:{int(old['left_at'])}:R>" + (f" during case #{old['case_no']}" if old["case_no"] else "")
        info = ui_kit.kv([
            ("New Account", f"<@{member.id}> · created <t:{int(member.created_at.timestamp())}:R>"),
            ("Looks Like", f"<@{old['user_id']}> · " + ("kept away from teens, " if old["restricted"] else "") + left),
        ])
        buttons = action_buttons(member.guild.id, None, member.id)
        ui_kit.add_action(buttons, "Keep Away From Teens", "Their replies and mentions to teens get removed",
                          f"{PREFIX}:keepaway:{member.id}")
        await self._case(member.guild, s, member.id, "teen", "Possible Alt Account",
                         [("member", "Who", info), ("warning", "Why Lookout Thinks So", "\n".join(f"- {w}" for w in why))],
                         footer="Take a look before acting. Keep Away From Teens gives the new account the same "
                                "restriction.",
                         buttons=buttons, thumbnail=member.display_avatar.url)  # a guess, so no ping

    # lookout network. only removed replies and mentions count (not "nearby"), never anything a mod does,
    # and only from servers that have had lookout a while, so a server made yesterday can't put anyone on it

    def _share(self, message: discord.Message, adult: discord.Member, teen: discord.Member, s) -> None:
        if s.network and self._can_share(message.guild):
            db.add_network_catch(message.guild.id, adult.id, teen.id, message.id, message.content)

    # going after teens in several servers. each server only sees one "how old are you" or "add me on roblox",
    # lookout sees the same account doing it everywhere. warning signs only count from servers that share
    async def _fishing_check(self, message: discord.Message, member: discord.Member, s) -> None:
        guild, shared = member.guild, self._sharing()
        rows = [(g, kind, teen) for g, kind, teen in db.network_signals(member.id, skip_guild=guild.id) if g in shared]
        servers, teens = {g for g, _, _ in rows}, {t for _, _, t in rows}
        serious = {kind for _, kind, _ in rows} - {"probe"}
        if len(servers) < FISHING_SERVERS or len(teens) < FISHING_TEENS or not (serious or len(teens) >= 5):
            if len({g for g, _, _ in db.network_signals(member.id)}) < 2:
                self._multi_members().discard(member.id)  # old signs ran out
            return
        if db.network_record(member.id, skip_guild=guild.id)["listed"]:
            return  # Caught in Other Servers already covers them
        if not db.fishing_alert_once(guild.id, member.id):
            return
        teen_here = s.teen_safety and self.band(member, s) == TEEN
        if teen_here:  # counted as a teen here, so check them like an adult
            db.flag_fake_teen(guild.id, member.id, "network")
        lines = [f"- {label} · {n} teen{'' if n == 1 else 's'}" for kind, label in FISHING_LABELS.items()
                 if (n := len({t for _, k, t in rows if k == kind}))]
        here = len({sig["other_id"] for sig in db.signals(guild.id, member.id) if sig["kind"] in db.FISHING_KINDS})
        buttons = action_buttons(guild.id, message.channel.id, member.id)
        ui_kit.add_action(buttons, "Keep Away From Teens", "Their replies and mentions to teens get removed",
                          f"{PREFIX}:netkeep:{member.id}")
        await self._case(guild, s, member.id, "teen", "Going After Teens in Other Servers", [
            ("member", "Who", ui_kit.member_line(member)),
            ("warning", "What Lookout Saw",
             f"In the last 2 weeks, in **{len(servers)} other servers** that use Lookout, this account went after "
             f"**{len(teens)} different teens**:\n" + "\n".join(lines)
             + ("\n**Lookout counted them as a teen here,** so it now checks their messages to teens like an adult's."
                if teen_here else "")
             + "\n-# One of these alone looks normal. The same account doing it in server after server doesn't. "
               "Lookout doesn't say which servers, who the teens were, or what was said."),
            ("cases", "Here", (f"Already showed warning signs toward {here} teen{'' if here == 1 else 's'} here. "
                               if here else "Nothing here yet. ")
             + f"They just sent a message in {message.channel.mention}. [Jump to It]({message.jump_url})"),
        ], footer="A heads-up from the Lookout Network, not proof. Watch how they act around teens here, and keep it "
                  "inside the mod team.",
            buttons=buttons, urgent=bool(serious & {"contact", "flirty", "teen_report", "gift"}),
            thumbnail=member.display_avatar.url, evidence=message.jump_url,
            summary=f"Went after {len(teens)} teens in {len(servers)} other servers")
        if not s.scanning:  # a scan only saves it for the report
            db.bump("fishing_alerts")

    async def _network_check(self, message: discord.Message, member: discord.Member, s) -> None:
        guild = member.guild
        record = db.network_record(member.id, skip_guild=guild.id)
        if not record["listed"] or not db.network_alert_once(guild.id, member.id):
            return
        n, last = len(record["catches"]), int(record["catches"][0]["created_at"])
        teen_here = s.teen_safety and self.band(member, s, explicit=True) == TEEN
        if s.teen_safety and self.band(member, s) == TEEN:  # counted as a teen here, so check them like an adult
            db.flag_fake_teen(guild.id, member.id, "network")
        buttons = action_buttons(guild.id, message.channel.id, member.id)
        ui_kit.add_action(buttons, "Keep Away From Teens", "Their replies and mentions to teens get removed",
                          f"{PREFIX}:netkeep:{member.id}")
        await self._case(guild, s, member.id, "teen", "Caught in Other Servers", [
            ("member", "Who", ui_kit.member_line(member)),
            ("warning", "What Happened Before",
             f"In **{record['servers']} other servers** that use Lookout, its filter removed **{n} inappropriate "
             f"message{'' if n == 1 else 's'}** this account sent to **{record['teens']} different teens**. The last "
             f"one was <t:{last}:R>." + ("\n**Lookout counted them as a teen here,** so it now checks their messages "
                                         "to teens like an adult's." if teen_here else "")
             + "\n-# Lookout doesn't say which servers, who the teens were, or what the messages said."),
            ("cases", "Why Now", f"They just sent a message in {message.channel.mention}. "
                                 f"[Jump to It]({message.jump_url})"),
        ], footer="This is a heads-up from the Lookout Network, not proof. Lookout hasn't done anything to them here. "
                  "Keep it inside the mod team.",
            buttons=buttons, urgent=True, thumbnail=member.display_avatar.url, evidence=message.jump_url,
            summary=f"Caught by Lookout in {record['servers']} other servers")
        if not s.scanning:
            db.bump("network_alerts")

    # report to discord

    async def _discord_report(self, interaction: discord.Interaction) -> None:
        case = db.case_by_message(interaction.message.id)
        if case is None:
            return await interaction.response.send_message(view=ui_kit.note("This case isn't saved anymore."),
                                                           ephemeral=True)
        guild, target = interaction.guild, case["target_id"]
        user = None
        if target:
            try:
                user = self.bot.get_user(target) or await self.bot.fetch_user(target)
            except discord.HTTPException:
                pass
        teens = sorted(db.teen_reporters(guild.id, target)) if target else []
        reasons = score(guild.id, target)[1] if target else []
        links = (case["evidence"] or "").split()
        if target:
            links += db.message_links(guild.id, target, case["created_at"] - db.SIGNAL_DAYS * 86400)
        links = list(dict.fromkeys(links))[:10]

        def plain(text):  # keep it inside the code block
            return str(text).replace("`", "'")
        reported_by = (f"{len(teens)} member{'' if len(teens) == 1 else 's'} under 18 (ID {', '.join(map(str, teens))})"
                       if teens else "Lookout's teen safety checks")
        lines = [f"Server: {plain(guild.name)} (ID {guild.id})",
                 f"Reported account: {plain(user.name) if user else 'unknown'} (ID {target})",
                 f"Reported by: {reported_by}",
                 f"What happened: {plain(case['summary'])}" + (f". Warning signs: {plain('; '.join(reasons))}"
                                                               if reasons else ""),
                 f"Case opened: {time.strftime('%b %d, %Y, %H:%M UTC', time.gmtime(case['created_at']))}",
                 "Messages: " + ("\n" + "\n".join(links) if links else "none saved")]
        view = ui_kit.card(
            ui_kit.titled("report", f"Report to Discord · Case #{case['case_no']}"),
            "Discord's safety team can act on the account everywhere on Discord. Copy this into their form.",
            ("Copy This", "```\n" + "\n".join(lines) + "\n```"),
            footer="Send links and IDs only. Never send copies of anything harmful.",
            buttons=[discord.ui.Button(label="Open Discord's Report Form", style=discord.ButtonStyle.link,
                                       url="https://dis.gd/request")])
        await interaction.response.send_message(view=view, ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())

    # buttons

    @staticmethod
    def _btn(label: str, action: str, *ids) -> discord.ui.Button:
        return discord.ui.Button(label=label, custom_id=":".join([PREFIX, action, *map(str, ids)]))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type not in (discord.InteractionType.component, discord.InteractionType.modal_submit):
            return
        parts = ui_kit.custom_id(interaction).split(":")
        if parts[0] != PREFIX or len(parts) < 3:
            return
        action, ids = parts[1], [int(p) for p in parts[2:] if p.isdigit()]
        if action in ("taccept", "tdecline", "tremove", "tapprove", "tdeny"):  # old /trust buttons
            return await interaction.response.send_message(
                view=ui_kit.note("This button doesn't work anymore."),
                ephemeral=interaction.guild is not None)
        if action in ("tell", "tellmore", "tellsend", "nick", "nickkeep", "agedel", "agekeep"):  # DM buttons
            return await self._from_dm(interaction, action, ids)

        user = interaction.user
        if interaction.guild is None or not isinstance(user, discord.Member) or not self.bot.is_staff(user):
            return await interaction.response.send_message(view=ui_kit.note("Only mods can use these."), ephemeral=True)
        guild = interaction.guild
        if action == "lift":
            db.set_restricted(guild.id, ids[0], False)
            db.log(guild.id, ids[0], "teen_unrestricted", "Lifted by the mods", actor_id=user.id)
            return await self._stamp(interaction, f"Lifted by {user.mention}. They're no longer kept away from teens.",
                                     state="Lifted")
        if action == "keep":
            return await self._stamp(interaction, f"Kept by {user.mention}. Press Lift anytime to end it.",
                                     buttons=[self._btn("Lift", "lift", ids[0]),
                                              self._btn("Report to Discord", "tns", ids[0])], state="Kept")
        if action in ("keepaway", "netkeep"):  # alt alert, network heads-up
            db.set_restricted(guild.id, ids[0], True)
            db.log(guild.id, ids[0], "teen_restricted", "Kept away from teens as a possible alt account"
                   if action == "keepaway" else "Kept away from teens after a Lookout Network heads-up",
                   actor_id=user.id)
            return await self._stamp(interaction, f"Kept away from teens by {user.mention}. Press Lift anytime to "
                                                  "end it.",
                                     buttons=[self._btn("Lift", "lift", ids[0]),
                                              discord.ui.Button(label="Resolve", custom_id=_cid("resolve", ids[0])),
                                              self._btn("Report to Discord", "tns", ids[0])],
                                     state="Kept Away")
        if action == "falsealarm" and len(ids) >= 2:
            target, message_id = ids[0], ids[1]
            db.false_alarm(guild.id, target, message_id)
            db.bump("false_alarms")
            if interaction.message:
                db.resolve_case(interaction.message.id, user.id, "False alarm")
            self._flirty_cases.pop((guild.id, target), None)
            return await self._stamp(interaction, f"Marked as a false alarm by {user.mention}. It won't count toward "
                                                  "their Warning Signs.", state="False Alarm")
        if action == "agefalse":
            if interaction.message:
                db.resolve_case(interaction.message.id, user.id, "False alarm")
            return await self._stamp(interaction, f"Marked as a false alarm by {user.mention}.", state="False Alarm")
        if action == "realteen":  # "not really a teen?" was wrong. stays cleared, lookout won't flag them again here
            db.clear_fake_teen(guild.id, ids[0])
            if len(ids) > 1:  # said two different ages: go with the teen one, and stop guessing from chat
                self.ages(guild.id)[ids[0]] = (ids[1], "mod", None,
                                               db.save_seen_age(guild.id, ids[0], ids[1], "mod", None))
            db.log(guild.id, ids[0], "teen_cleared", "A mod said they really are a teen", actor_id=user.id)
            if interaction.message:
                db.resolve_case(interaction.message.id, user.id, "They really are a teen")
            return await self._stamp(interaction, f"Marked as a teen by {user.mention}. Lookout treats them as a teen "
                                                  "again.", state="Cleared")
        if action == "tns":
            return await self._discord_report(interaction)

    async def _from_dm(self, interaction: discord.Interaction, action: str, ids: list[int]):
        guild = self.bot.get_guild(ids[0]) if ids else None
        if guild is None:
            return await interaction.response.edit_message(view=ui_kit.note("That server isn't available anymore."))
        s = db.get_settings(guild.id)
        me = interaction.user
        if action == "tellmore":  # Tell the Mods button
            modal = discord.ui.Modal(title="Tell the Mods", custom_id=f"{PREFIX}:tellsend:{guild.id}:{ids[1]}")
            modal.add_item(discord.ui.TextInput(label="Anything you want them to know? (optional)", custom_id="text",
                                                style=discord.TextStyle.paragraph, required=False, max_length=500,
                                                placeholder="They won't say it was you."))
            return await interaction.response.send_modal(modal)
        if action == "tellsend":
            adult_id = ids[1]
            text = ""
            for row in (interaction.data or {}).get("components") or []:
                for field in row.get("components") or [row.get("component") or {}]:
                    if field.get("custom_id") == "text":
                        text = (field.get("value") or "").strip()
            await interaction.response.edit_message(view=ui_kit.note(
                "**Sent to the mods.** They won't say it was you.", buttons=[ui_kit.block_button()]))
            recent = self._flirty_cases.get((guild.id, adult_id))
            case = db.case_by_message(recent[0]) if recent else None
            line = f"**<@{me.id}> pressed Tell the Mods.** Don't tell the member who reported it." + (
                f"\n{_quote(text)}" if text else "")
            if case:
                await self._note_on_case(guild, case, line)
            else:
                await self._case(guild, s, adult_id, "teen", "A Teen Asked for Help", [
                    ("member", "Who", f"<@{adult_id}>"), ("report", "What Happened", line)],
                    footer="If a minor might be at risk, report it to Discord too: https://dis.gd/request",
                    buttons=action_buttons(guild.id, None, adult_id), urgent=True)
            teen, adult = await self._fetch(guild, me.id), await self._fetch(guild, adult_id)
            if adult and teen:
                await self.reported(guild, teen, adult)
            return
        if action == "tell":  # old heads-up button
            adult_id, channel_id = ids[1], ids[2]
            # old buttons don't have the message id
            evidence = f"https://discord.com/channels/{guild.id}/{channel_id}/{ids[3]}" if len(ids) > 3 else None
            await interaction.response.edit_message(view=ui_kit.note(
                "**Sent to the mods.** Thanks for telling them. They won't say it was you."))
            teen, adult = await self._fetch(guild, me.id), await self._fetch(guild, adult_id)
            await self._case(guild, s, adult_id, "teen", "A Teen Asked for Help", [
                ("member", "Who", ui_kit.member_line(adult) if adult else f"<@{adult_id}> (left the server)"),
                ("report", "What Happened", f"<@{me.id}> got a message from <@{adult_id}> in <#{channel_id}> and "
                                            "pressed **Tell the Mods**. Don't tell the member who reported it."),
            ], footer="If a minor might be at risk, report it to Discord too: https://dis.gd/request",
               buttons=action_buttons(guild.id, channel_id, adult_id), urgent=True, evidence=evidence)
            if adult and teen:
                await self.reported(guild, teen, adult)
            return

        if action in ("agedel", "agekeep"):  # "you shared your age"
            return await self._remove_age(interaction, guild, action, ids)

        member = await self._fetch(guild, me.id) if ids[1:] == [me.id] else None
        if action == "nickkeep" or member is None:
            return await interaction.response.edit_message(view=ui_kit.note(
                "Okay, it stays. You can change it anytime in your server profile."))
        found, clean = nickname_info(member.display_name)
        if not found or not clean:
            return await interaction.response.edit_message(view=ui_kit.note(
                "Your nickname doesn't show any personal info anymore, so there's nothing to change."))
        try:
            await member.edit(nick=clean[:32], reason="Lookout: the member asked to take personal info out of "
                                                      "their nickname")
        except discord.HTTPException:
            return await interaction.response.edit_message(view=ui_kit.note(
                "Lookout couldn't change it. You can change it yourself in your server profile."))
        return await interaction.response.edit_message(view=ui_kit.note(
            f"Your nickname in **{discord.utils.escape_markdown(guild.name)}** is now "
            f"**{discord.utils.escape_markdown(clean[:32])}**."), allowed_mentions=discord.AllowedMentions.none())

    # ids: guild, channel, message, the teen. only the teen who said it can remove it
    @staticmethod
    async def _remove_age(interaction: discord.Interaction, guild: discord.Guild, action: str, ids: list[int]):
        yourself = "You can delete it yourself: hover over the message and press the trash icon."
        if action == "agekeep" or len(ids) < 4 or ids[3] != interaction.user.id:
            return await interaction.response.edit_message(view=ui_kit.note(f"Okay, it stays. {yourself}"))
        channel = guild.get_channel_or_thread(ids[1])
        try:
            message = await channel.fetch_message(ids[2])
            mine = message.author.id == interaction.user.id
            if mine:
                await message.delete()
        except discord.NotFound:
            return await interaction.response.edit_message(view=ui_kit.note("**It's already gone.**"))
        except (discord.HTTPException, AttributeError):  # missing permission, or the channel is gone
            mine = False
        if not mine:
            return await interaction.response.edit_message(view=ui_kit.note(f"Lookout couldn't remove it. {yourself}"))
        await interaction.response.edit_message(view=ui_kit.note(
            f"**Removed.** Your message in {channel.mention} is gone."))

    @staticmethod
    async def _fetch(guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        return member

    async def _stamp(self, interaction: discord.Interaction, text: str, buttons: list | None = None,
                     state: str | None = None):
        view = discord.ui.LayoutView.from_message(interaction.message, timeout=None)
        box = ui_kit.action_box(view)
        ui_kit.remove_actions(box)
        if state:
            ui_kit.set_status(view, state, f"by {interaction.user.mention}")
        box.add_item(discord.ui.TextDisplay(ui_kit.tidy(f"-# {text}")))
        if buttons:
            box.add_item(discord.ui.ActionRow(*buttons))
        await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(TeenSafety(bot))
