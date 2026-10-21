import sqlite3
import time
from dataclasses import dataclass

import config

_conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER, user_id INTEGER, actor_id INTEGER,
        channel_id INTEGER, kind TEXT, detail TEXT, created_at REAL
    );
    CREATE INDEX IF NOT EXISTS ev_user ON events (guild_id, user_id);
    CREATE INDEX IF NOT EXISTS ev_chan ON events (guild_id, channel_id, kind, created_at);
    CREATE TABLE IF NOT EXISTS alerts (
        message_id INTEGER PRIMARY KEY, guild_id INTEGER,
        claimed_by INTEGER, claimed_name TEXT, created_at REAL
    );
    CREATE TABLE IF NOT EXISTS alert_actions (
        message_id INTEGER, text TEXT, created_at REAL
    );
    CREATE TABLE IF NOT EXISTS appeals (
        event_id INTEGER PRIMARY KEY, guild_id INTEGER, user_id INTEGER, text TEXT,
        status TEXT DEFAULT 'pending', decided_by INTEGER, created_at REAL
    );
    CREATE TABLE IF NOT EXISTS reporters (
        message_id INTEGER, user_id INTEGER, created_at REAL,
        PRIMARY KEY (message_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS icons (
        key TEXT PRIMARY KEY, hash TEXT, emoji_id INTEGER
    );
    -- /totals counters, just numbers
    CREATE TABLE IF NOT EXISTS totals (
        key TEXT PRIMARY KEY, value INTEGER
    );
    CREATE TABLE IF NOT EXISTS guilds (
        guild_id INTEGER PRIMARY KEY,
        staff_channel_id INTEGER,
        staff_role_id INTEGER,
        image_scan INTEGER DEFAULT 1
    );
    DROP TABLE IF EXISTS keywords;

    -- teen safety
    CREATE TABLE IF NOT EXISTS teen_signals (
        guild_id INTEGER, user_id INTEGER, kind TEXT, other_id INTEGER, detail TEXT, created_at REAL
    );
    CREATE INDEX IF NOT EXISTS ts_user ON teen_signals (guild_id, user_id, created_at);
    CREATE TABLE IF NOT EXISTS restrictions (
        guild_id INTEGER, user_id INTEGER, status TEXT, created_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS risk_flags (
        guild_id INTEGER, user_id INTEGER, score INTEGER, test INTEGER, flagged_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS nudged (
        guild_id INTEGER, user_id INTEGER, created_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS nick_tips (
        guild_id INTEGER, user_id INTEGER, nick TEXT, created_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS leavers (
        guild_id INTEGER, user_id INTEGER, name TEXT, display TEXT, avatar TEXT, case_no INTEGER,
        restricted INTEGER, left_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS alt_alerts (
        guild_id INTEGER, user_id INTEGER, created_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );
    -- small things to remember between restarts, like a hash of the last synced commands
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT
    );
    -- pressed the 18+ button in an age-restricted channel. no role, nobody else can see it
    CREATE TABLE IF NOT EXISTS verified_adults (
        guild_id INTEGER, user_id INTEGER, created_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );
    -- /trivia points, per server
    CREATE TABLE IF NOT EXISTS trivia_points (
        guild_id INTEGER, user_id INTEGER, points INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );
    -- lookout network: messages to teens the filter removed, shared between servers.
    -- stays when lookout leaves a server, otherwise a server could kick it to wipe someone
    CREATE TABLE IF NOT EXISTS network_catches (
        guild_id INTEGER, user_id INTEGER, teen_id INTEGER, message_id INTEGER, text TEXT, created_at REAL,
        PRIMARY KEY (guild_id, message_id)
    );
    CREATE INDEX IF NOT EXISTS nc_user ON network_catches (user_id, created_at);
    CREATE TABLE IF NOT EXISTS network_alerts (
        guild_id INTEGER, user_id INTEGER, created_at REAL,
        PRIMARY KEY (guild_id, user_id)
    );

    -- removed features (age-aware voice, /trust, first-contact heads-up, selfie reply block)
    DROP TABLE IF EXISTS first_contacts;
    DROP TABLE IF EXISTS trust;
    DROP TABLE IF EXISTS age_seen;
    DROP TABLE IF EXISTS teen_photos;
    DELETE FROM teen_signals WHERE kind NOT IN ('flirty', 'contact', 'teen_report');
    """
)
_conn.execute("INSERT OR IGNORE INTO totals VALUES ('since', ?)", (int(time.time()),))
_conn.commit()

# columns added after the first version. old dbs also have a few unused ones left
_GUILD_COLS = {"weekly_summary": "INTEGER DEFAULT 1", "summary_sent_at": "REAL",
               "reminder_minutes": "INTEGER DEFAULT 5", "appeals": "INTEGER DEFAULT 1",
               "report_updates": "INTEGER DEFAULT 1", "panel_channel_id": "INTEGER",
               "young_roles": "TEXT", "older_roles": "TEXT", "adult_roles": "TEXT",
               "no_age_role": "TEXT DEFAULT 'adult'", "contact_filter": "INTEGER DEFAULT 1",
               "teen_restrict": "INTEGER DEFAULT 1", "risk_mode": "TEXT DEFAULT 'test'",
               "risk_test_until": "REAL", "last_case": "INTEGER", "tips_dm": "INTEGER DEFAULT 1",
               "selfie_guard": "INTEGER DEFAULT 1", "nick_check": "INTEGER DEFAULT 1",
               "alt_catcher": "INTEGER DEFAULT 1", "tips_channel_id": "INTEGER", "flirty_filter": "INTEGER DEFAULT 1",
               "checkup_score": "INTEGER", "checkup_at": "REAL", "filter_languages": "TEXT DEFAULT 'en'",
               "age_check": "INTEGER DEFAULT 1", "adult_channel_id": "INTEGER", "trivia_channel_id": "INTEGER",
               "network": "INTEGER DEFAULT 1"}
_have = {r[1] for r in _conn.execute("PRAGMA table_info(guilds)")}
for _col, _type in _GUILD_COLS.items():
    if _col not in _have:
        _conn.execute(f"ALTER TABLE guilds ADD COLUMN {_col} {_type}")
_conn.commit()

_ALERT_COLS = {"case_no": "INTEGER", "kind": "TEXT", "channel_id": "INTEGER", "staff_channel_id": "INTEGER",
               "target_id": "INTEGER", "summary": "TEXT", "status": "TEXT DEFAULT 'open'",
               "resolved_by": "INTEGER", "resolved_at": "REAL", "note": "TEXT", "review_url": "TEXT",
               "screenshot_url": "TEXT", "claimed_at": "REAL", "reminded_at": "REAL",
               "evidence": "TEXT", "target_left_at": "REAL"}
_have = {r[1] for r in _conn.execute("PRAGMA table_info(alerts)")}
for _col, _type in _ALERT_COLS.items():
    if _col not in _have:
        _conn.execute(f"ALTER TABLE alerts ADD COLUMN {_col} {_type}")
# message id, for the report to discord links
if "message_id" not in {r[1] for r in _conn.execute("PRAGMA table_info(events)")}:
    _conn.execute("ALTER TABLE events ADD COLUMN message_id INTEGER")
_conn.commit()

FLAG_KINDS = ("vc_report", "image_flagged", "speech_flagged", "voicemsg_flagged")


# settings

@dataclass
class GuildSettings:
    guild_id: int
    staff_channel_id: int | None = None
    staff_role_id: int | None = None
    image_scan: bool = True
    weekly_summary: bool = True
    reminder_minutes: int = 5  # minutes, 0 = off
    appeals: bool = True                 # warned members can appeal from the DM
    report_updates: bool = True          # tell reporters by DM when their case is closed
    panel_channel_id: int | None = None  # where the Report button was last posted

    # teen safety. role lists are "id,id,id"
    young_roles: str | None = None       # teen roles
    older_roles: str | None = None  # old 16-17 roles
    adult_roles: str | None = None       # 18+
    no_age_role: str = "adult"           # members with no age role count as "adult", "teen" or "ignore"
    contact_filter: bool = True
    teen_restrict: bool = True  # keep away after 2 reports
    risk_mode: str = "test"              # Warning Signs: off, test or on
    risk_test_until: float | None = None
    tips_dm: bool = True                 # teens get the safety tips by DM, once
    selfie_guard: bool = True  # selfie tip
    nick_check: bool = True  # nickname check
    alt_catcher: bool = True
    tips_channel_id: int | None = None   # where the safety tips were last posted
    flirty_filter: bool = True  # message filter
    checkup_score: int | None = None     # how many Safety Checkup items passed last time
    checkup_at: float | None = None
    filter_languages: str = "en"         # the Message Filter's languages, like "en,es"
    age_check: bool = True               # age mismatch
    adult_channel_id: int | None = None  # where the 18+ button was posted
    trivia_channel_id: int | None = None  # the only channel /trivia runs in
    network: bool = True                 # lookout network: shares catches and gets heads-ups

    @property
    def configured(self) -> bool:
        return bool(self.staff_channel_id)

    def role_ids(self, field: str) -> set[int]:
        return {int(r) for r in (getattr(self, field) or "").split(",") if r.isdigit()}

    # teen by default: only an 18+ role, everyone without it is protected as a teen
    @property
    def teen_default(self) -> bool:
        return self.no_age_role == "teen"

    @property
    def teen_safety(self) -> bool:
        return bool(self.young_roles or self.older_roles
                    or (self.teen_default and (self.adult_roles or self.adult_channel_id)))

    @property
    def languages(self) -> tuple[str, ...]:
        return tuple(code for code in (self.filter_languages or "en").split(",") if code) or ("en",)

    def risk_now(self) -> str:
        if self.risk_mode == "test" and self.risk_test_until and time.time() > self.risk_test_until:
            return "on"
        return self.risk_mode


_COLS = ("staff_channel_id", "staff_role_id", "image_scan", "weekly_summary", "reminder_minutes",
         "appeals", "report_updates", "panel_channel_id", "young_roles", "older_roles", "adult_roles",
         "no_age_role", "contact_filter", "teen_restrict", "risk_mode", "risk_test_until", "tips_dm",
         "selfie_guard", "nick_check", "alt_catcher", "tips_channel_id", "flirty_filter", "checkup_score",
         "checkup_at", "filter_languages", "age_check", "adult_channel_id", "trivia_channel_id", "network")
_BOOLS = ("image_scan", "weekly_summary", "appeals", "report_updates", "contact_filter", "teen_restrict",
          "tips_dm", "selfie_guard", "nick_check", "alt_catcher", "flirty_filter", "age_check", "network")


def get_settings(guild_id: int) -> GuildSettings:
    row = _conn.execute(f"SELECT {', '.join(_COLS)} FROM guilds WHERE guild_id=?", (guild_id,)).fetchone()
    if not row:
        return GuildSettings(guild_id)
    s = GuildSettings(guild_id, *row)
    for name in _BOOLS:  # NULL in old rows means on
        setattr(s, name, getattr(s, name) is None or bool(getattr(s, name)))
    s.reminder_minutes = 5 if s.reminder_minutes is None else s.reminder_minutes
    s.no_age_role = s.no_age_role or "adult"
    s.risk_mode = s.risk_mode or "test"
    s.filter_languages = s.filter_languages or "en"
    return s


def save_settings(s: GuildSettings) -> None:
    values = [getattr(s, c) for c in _COLS]
    _conn.execute(
        f"INSERT INTO guilds (guild_id, {', '.join(_COLS)}) VALUES (?, {', '.join('?' * len(_COLS))})"
        f" ON CONFLICT(guild_id) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in _COLS)}",
        [s.guild_id, *values],
    )
    _conn.commit()


def forget_guild(guild_id: int) -> None:
    for table in ("alert_actions", "reporters"):
        _conn.execute(f"DELETE FROM {table} WHERE message_id IN (SELECT message_id FROM alerts WHERE guild_id=?)",
                      (guild_id,))
    # network_catches stay, see the table
    for table in ("alerts", "appeals", "events", "guilds", "trivia_points", "network_alerts") + TEEN_TABLES:
        _conn.execute(f"DELETE FROM {table} WHERE guild_id=?", (guild_id,))
    _conn.commit()


# member log

def log(guild_id, user_id, kind, detail="", channel_id=None, actor_id=None, message_id=None) -> int:
    cur = _conn.execute(
        "INSERT INTO events (guild_id, user_id, actor_id, channel_id, kind, detail, created_at, message_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, user_id, actor_id, channel_id, kind, detail[:1000], time.time(), message_id),
    )
    _conn.commit()
    return cur.lastrowid


def get_event(guild_id: int, event_id: int) -> dict | None:
    row = _conn.execute("SELECT kind, user_id, actor_id, detail, created_at FROM events WHERE guild_id=? AND id=?",
                        (guild_id, event_id)).fetchone()
    return dict(zip(("kind", "user_id", "actor_id", "detail", "created_at"), row)) if row else None


def count_flags(guild_id: int, user_id: int) -> int:
    q = f"SELECT COUNT(*) FROM events WHERE guild_id=? AND user_id=? AND kind IN ({','.join('?' * len(FLAG_KINDS))})"
    return _conn.execute(q, (guild_id, user_id, *FLAG_KINDS)).fetchone()[0]


def count_kind(guild_id: int, user_id: int, kind: str) -> int:
    return _conn.execute("SELECT COUNT(*) FROM events WHERE guild_id=? AND user_id=? AND kind=?",
                         (guild_id, user_id, kind)).fetchone()[0]


def history(guild_id: int, user_id: int, limit: int = 12) -> list[tuple]:
    return _conn.execute(
        "SELECT kind, detail, actor_id, channel_id, created_at, id FROM events"
        " WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT ?",
        (guild_id, user_id, limit),
    ).fetchall()


def purge_old() -> int:
    cutoff = time.time() - config.RETENTION_DAYS * 86400
    cur = _conn.execute("DELETE FROM events WHERE created_at < ?", (cutoff,))
    _conn.execute("DELETE FROM alerts WHERE created_at < ?", (cutoff,))
    _conn.execute("DELETE FROM alert_actions WHERE created_at < ?", (cutoff,))
    _conn.execute("DELETE FROM appeals WHERE created_at < ?", (cutoff,))
    _conn.execute("DELETE FROM reporters WHERE created_at < ?", (cutoff,))
    _conn.execute("DELETE FROM teen_signals WHERE created_at < ?", (time.time() - SIGNAL_DAYS * 86400,))
    _conn.execute("DELETE FROM risk_flags WHERE flagged_at < ?", (time.time() - SIGNAL_DAYS * 86400,))
    _conn.execute("DELETE FROM nick_tips WHERE created_at < ?", (cutoff,))
    _conn.execute("DELETE FROM leavers WHERE left_at < ?", (cutoff,))
    _conn.execute("DELETE FROM alt_alerts WHERE created_at < ?", (cutoff,))
    for table in ("network_catches", "network_alerts"):
        _conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (time.time() - NETWORK_DAYS * 86400,))
    _conn.commit()
    return cur.rowcount


# claims

def get_claim(message_id: int) -> tuple[int, str] | None:
    row = _conn.execute("SELECT claimed_by, claimed_name FROM alerts WHERE message_id=? AND claimed_by IS NOT NULL",
                        (message_id,)).fetchone()
    return (row[0], row[1]) if row else None


def set_claim(guild_id: int, message_id: int, user_id: int | None, name: str | None) -> None:
    now = time.time()
    _conn.execute(
        "INSERT INTO alerts (message_id, guild_id, claimed_by, claimed_name, created_at, claimed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(message_id) DO UPDATE SET claimed_by=excluded.claimed_by, claimed_name=excluded.claimed_name,"
        " claimed_at=excluded.claimed_at",
        (message_id, guild_id, user_id, name, now, now if user_id else None),
    )
    _conn.commit()


# claim reminder

def due_reminders(now: float) -> list[dict]:
    rows = _conn.execute(
        "SELECT a.message_id, a.guild_id, a.staff_channel_id, a.case_no, a.created_at, g.staff_role_id"
        " FROM alerts a JOIN guilds g ON g.guild_id = a.guild_id"
        " WHERE a.kind IN ('voice', 'report', 'teen', 'risk') AND a.case_no IS NOT NULL AND a.status='open' AND a.claimed_by IS NULL"
        " AND a.reminded_at IS NULL AND g.reminder_minutes > 0 AND a.created_at <= ? - g.reminder_minutes * 60",
        (now,)).fetchall()
    keys = ("message_id", "guild_id", "staff_channel_id", "case_no", "created_at", "staff_role_id")
    return [dict(zip(keys, r)) for r in rows]


def mark_reminded(message_id: int) -> None:
    _conn.execute("UPDATE alerts SET reminded_at=? WHERE message_id=?", (time.time(), message_id))
    _conn.commit()


def add_alert_action(message_id: int, text: str) -> None:
    _conn.execute("INSERT INTO alert_actions VALUES (?, ?, ?)", (message_id, text[:300], time.time()))
    _conn.commit()


def alert_actions(message_id: int) -> list[str]:
    return [r[0] for r in _conn.execute(
        "SELECT text FROM alert_actions WHERE message_id=? ORDER BY created_at", (message_id,)).fetchall()]


# cases

# take the number now so two alerts can't share one
def next_case_no(guild_id: int) -> int:
    _conn.execute("INSERT INTO guilds (guild_id) VALUES (?) ON CONFLICT(guild_id) DO NOTHING", (guild_id,))
    _conn.execute("UPDATE guilds SET last_case = MAX(COALESCE(last_case, 0),"
                  " (SELECT COALESCE(MAX(case_no), 0) FROM alerts WHERE guild_id=?)) + 1 WHERE guild_id=?",
                  (guild_id, guild_id))
    _conn.commit()
    return _conn.execute("SELECT last_case FROM guilds WHERE guild_id=?", (guild_id,)).fetchone()[0]


def create_case(guild_id: int, message_id: int, staff_channel_id: int, case_no: int, kind: str,
                channel_id: int | None, target_id: int | None, summary: str, evidence: str | None = None) -> None:
    _conn.execute(
        "INSERT INTO alerts (message_id, guild_id, created_at, case_no, kind, channel_id, staff_channel_id,"
        " target_id, summary, status, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)"
        " ON CONFLICT(message_id) DO UPDATE SET case_no=excluded.case_no, kind=excluded.kind,"
        " channel_id=excluded.channel_id, staff_channel_id=excluded.staff_channel_id,"
        " target_id=excluded.target_id, summary=excluded.summary, evidence=excluded.evidence",
        (message_id, guild_id, time.time(), case_no, kind, channel_id, staff_channel_id, target_id, summary[:300],
         evidence),
    )
    _conn.commit()


def case_by_message(message_id: int) -> dict | None:
    row = _conn.execute("SELECT guild_id, case_no, kind, target_id, summary, created_at, evidence, message_id,"
                        " staff_channel_id, status FROM alerts WHERE message_id=?", (message_id,)).fetchone()
    keys = ("guild_id", "case_no", "kind", "target_id", "summary", "created_at", "evidence", "message_id",
            "staff_channel_id", "status")
    return dict(zip(keys, row)) if row else None


def has_case(guild_id: int, user_id: int, kind: str) -> bool:
    return _conn.execute("SELECT 1 FROM alerts WHERE guild_id=? AND target_id=? AND kind=?",
                         (guild_id, user_id, kind)).fetchone() is not None


def update_case(message_id: int, **fields) -> None:
    allowed = {k: v for k, v in fields.items() if k in _ALERT_COLS}
    if allowed:
        _conn.execute(f"UPDATE alerts SET {', '.join(f'{k}=?' for k in allowed)} WHERE message_id=?",
                      (*allowed.values(), message_id))
        _conn.commit()


def resolve_case(message_id: int, mod_id: int, note: str) -> None:
    if not is_resolved(message_id):
        bump("handled")
    update_case(message_id, status="resolved", resolved_by=mod_id, resolved_at=time.time(), note=note[:500])


def is_resolved(message_id: int) -> bool:
    row = _conn.execute("SELECT status FROM alerts WHERE message_id=?", (message_id,)).fetchone()
    return bool(row and row[0] == "resolved")


_CASE_FIELDS = ("message_id", "case_no", "kind", "channel_id", "staff_channel_id", "target_id", "summary",
                "status", "claimed_by", "resolved_by", "resolved_at", "note", "review_url", "created_at",
                "screenshot_url")


def _case_row(row):
    return dict(zip(_CASE_FIELDS, row)) if row else None


def list_cases(guild_id: int, status: str = "all", member_id: int | None = None,
               limit: int = 6, offset: int = 0) -> tuple[list[dict], int]:
    where, args = ["guild_id=?", "case_no IS NOT NULL"], [guild_id]
    if status == "open":
        where.append("status='open' AND claimed_by IS NULL")
    elif status == "claimed":
        where.append("status='open' AND claimed_by IS NOT NULL")
    elif status == "resolved":
        where.append("status='resolved'")
    if member_id:
        where.append("target_id=?")
        args.append(member_id)
    w = " AND ".join(where)
    total = _conn.execute(f"SELECT COUNT(*) FROM alerts WHERE {w}", args).fetchone()[0]
    rows = _conn.execute(f"SELECT {', '.join(_CASE_FIELDS)} FROM alerts WHERE {w}"
                         " ORDER BY case_no DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
    return [_case_row(r) for r in rows], total


def case_counts(guild_id: int) -> dict:
    q = "SELECT COUNT(*) FROM alerts WHERE guild_id=? AND case_no IS NOT NULL AND "
    return {
        "open": _conn.execute(q + "status='open' AND claimed_by IS NULL", (guild_id,)).fetchone()[0],
        "claimed": _conn.execute(q + "status='open' AND claimed_by IS NOT NULL", (guild_id,)).fetchone()[0],
        "resolved": _conn.execute(q + "status='resolved'", (guild_id,)).fetchone()[0],
    }


def get_case(guild_id: int, case_no: int) -> dict | None:
    return _case_row(_conn.execute(f"SELECT {', '.join(_CASE_FIELDS)} FROM alerts WHERE guild_id=? AND case_no=?",
                                   (guild_id, case_no)).fetchone())


# weekly summary

def summary_sent_at(guild_id: int) -> float | None:
    row = _conn.execute("SELECT summary_sent_at FROM guilds WHERE guild_id=?", (guild_id,)).fetchone()
    return row[0] if row else None


def mark_summary_sent(guild_id: int) -> None:
    _conn.execute("UPDATE guilds SET summary_sent_at=? WHERE guild_id=?", (time.time(), guild_id))
    _conn.commit()


def week_stats(guild_id: int, start: float, end: float) -> dict:
    span = (guild_id, start, end)
    kinds = dict(_conn.execute(
        "SELECT kind, COUNT(*) FROM events WHERE guild_id=? AND created_at>=? AND created_at<? GROUP BY kind",
        span).fetchall())
    opened = _conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE guild_id=? AND case_no IS NOT NULL AND created_at>=? AND created_at<?",
        span).fetchone()[0]
    resolved, avg_seconds = _conn.execute(
        "SELECT COUNT(*), AVG(resolved_at - created_at) FROM alerts WHERE guild_id=? AND status='resolved'"
        " AND resolved_at>=? AND resolved_at<?", span).fetchone()
    still_open = _conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE guild_id=? AND case_no IS NOT NULL AND status='open'",
        (guild_id,)).fetchone()[0]

    # reports are saved as "Type: details"
    days = max(1, min(7, -(-int(end - start) // 86400)))
    daily, types = [0] * 7, {}
    for created_at, detail in _conn.execute(
            "SELECT created_at, detail FROM events WHERE guild_id=? AND created_at>=? AND created_at<?"
            " AND kind='vc_report'", span):
        daily[min(int((created_at - start) // 86400), days - 1)] += 1
        label = (detail or "").split(":")[0].strip() or "Other"
        types[label] = types.get(label, 0) + 1

    def top(query, *extra):
        return _conn.execute(query + " GROUP BY 1 ORDER BY 2 DESC LIMIT 1", (*span, *extra)).fetchone()

    return {
        "reports": kinds.get("vc_report", 0),
        "images": kinds.get("image_flagged", 0),
        "warnings": kinds.get("staff_warn", 0),
        "timeouts": kinds.get("staff_timeout", 0),
        "disconnects": kinds.get("staff_disconnect", 0),
        "opened": opened, "resolved": resolved, "avg_seconds": avg_seconds, "still_open": still_open,
        "daily": daily, "types": sorted(types.items(), key=lambda item: -item[1]),
        "top_channel": top("SELECT channel_id, COUNT(*) FROM events WHERE guild_id=? AND created_at>=?"
                           " AND created_at<? AND kind='vc_report' AND channel_id IS NOT NULL"),
        "top_member": top("SELECT user_id, COUNT(*) FROM events WHERE guild_id=? AND created_at>=?"
                          f" AND created_at<? AND user_id IS NOT NULL AND kind IN ({','.join('?' * len(FLAG_KINDS))})",
                          *FLAG_KINDS),
        "top_staff": top("SELECT resolved_by, COUNT(*) FROM alerts WHERE guild_id=? AND resolved_at>=?"
                         " AND resolved_at<? AND status='resolved' AND resolved_by IS NOT NULL"),
    }


# reporters, deleted once the case is closed

def add_reporter(message_id: int, user_id: int) -> None:
    _conn.execute("INSERT OR IGNORE INTO reporters VALUES (?, ?, ?)", (message_id, user_id, time.time()))
    _conn.commit()


def take_reporters(message_id: int) -> list[int]:
    ids = [r[0] for r in _conn.execute("SELECT user_id FROM reporters WHERE message_id=?", (message_id,))]
    _conn.execute("DELETE FROM reporters WHERE message_id=?", (message_id,))
    _conn.commit()
    return ids


# icons

def get_icon(key: str) -> tuple[str, int] | None:
    return _conn.execute("SELECT hash, emoji_id FROM icons WHERE key=?", (key,)).fetchone()


def save_icon(key: str, picture_hash: str, emoji_id: int) -> None:
    _conn.execute("INSERT INTO icons VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET hash=excluded.hash,"
                  " emoji_id=excluded.emoji_id", (key, picture_hash, emoji_id))
    _conn.commit()


# staff stats

def staff_stats(guild_id: int, since: float) -> dict:
    staff: dict[int, dict] = {}

    def person(user_id):
        return staff.setdefault(user_id, {"cases": 0, "avg_seconds": None, "warnings": 0, "timeouts": 0,
                                          "disconnects": 0})

    for user_id, n, avg in _conn.execute(
            "SELECT resolved_by, COUNT(*), AVG(resolved_at - created_at) FROM alerts WHERE guild_id=?"
            " AND status='resolved' AND resolved_at>=? AND resolved_by IS NOT NULL GROUP BY resolved_by",
            (guild_id, since)):
        person(user_id).update(cases=n, avg_seconds=avg)
    names = {"staff_warn": "warnings", "staff_timeout": "timeouts", "staff_disconnect": "disconnects"}
    for user_id, kind, n in _conn.execute(
            "SELECT actor_id, kind, COUNT(*) FROM events WHERE guild_id=? AND created_at>=? AND actor_id IS NOT NULL"
            f" AND kind IN ({','.join('?' * len(names))}) GROUP BY actor_id, kind", (guild_id, since, *names)):
        person(user_id)[names[kind]] = n

    resolved, resolve_avg = _conn.execute(
        "SELECT COUNT(*), AVG(resolved_at - created_at) FROM alerts WHERE guild_id=? AND status='resolved'"
        " AND resolved_at>=?", (guild_id, since)).fetchone()
    claim_avg = _conn.execute(
        "SELECT AVG(claimed_at - created_at) FROM alerts WHERE guild_id=? AND case_no IS NOT NULL"
        " AND claimed_at IS NOT NULL AND claimed_at>=? AND claimed_at>=created_at", (guild_id, since)).fetchone()[0]
    opened = _conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE guild_id=? AND case_no IS NOT NULL AND created_at>=?",
        (guild_id, since)).fetchone()[0]
    return {"staff": staff, "resolved": resolved, "opened": opened, "resolve_avg": resolve_avg, "claim_avg": claim_avg}


# appeals

def create_appeal(event_id: int, guild_id: int, user_id: int, text: str) -> bool:
    cur = _conn.execute(
        "INSERT OR IGNORE INTO appeals (event_id, guild_id, user_id, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (event_id, guild_id, user_id, text[:1000], time.time()))
    _conn.commit()
    return cur.rowcount == 1


def get_appeal(event_id: int) -> dict | None:
    row = _conn.execute("SELECT guild_id, user_id, text, status, decided_by FROM appeals WHERE event_id=?",
                        (event_id,)).fetchone()
    return dict(zip(("guild_id", "user_id", "text", "status", "decided_by"), row)) if row else None


def decide_appeal(event_id: int, status: str, mod_id: int) -> bool:
    cur = _conn.execute("UPDATE appeals SET status=?, decided_by=? WHERE event_id=? AND status='pending'",
                        (status, mod_id, event_id))
    _conn.commit()
    return cur.rowcount == 1


def delete_appeal(event_id: int) -> None:
    _conn.execute("DELETE FROM appeals WHERE event_id=?", (event_id,))
    _conn.commit()


# removing records

def clear_member(guild_id: int, user_id: int, what: str) -> int:
    if what == "everything":
        cur = _conn.execute("DELETE FROM events WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        ids = [r[0] for r in _conn.execute("SELECT message_id FROM alerts WHERE guild_id=? AND target_id=?",
                                           (guild_id, user_id))]
        for mid in ids:
            _conn.execute("DELETE FROM alert_actions WHERE message_id=?", (mid,))
            _conn.execute("DELETE FROM reporters WHERE message_id=?", (mid,))
        _conn.execute("DELETE FROM alerts WHERE guild_id=? AND target_id=?", (guild_id, user_id))
        for table in ("teen_signals", "risk_flags", "nick_tips", "leavers", "alt_alerts", "network_catches"):
            _conn.execute(f"DELETE FROM {table} WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        _conn.commit()
        return cur.rowcount + len(ids)
    cur = _conn.execute("DELETE FROM events WHERE guild_id=? AND user_id=? AND kind='staff_warn'",
                        (guild_id, user_id))
    _conn.commit()
    return cur.rowcount


def delete_case(guild_id: int, case_no: int) -> dict | None:
    case = get_case(guild_id, case_no)
    if not case:
        return None
    _conn.execute("DELETE FROM alert_actions WHERE message_id=?", (case["message_id"],))
    _conn.execute("DELETE FROM reporters WHERE message_id=?", (case["message_id"],))
    _conn.execute("DELETE FROM alerts WHERE message_id=?", (case["message_id"],))
    _conn.commit()
    return case


def delete_event(guild_id: int, event_id: int) -> tuple | None:
    row = _conn.execute("SELECT kind, user_id FROM events WHERE guild_id=? AND id=?", (guild_id, event_id)).fetchone()
    if row:
        _conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        _conn.commit()
    return row


# teen safety

TEEN_TABLES = ("teen_signals", "restrictions", "risk_flags", "nudged", "nick_tips", "leavers", "alt_alerts",
               "verified_adults")


def get_meta(key: str) -> str | None:
    row = _conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(key: str, value: str) -> None:
    _conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
    _conn.commit()


def verified_adults(guild_id: int) -> set[int]:
    return {r[0] for r in _conn.execute("SELECT user_id FROM verified_adults WHERE guild_id=?", (guild_id,))}


def set_verified_adult(guild_id: int, user_id: int, on: bool) -> None:
    if on:
        _conn.execute("INSERT OR IGNORE INTO verified_adults VALUES (?, ?, ?)", (guild_id, user_id, time.time()))
    else:
        _conn.execute("DELETE FROM verified_adults WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    _conn.commit()


SIGNAL_DAYS = 30        # how far back warning signs count


def add_signal(guild_id: int, user_id: int, kind: str, other_id: int | None = None, detail: str = "") -> None:
    _conn.execute("INSERT INTO teen_signals VALUES (?, ?, ?, ?, ?, ?)",
                  (guild_id, user_id, kind, other_id, detail[:200], time.time()))
    _conn.commit()


# undo the signals for the whole case and drop it from their history
def false_alarm(guild_id: int, user_id: int, first_message_id: int) -> None:
    first = str(first_message_id)
    rows = _conn.execute("SELECT detail FROM teen_signals WHERE guild_id=? AND user_id=? AND kind='flirty'"
                         " AND (detail=? OR detail LIKE ?)", (guild_id, user_id, first, first + ":%")).fetchall()
    ids = {int(part) for (detail,) in rows for part in detail.split(":") if part.isdigit()} | {first_message_id}
    _conn.execute("DELETE FROM teen_signals WHERE guild_id=? AND user_id=? AND kind='flirty'"
                  " AND (detail=? OR detail LIKE ?)", (guild_id, user_id, first, first + ":%"))
    _conn.executemany("DELETE FROM events WHERE guild_id=? AND user_id=? AND kind='teen_flirty' AND message_id=?",
                      [(guild_id, user_id, mid) for mid in ids])
    _conn.executemany("DELETE FROM network_catches WHERE guild_id=? AND user_id=? AND message_id=?",
                      [(guild_id, user_id, mid) for mid in ids])
    _conn.commit()


def signals(guild_id: int, user_id: int) -> list[dict]:
    rows = _conn.execute("SELECT kind, other_id, detail, created_at FROM teen_signals"
                         " WHERE guild_id=? AND user_id=? AND created_at>=? ORDER BY created_at",
                         (guild_id, user_id, time.time() - SIGNAL_DAYS * 86400)).fetchall()
    return [dict(zip(("kind", "other_id", "detail", "created_at"), r)) for r in rows]


def teen_reporters(guild_id: int, user_id: int, settled_only: bool = False) -> set[int]:
    # "new" = joined this week or has a new account. their reports still count as warning signs
    extra = " AND detail != 'new'" if settled_only else ""
    return {r[0] for r in _conn.execute(
        "SELECT DISTINCT other_id FROM teen_signals WHERE guild_id=? AND user_id=? AND kind='teen_report'"
        f" AND created_at>=?{extra}", (guild_id, user_id, time.time() - SIGNAL_DAYS * 86400))}


def restricted(guild_id: int, user_id: int) -> bool:
    return _conn.execute("SELECT 1 FROM restrictions WHERE guild_id=? AND user_id=?",
                         (guild_id, user_id)).fetchone() is not None


def set_restricted(guild_id: int, user_id: int, on: bool) -> None:
    if on:
        _conn.execute("INSERT OR IGNORE INTO restrictions VALUES (?, ?, 'active', ?)", (guild_id, user_id, time.time()))
    else:
        _conn.execute("DELETE FROM restrictions WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    _conn.commit()


def risk_flag(guild_id: int, user_id: int) -> dict | None:
    row = _conn.execute("SELECT score, test, flagged_at FROM risk_flags WHERE guild_id=? AND user_id=?",
                        (guild_id, user_id)).fetchone()
    return dict(zip(("score", "test", "flagged_at"), row)) if row else None


def set_risk_flag(guild_id: int, user_id: int, score: int, test: bool) -> None:
    _conn.execute("INSERT INTO risk_flags VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id, user_id)"
                  " DO UPDATE SET score=excluded.score, test=excluded.test, flagged_at=excluded.flagged_at",
                  (guild_id, user_id, score, int(test), time.time()))
    _conn.commit()


def risk_flags(guild_id: int) -> list[dict]:
    rows = _conn.execute("SELECT user_id, score, test, flagged_at FROM risk_flags WHERE guild_id=?"
                         " ORDER BY flagged_at DESC LIMIT 15", (guild_id,)).fetchall()
    return [dict(zip(("user_id", "score", "test", "flagged_at"), r)) for r in rows]


def first_nudge(guild_id: int, user_id: int) -> bool:
    cur = _conn.execute("INSERT OR IGNORE INTO nudged VALUES (?, ?, ?)", (guild_id, user_id, time.time()))
    _conn.commit()
    return cur.rowcount == 1


def nick_tipped(guild_id: int, user_id: int, nick: str) -> bool:
    row = _conn.execute("SELECT nick FROM nick_tips WHERE guild_id=? AND user_id=?", (guild_id, user_id)).fetchone()
    if row and row[0] == nick:
        return True
    _conn.execute("INSERT INTO nick_tips VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, user_id)"
                  " DO UPDATE SET nick=excluded.nick, created_at=excluded.created_at",
                  (guild_id, user_id, nick, time.time()))
    _conn.commit()
    return False


# members who left during a case

def watch_list() -> list[tuple[int, int]]:
    return _conn.execute(
        "SELECT guild_id, target_id FROM alerts WHERE kind IN ('teen', 'risk') AND status='open'"
        " AND target_id IS NOT NULL AND target_left_at IS NULL"
        " UNION SELECT guild_id, user_id FROM restrictions"
        " EXCEPT SELECT guild_id, user_id FROM leavers").fetchall()


def mark_left(guild_id: int, user_id: int, name: str | None, display: str | None, avatar: str | None) -> list[dict]:
    keys = ("message_id", "staff_channel_id", "case_no")
    where = ("guild_id=? AND target_id=? AND kind IN ('teen', 'risk') AND status='open' AND target_left_at IS NULL")
    cases = [dict(zip(keys, r)) for r in _conn.execute(
        f"SELECT message_id, staff_channel_id, case_no FROM alerts WHERE {where} ORDER BY case_no", (guild_id, user_id))]
    case_no = cases[-1]["case_no"] if cases else None
    now = time.time()
    _conn.execute("INSERT OR REPLACE INTO leavers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (guild_id, user_id, name, display, avatar, case_no, int(restricted(guild_id, user_id)), now))
    _conn.execute(f"UPDATE alerts SET target_left_at=? WHERE {where}", (now, guild_id, user_id))
    _conn.commit()
    return cases


def recent_leavers(guild_id: int, since: float) -> list[dict]:
    keys = ("user_id", "name", "display", "avatar", "case_no", "restricted", "left_at")
    rows = _conn.execute(f"SELECT {', '.join(keys)} FROM leavers WHERE guild_id=? AND left_at>=?", (guild_id, since))
    return [dict(zip(keys, r)) for r in rows]


def forget_leaver(guild_id: int, user_id: int) -> None:
    if _conn.execute("DELETE FROM leavers WHERE guild_id=? AND user_id=?", (guild_id, user_id)).rowcount:
        _conn.commit()


def alt_alert_once(guild_id: int, user_id: int) -> bool:
    cur = _conn.execute("INSERT OR IGNORE INTO alt_alerts VALUES (?, ?, ?)", (guild_id, user_id, time.time()))
    _conn.commit()
    return cur.rowcount == 1


def message_links(guild_id: int, user_id: int, since: float) -> list[str]:
    rows = _conn.execute("SELECT channel_id, message_id FROM events WHERE guild_id=? AND user_id=? AND message_id"
                         " IS NOT NULL AND created_at>=? ORDER BY created_at DESC LIMIT 10", (guild_id, user_id, since))
    return [f"https://discord.com/channels/{guild_id}/{c}/{m}" for c, m in rows]


# lookout network

NETWORK_DAYS = 365      # how long a catch counts
NETWORK_SERVERS = 2     # caught in at least this many servers
NETWORK_TEENS = 2       # with at least this many different teens


def add_network_catch(guild_id: int, user_id: int, teen_id: int, message_id: int, text: str) -> None:
    _conn.execute("INSERT OR IGNORE INTO network_catches VALUES (?, ?, ?, ?, ?, ?)",
                  (guild_id, user_id, teen_id, message_id, (text or "")[:200], time.time()))
    _conn.commit()


# runs on every message, so just "is there anything at all"
def has_network_catches(user_id: int) -> bool:
    return _conn.execute("SELECT 1 FROM network_catches WHERE user_id=? AND created_at>=? LIMIT 1",
                         (user_id, time.time() - NETWORK_DAYS * 86400)).fetchone() is not None


def network_record(user_id: int, skip_guild: int | None = None) -> dict:
    rows = _conn.execute("SELECT guild_id, teen_id, text, created_at FROM network_catches WHERE user_id=?"
                         " AND created_at>=? AND guild_id IS NOT ? ORDER BY created_at DESC",
                         (user_id, time.time() - NETWORK_DAYS * 86400, skip_guild)).fetchall()
    servers, teens = {r[0] for r in rows}, {r[1] for r in rows}
    return {"catches": [dict(zip(("guild_id", "teen_id", "text", "created_at"), r)) for r in rows],
            "servers": len(servers), "teens": len(teens),
            "listed": len(servers) >= NETWORK_SERVERS and len(teens) >= NETWORK_TEENS}


def network_alert_once(guild_id: int, user_id: int) -> bool:
    cur = _conn.execute("INSERT OR IGNORE INTO network_alerts VALUES (?, ?, ?)", (guild_id, user_id, time.time()))
    _conn.commit()
    return cur.rowcount == 1


# an accepted appeal
def clear_network(user_id: int) -> int:
    cur = _conn.execute("DELETE FROM network_catches WHERE user_id=?", (user_id,))
    _conn.execute("DELETE FROM network_alerts WHERE user_id=?", (user_id,))
    _conn.commit()
    return cur.rowcount


# a server turned the network off
def stop_sharing(guild_id: int) -> None:
    _conn.execute("DELETE FROM network_catches WHERE guild_id=?", (guild_id,))
    _conn.commit()


def network_size() -> int:
    return _conn.execute(
        "SELECT COUNT(*) FROM (SELECT user_id FROM network_catches WHERE created_at>=? GROUP BY user_id"
        " HAVING COUNT(DISTINCT guild_id)>=? AND COUNT(DISTINCT teen_id)>=?)",
        (time.time() - NETWORK_DAYS * 86400, NETWORK_SERVERS, NETWORK_TEENS)).fetchone()[0]


# totals

# keys: removed, contact, filter_alerts, false_alarms, handled, network_alerts
def bump(key: str, n: int = 1) -> None:
    _conn.execute("INSERT INTO totals VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = value + excluded.value",
                  (key, n))
    _conn.commit()


def totals() -> dict[str, int]:
    return dict(_conn.execute("SELECT key, value FROM totals").fetchall())


def claim_minutes() -> float | None:
    row = _conn.execute("SELECT AVG(claimed_at - created_at) FROM alerts WHERE claimed_at IS NOT NULL"
                        " AND claimed_at >= created_at").fetchone()
    return row[0] / 60 if row and row[0] is not None else None


# trivia

def add_trivia_points(guild_id: int, user_ids: list[int]) -> None:
    _conn.executemany("INSERT INTO trivia_points VALUES (?, ?, 1) ON CONFLICT(guild_id, user_id) "
                      "DO UPDATE SET points = points + 1", [(guild_id, u) for u in user_ids])
    _conn.commit()


def trivia_top(guild_id: int, limit: int = 10) -> list[tuple[int, int]]:
    return _conn.execute("SELECT user_id, points FROM trivia_points WHERE guild_id=? ORDER BY points DESC LIMIT ?",
                         (guild_id, limit)).fetchall()


def trivia_rank(guild_id: int, user_id: int) -> tuple[int, int]:
    row = _conn.execute("SELECT points FROM trivia_points WHERE guild_id=? AND user_id=?",
                        (guild_id, user_id)).fetchone()
    if not row:
        return 0, 0
    ahead = _conn.execute("SELECT COUNT(*) FROM trivia_points WHERE guild_id=? AND points>?",
                          (guild_id, row[0])).fetchone()[0]
    return ahead + 1, row[0]
