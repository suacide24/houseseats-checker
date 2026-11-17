#!/usr/bin/python3
"""
HouseSeats.com Show Checker
Logs into lv.houseseats.com and fetches available shows,
filtering out any shows on the denylist.
Sends email notifications for new shows (only once per show+date).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import smtplib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote, urlencode


# Pacific Time offset (UTC-8 for PST, UTC-7 for PDT)
# Using a simple approach - for accuracy we check if we're in DST
def get_pacific_time():
    """Get current time in Pacific Time."""
    utc_now = datetime.now(timezone.utc)
    # Approximate DST: second Sunday in March to first Sunday in November
    year = utc_now.year
    # DST starts second Sunday of March
    march_first = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = march_first + timedelta(days=(6 - march_first.weekday() + 7) % 7 + 7)
    # DST ends first Sunday of November
    nov_first = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov_first + timedelta(days=(6 - nov_first.weekday()) % 7)

    if dst_start <= utc_now < dst_end:
        offset = timedelta(hours=-7)  # PDT
    else:
        offset = timedelta(hours=-8)  # PST

    return utc_now + offset


import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

try:  # urllib3 ships with requests; guard only so odd installs still import
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    Retry = None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check HouseSeats and 1stTix for available shows"
    )
    parser.add_argument(
        "--fast",
        "--no-delay",
        action="store_true",
        help="Skip random delays (for testing)",
    )
    parser.add_argument(
        "--no-houseseats", action="store_true", help="Skip HouseSeats checking"
    )
    parser.add_argument(
        "--no-firsttix", action="store_true", help="Skip 1stTix checking"
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Disable email notifications for this run",
    )
    parser.add_argument(
        "--telegram-test",
        action="store_true",
        help="Send one test message to the Telegram topic and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape for real but send nothing and write no state files",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Commit and push data files from this script (local runs only; "
        "the GitHub Actions workflow does its own commit)",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=0,
        metavar="MINUTES",
        help="Exit early if the last check was less than this many minutes ago",
    )
    return parser


# Defaults so the module can be imported (tests, tooling) without parsing argv.
# main() replaces this with the real command-line arguments.
args = build_arg_parser().parse_args([])

# Configuration - HouseSeats
HOUSESEATS_BASE_URL = "https://lv.houseseats.com"
HOUSESEATS_LOGIN_URL = f"{HOUSESEATS_BASE_URL}/member/index.bv"
HOUSESEATS_SHOWS_URL = f"{HOUSESEATS_BASE_URL}/member/ajax/upcoming-shows.bv"
HOUSESEATS_EMAIL = os.environ.get("HOUSESEATS_EMAIL", "")
HOUSESEATS_PASSWORD = os.environ.get("HOUSESEATS_PASSWORD", "")

# Configuration - 1stTix
FIRSTTIX_BASE_URL = "https://www.1sttix.org"
FIRSTTIX_LOGIN_URL = f"{FIRSTTIX_BASE_URL}/login"
FIRSTTIX_EVENTS_URL = f"{FIRSTTIX_BASE_URL}/tixer/get-tickets/events"
FIRSTTIX_EMAIL = os.environ.get("FIRSTTIX_EMAIL", "")
FIRSTTIX_PASSWORD = os.environ.get("FIRSTTIX_PASSWORD", "")

# Email configuration
def _env_flag(name: str, default: bool = True) -> bool:
    """Read an on/off env var. Empty/unset -> default; off values: 0/false/no/off."""
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


# Master email on/off switch. Set EMAIL_ENABLED=false (or 0/no/off) to mute email.
EMAIL_ENABLED = _env_flag("EMAIL_ENABLED", True)
# Addresses and passwords come only from the environment (GitHub Actions
# secrets in production, your shell or run.sh locally) - never from this file.
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "") or SMTP_EMAIL

# Paths
# Telegram (the owner's show alerts). The token is a secret; the chat and
# topic ids are not, and can be overridden with repo Variables.
def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = _env_int("TELEGRAM_CHAT_ID", -1003983050734)  # "UltimateVibes" group
TELEGRAM_THREAD_ID = _env_int("TELEGRAM_THREAD_ID", 4)  # "houseseats-checker" topic

SCRIPT_DIR = Path(__file__).parent
DENYLIST_FILE = SCRIPT_DIR / "denylist.txt"
HOUSESEATS_OUTPUT_FILE = SCRIPT_DIR / "houseseats_shows.json"
FIRSTTIX_OUTPUT_FILE = SCRIPT_DIR / "firsttix_shows.json"
LOG_FILE = SCRIPT_DIR / "houseseats.log"
NOTIFIED_FILE = SCRIPT_DIR / "notified_shows.json"
HISTORY_FILE = SCRIPT_DIR / "show_history.json"
WATCHLIST_FILE = SCRIPT_DIR / "watchlist.txt"
SUBSCRIBERS_FILE = SCRIPT_DIR / "subscribers.json"
# Shows hidden with the one-click "Hide this show" link: one file per person
# (owner.txt, <subscriber id>.txt), written by the hide-show workflow and
# combined with that person's own denylist.
HIDDEN_DIR = SCRIPT_DIR / "denylists"
OWNER_ID = "owner"

# Network behaviour. Every request gets a timeout so a stalled connection can
# never hang the run; transient failures are retried.
REQUEST_TIMEOUT = (10, 30)  # (connect, read) seconds
SCRAPE_ATTEMPTS = 3  # full login+fetch attempts per source per run
SMTP_ATTEMPTS = 3
SMTP_TIMEOUT = 30

# A show+date first seen within this many hours is flagged "new" on the site.
NEW_SHOW_HOURS = 24
TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S PT"


class StateError(Exception):
    """A state file exists but can't be read. Treating it as empty would
    re-notify every show, so the run stops instead."""

# Rare show detection settings
RARE_THRESHOLD_DAYS = 30  # Look back this many days
RARE_THRESHOLD_COUNT = 3  # Show is "rare" if seen fewer than this many times

# Denylist Gist configuration
DENYLIST_GIST_RAW_URL = "https://gist.githubusercontent.com/suacide24/f1bf569e229cf1319137a4230d7db1b6/raw/denylist.txt"
DENYLIST_GIST_EDIT_URL = (
    "https://gist.github.com/suacide24/f1bf569e229cf1319137a4230d7db1b6/edit"
)
WATCHLIST_EDIT_URL = (
    "https://github.com/suacide24/houseseats-checker/edit/main/watchlist.txt"
)

# Available shows GitHub Pages URL (update this after setting up GitHub Pages)
AVAILABLE_SHOWS_URL = "https://suacide24.github.io/houseseats-checker/"
HIDE_ISSUE_URL = "https://github.com/suacide24/houseseats-checker/issues/new"
HIDE_TITLE_PREFIX = "Hide for"
# Every email this project sends starts with this, so one mail filter
# ("subject:[HouseSeats]") catches them all. check_health.py uses the same.
SUBJECT_PREFIX = "[HouseSeats]"


def log_message(message: str):
    """Log a message with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(log_entry + "\n")
    except OSError:
        pass  # logging must never break a run


def write_json_atomic(path: Path, data) -> None:
    """Write JSON via a temp file + rename so a crash mid-write can never
    leave a truncated state file behind."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# User agent pool for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0",
]


def get_random_user_agent() -> str:
    """Return a random user agent string."""
    return random.choice(USER_AGENTS)


def random_delay(
    min_seconds: float = 2.0, max_seconds: float = 8.0, silent: bool = False
):
    """Wait a random amount of time to avoid bot detection."""
    if args.fast:
        return  # Skip delays in fast mode
    delay = random.uniform(min_seconds, max_seconds)
    if not silent:
        log_message(f"Waiting {delay:.1f} seconds...")
    time.sleep(delay)


def random_page_delay():
    """Random delay between page fetches - shorter but still varied."""
    if args.fast:
        return
    # Vary between 1-4 seconds with occasional longer pauses
    if random.random() < 0.15:  # 15% chance of longer pause
        delay = random.uniform(4.0, 8.0)
    else:
        delay = random.uniform(1.0, 4.0)
    time.sleep(delay)


class TimeoutSession(requests.Session):
    """Session that applies REQUEST_TIMEOUT to every request by default."""

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return super().request(method, url, **kwargs)


def create_session_with_random_ua() -> requests.Session:
    """Create a requests session with a random user agent, default timeouts,
    and automatic retries of idempotent requests on transient errors."""
    session = TimeoutSession()
    if Retry is not None:
        retry = Retry(
            total=3,
            connect=3,
            read=2,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "HEAD"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    return session


def load_denylist() -> set:
    """Load the denylist from GitHub Gist, falling back to local file."""
    # Try to fetch from Gist first (a couple of attempts: a transient failure
    # here would otherwise let denied shows through and trigger emails).
    for attempt in range(1, 3):
        try:
            log_message("Fetching denylist from Gist...")
            response = requests.get(DENYLIST_GIST_RAW_URL, timeout=10)
            response.raise_for_status()
            denylist = parse_list_lines(response.text)
            if denylist:
                log_message(f"Loaded {len(denylist)} shows from Gist denylist")
                return denylist
            log_message("Gist denylist came back empty - using local copy instead")
            break
        except requests.RequestException as e:
            log_message(f"Failed to fetch Gist denylist (attempt {attempt}): {e}")
            if attempt < 2:
                time.sleep(2)

    log_message("Falling back to local denylist file...")
    denylist = load_list_file(DENYLIST_FILE)
    log_message(f"Loaded {len(denylist)} shows from local denylist")
    return denylist


def parse_list_lines(text: str) -> set:
    """Parse a one-entry-per-line list: lowercased, blanks and # comments dropped."""
    entries = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.add(line.lower())
    return entries


def load_list_file(path: Path) -> set:
    """Load a denylist/watchlist style text file; missing file -> empty set."""
    try:
        return parse_list_lines(path.read_text())
    except OSError:
        return set()


def load_watchlist() -> set:
    """Load watchlist keywords (shows you especially want). Matching shows are
    starred on the site and called out in the email subject."""
    return load_list_file(WATCHLIST_FILE)


def matches_any(name: str, keywords: set) -> bool:
    """Case-insensitive 'any keyword is contained in name'."""
    name_lower = (name or "").lower()
    return any(keyword in name_lower for keyword in keywords)


def load_notified_shows() -> set:
    """Load the set of already-notified show+date combinations.

    Raises StateError if the file exists but is unreadable: silently treating
    a corrupt file as "nothing notified yet" would email every current show."""
    if not NOTIFIED_FILE.exists():
        return set()

    try:
        with open(NOTIFIED_FILE, "r") as f:
            data = json.load(f)
        notified = data["notified"]
        if not isinstance(notified, list):
            raise ValueError("'notified' is not a list")
        return set(notified)
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
        raise StateError(f"{NOTIFIED_FILE.name} is unreadable: {e}") from e


def save_notified_shows(notified: set):
    """Save the set of notified show+date combinations (sorted, so git diffs
    only show real changes)."""
    write_json_atomic(NOTIFIED_FILE, {"notified": sorted(notified)})


def load_show_history() -> dict:
    """Load the show history tracking data. A corrupt file only costs the
    rare-show flags, so it is reset rather than stopping the run."""
    if not HISTORY_FILE.exists():
        return {"shows": {}}

    try:
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
        if not isinstance(history.get("shows"), dict):
            raise ValueError("'shows' is not an object")
        return history
    except (json.JSONDecodeError, OSError, AttributeError, ValueError) as e:
        log_message(f"show_history.json unreadable ({e}) - starting fresh")
        return {"shows": {}}


def save_show_history(history: dict):
    """Save the show history tracking data."""
    write_json_atomic(HISTORY_FILE, history)


def get_show_name_key(show: dict) -> str:
    """Generate a key based on show name and source (ignoring date)."""
    name = show.get("name", "").strip().lower()
    source = show.get("source", "").strip()
    return f"{source}|{name}"


def update_show_history(shows: list, history: dict) -> dict:
    """Update history with today's shows and return updated history."""
    today = get_pacific_time().strftime("%Y-%m-%d")

    for show in shows:
        key = get_show_name_key(show)
        if key not in history["shows"]:
            history["shows"][key] = {
                "name": show.get("name", ""),
                "source": show.get("source", ""),
                "appearances": [],
            }

        # Add today's date if not already recorded today
        if today not in history["shows"][key]["appearances"]:
            history["shows"][key]["appearances"].append(today)

    return history


def is_rare_show(show: dict, history: dict) -> bool:
    """Check if a show is rare based on appearance history."""
    key = get_show_name_key(show)

    if key not in history["shows"]:
        return True  # Never seen before = rare!

    # Count appearances in the last RARE_THRESHOLD_DAYS
    cutoff_date = get_pacific_time() - timedelta(days=RARE_THRESHOLD_DAYS)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")

    appearances = history["shows"][key]["appearances"]
    recent_count = sum(1 for date in appearances if date >= cutoff_str)

    return recent_count < RARE_THRESHOLD_COUNT


def mark_rare_shows(shows: list, history: dict) -> list:
    """Add 'rare' flag to shows that are rare."""
    for show in shows:
        show["rare"] = is_rare_show(show, history)
    return shows


def cleanup_old_history(history: dict, max_age_days: int = 90) -> dict:
    """Remove history entries older than max_age_days to prevent file bloat."""
    cutoff_date = get_pacific_time() - timedelta(days=max_age_days)
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")

    for key in history["shows"]:
        # Filter to only keep recent appearances
        history["shows"][key]["appearances"] = [
            date for date in history["shows"][key]["appearances"] if date >= cutoff_str
        ]

    # Remove shows with no recent appearances
    history["shows"] = {
        key: data for key, data in history["shows"].items() if data["appearances"]
    }

    return history


# Month name/abbreviation -> month number, for resolving bare show dates.
_MONTH_TO_NUM = {}
for _i, _name in enumerate(
    [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ],
    start=1,
):
    _MONTH_TO_NUM[_name] = _i
    _MONTH_TO_NUM[_name[:3]] = _i

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _pick_nearest_year(month: int, day: int, now: datetime) -> int:
    """Choose the calendar year (prev/current/next) whose month+day lands
    closest to `now` — resolves a year-less date to the occurrence a human
    would mean when they see it."""
    now_naive = now.replace(tzinfo=None)
    best_year, best_dist = now.year, None
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            continue  # e.g. Feb 29 in a non-leap year
        dist = abs((candidate - now_naive).days)
        if best_dist is None or dist < best_dist:
            best_dist, best_year = dist, year
    return best_year


def resolve_show_date(date_str: str, now: datetime) -> str | None:
    """Normalize a show's display date to a canonical ``YYYY-MM-DD`` string,
    inferring the year from ``now`` when the date carries none (HouseSeats
    dates look like "July 18th"; 1stTix embeds a year like "Fri, 13 Feb '26").
    Returns None if no month+day can be parsed, so callers can fall back."""
    if not date_str:
        return None
    s = date_str.strip().lower()
    month_match = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*", s
    )
    day_match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", s)
    if not month_match or not day_match:
        return None
    month = _MONTH_TO_NUM.get(month_match.group(1))
    day = int(day_match.group(1))
    if not month or not (1 <= day <= 31):
        return None
    # Explicit year: 4-digit (2026) or apostrophe 2-digit ('26). A bare 2-digit
    # number is treated as the day, never the year, to avoid ambiguity.
    year_match = re.search(r"\b(20\d{2})\b", s) or re.search(r"'(\d{2})\b", s)
    if year_match:
        y = int(year_match.group(1))
        year = y if y >= 2000 else 2000 + y
    else:
        year = _pick_nearest_year(month, day, now)
    return f"{year:04d}-{month:02d}-{day:02d}"


def get_show_key(show: dict) -> str:
    """Generate a unique key for a show+date+source combination.

    The date is normalized to ``YYYY-MM-DD`` with an inferred year so the same
    calendar date in different years produces distinct keys — the checker
    re-notifies year after year instead of suppressing a date it saw once.
    Falls back to the raw date string if it can't be parsed."""
    name = show.get("name", "").strip()
    source = show.get("source", "").strip()
    raw_date = show.get("date", "").strip()
    iso_date = resolve_show_date(raw_date, get_pacific_time())
    date_part = iso_date if iso_date else raw_date
    return f"{source}|{name}|{date_part}"


def migrate_notified_keys(notified: set, now: datetime) -> tuple[set, bool]:
    """One-time upgrade of legacy ``source|name|July 18th`` keys to the
    year-aware ``source|name|2026-07-18`` form so existing notifications keep
    matching (no re-notification flood) while future years get fresh keys.
    Returns (possibly-migrated set, changed?)."""
    migrated, changed = set(), False
    for key in notified:
        parts = key.split("|", 2)
        if len(parts) != 3:
            migrated.add(key)
            continue
        source, name, date_part = parts
        if _ISO_DATE_RE.match(date_part):
            migrated.add(key)  # already year-aware
            continue
        iso_date = resolve_show_date(date_part, now)
        if iso_date:
            migrated.add(f"{source}|{name}|{iso_date}")
            changed = True
        else:
            migrated.add(key)  # unparseable — leave as-is
    return migrated, changed


def cleanup_old_notified(
    notified: set, now: datetime, max_age_days: int = 400
) -> tuple[set, bool]:
    """Drop notified keys whose (year-aware) show date is more than
    ``max_age_days`` in the past. 400d keeps well over a year so the annual
    re-notify logic is never affected; only ancient entries are pruned.
    Legacy/unparseable keys are always kept. Returns (kept set, changed?)."""
    now_naive = now.replace(tzinfo=None)
    cutoff = now_naive - timedelta(days=max_age_days)
    kept, changed = set(), False
    for key in notified:
        parts = key.split("|", 2)
        if len(parts) == 3 and _ISO_DATE_RE.match(parts[2]):
            try:
                show_date = datetime.strptime(parts[2], "%Y-%m-%d")
            except ValueError:
                kept.add(key)
                continue
            if show_date < cutoff:
                changed = True
                continue  # prune
        kept.add(key)
    return kept, changed


def format_show_date(show: dict) -> str:
    """Human-friendly date with the weekday, e.g. 'Sat, Nov 14'. Falls back
    to the scraped text when the date could not be normalized."""
    iso = show.get("date_iso")
    if iso:
        try:
            d = datetime.strptime(iso, "%Y-%m-%d")
            label = f"{d.strftime('%a')}, {d.strftime('%b')} {d.day}"
            time_match = re.search(r"\d{1,2}:\d{2}\s*[AP]M", show.get("date", ""), re.I)
            return f"{label} {time_match.group(0)}" if time_match else label
        except ValueError:
            pass
    return show.get("date") or "Date TBD"


def enrich_shows(shows: list, previous_shows: list, watchlist: set, now: datetime,
                 notified: set | None = None) -> list:
    """Add derived fields used by the site and the email:

    - ``date_iso``  canonical YYYY-MM-DD (year inferred) for sorting
    - ``weekday``   'Sat' etc.
    - ``first_seen`` when this show+date first appeared (carried over from the
      previous output so it survives across runs; a slot that vanishes and
      comes back counts as newly seen). A slot that was already notified
      but has no recorded time (first run after an upgrade, or the previous
      list was lost) gets None rather than "now", so it is not shown as NEW.
    - ``watchlist`` True when the name matches a watchlist keyword
    """
    previous_first_seen = {}
    for prev in previous_shows or []:
        if prev.get("first_seen"):
            previous_first_seen[get_show_key(prev)] = prev["first_seen"]

    now_stamp = now.strftime(TIMESTAMP_FMT)
    for show in shows:
        iso = resolve_show_date(show.get("date", ""), now)
        if iso:
            show["date_iso"] = iso
            try:
                show["weekday"] = datetime.strptime(iso, "%Y-%m-%d").strftime("%a")
            except ValueError:
                pass
        key = get_show_key(show)
        if key in previous_first_seen:
            show["first_seen"] = previous_first_seen[key]
        elif notified and key in notified:
            show["first_seen"] = None
        else:
            show["first_seen"] = now_stamp
        show["watchlist"] = matches_any(show.get("name", ""), watchlist)
    return shows


def dedupe_shows(shows: list) -> list:
    """Drop exact duplicates (same show, date and link), keeping the first.
    Two listings for the same day with different links are both kept."""
    seen, unique = set(), []
    for show in shows:
        key = (get_show_key(show), show.get("link", ""))
        if key not in seen:
            seen.add(key)
            unique.append(show)
    return unique


def get_chatgpt_link(show: dict) -> str:
    """Generate a ChatGPT link to ask about the show."""
    name = show.get("name", "Unknown")
    date = show.get("date", "")

    prompt = f"I'm considering going to see '{name}' in Las Vegas"
    if date:
        prompt += f" on {date}"
    prompt += ". Is this show good? What can you tell me about it? Should I go see it? What should I expect?"

    encoded_prompt = quote(prompt)
    return f"https://chat.openai.com/?q={encoded_prompt}"


def load_hidden(person_id: str) -> set:
    """Keywords this person hid with the one-click link."""
    return load_list_file(HIDDEN_DIR / f"{person_id}.txt")


def get_hide_link(show_name: str, person_id: str) -> str:
    """Link that opens a pre-filled GitHub issue; submitting it makes the
    hide-show workflow add the show to this person's hidden list."""
    name = " ".join((show_name or "").split())
    query = urlencode(
        {
            "title": f"{HIDE_TITLE_PREFIX} {person_id}: {name}",
            "body": "Press the green \"Create\" button below to hide this show. "
            "It disappears from your alerts and page at the next check.",
        }
    )
    return f"{HIDE_ISSUE_URL}?{query}"


def find_new_shows(shows: list, notified: set) -> list:
    """Find shows that haven't been notified yet."""
    new_shows = []
    for show in shows:
        key = get_show_key(show)
        if key not in notified:
            new_shows.append(show)
    return new_shows


def group_shows_by_name(shows: list) -> list:
    """Group shows by name + source, collecting all time slots together.
    Watchlist shows sort first, then rare ones, then alphabetically; slots
    within a show sort chronologically."""
    grouped = {}

    for show in shows:
        key = f"{show.get('source', 'Unknown')}|{show.get('name', 'Unknown')}"

        if key not in grouped:
            grouped[key] = {
                "name": show.get("name", "Unknown"),
                "source": show.get("source", "Unknown"),
                "image": show.get("image"),
                "rare": False,
                "watchlist": False,
                "time_slots": [],
            }

        grouped[key]["time_slots"].append(
            {
                "date": show.get("date", "N/A"),
                "label": format_show_date(show),
                "date_iso": show.get("date_iso") or "9999-12-31",
                "link": show.get("link", ""),
            }
        )

        # If any slot is rare / on the watchlist, flag the whole show
        if show.get("rare"):
            grouped[key]["rare"] = True
        if show.get("watchlist"):
            grouped[key]["watchlist"] = True
        if not grouped[key]["image"] and show.get("image"):
            grouped[key]["image"] = show["image"]

    for group in grouped.values():
        group["time_slots"].sort(key=lambda slot: slot["date_iso"])

    return sorted(
        grouped.values(),
        key=lambda g: (not g["watchlist"], not g["rare"], g["name"].lower()),
    )


def _one_line(text: str, limit: int = 45) -> str:
    """Collapse whitespace (no header injection via newlines) and truncate."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_email_subject(grouped_shows: list) -> str:
    """A subject that says what is new, so the alert is useful unopened:
    '⭐ Watchlist: Penn & Teller (Sat, Nov 14), ROUGE +2 more'."""
    if not grouped_shows:
        return "🎭 Shows Alert"
    if any(g["watchlist"] for g in grouped_shows):
        prefix = "⭐ Watchlist"
    elif any(g["rare"] for g in grouped_shows):
        prefix = "🔥 Rare"
    else:
        prefix = "🎭 New"
    first = grouped_shows[0]
    subject = f"{prefix}: {_one_line(first['name'])}"
    if first["time_slots"]:
        subject += f" ({first['time_slots'][0]['label']})"
    if len(grouped_shows) > 1:
        subject += f", {_one_line(grouped_shows[1]['name'], 30)}"
    if len(grouped_shows) > 2:
        subject += f" +{len(grouped_shows) - 2} more"
    return subject


def build_email_bodies(
    grouped_shows: list,
    total_slots: int,
    view_url: str | None = None,
    denylist_edit_url: str | None = DENYLIST_GIST_EDIT_URL,
    watchlist_edit_url: str | None = WATCHLIST_EDIT_URL,
    welcome: bool = False,
    footer_note: str = "",
    hide_id: str | None = OWNER_ID,
) -> tuple[str, str]:
    """Return (plain_text, html) bodies. All scraped text is HTML-escaped.
    The footer links are per recipient; pass None to leave one out."""
    esc = html.escape
    view_url = view_url or AVAILABLE_SHOWS_URL
    heading = "You're subscribed! Here's what's available now" if welcome else "New Shows Available!"
    button = "display: inline-block; background: #6c757d; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 500; margin: 0 5px 8px 5px;"
    footer_buttons = f'<a href="{esc(view_url, quote=True)}" style="{button} background: #3498db;">📋 View All Shows</a>'
    footer_text = f"\n📋 View All Shows: {view_url}\n"
    if denylist_edit_url:
        footer_buttons += f'<a href="{esc(denylist_edit_url, quote=True)}" style="{button}">✏️ Edit Denylist</a>'
        footer_text += f"✏️ Edit Denylist: {denylist_edit_url}\n"
    if watchlist_edit_url:
        footer_buttons += f'<a href="{esc(watchlist_edit_url, quote=True)}" style="{button}">⭐ Edit Watchlist</a>'
        footer_text += f"⭐ Edit Watchlist: {watchlist_edit_url}\n"
    if footer_note:
        footer_text += f"\n{footer_note}\n"
    footer_note_html = ("<br>" + esc(footer_note)) if footer_note else ""

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
    <div style="background: linear-gradient(135deg, #e74c3c, #c0392b); padding: 20px; text-align: center;">
        <h1 style="color: white; margin: 0;">🎭 {heading}</h1>
        <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0 0;">{len(grouped_shows)} show(s) • {total_slots} time slot(s)</p>
    </div>
    <div style="padding: 20px;">
    """

    for show in grouped_shows:
        source = show["source"]
        time_slots = show["time_slots"]
        source_bg = "#3498db" if source == "HouseSeats" else "#27ae60"
        border = "#f1c40f" if show["watchlist"] else source_bg

        badges = ""
        if show["watchlist"]:
            badges += '<span style="background: #f1c40f; color: #333; padding: 3px 8px; border-radius: 4px; font-size: 11px; margin-left: 8px; font-weight: bold;">⭐ WATCHLIST</span>'
        if show["rare"]:
            badges += '<span style="background: linear-gradient(135deg, #e74c3c, #c0392b); color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px; margin-left: 8px; font-weight: bold;">🔥 RARE</span>'

        html_body += f"""
        <div style="background: #f8f9fa; border-radius: 8px; padding: 15px; margin-bottom: 15px; border-left: 4px solid {border};">
            <div style="margin-bottom: 10px;">
                <span style="background: {source_bg}; color: white; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; text-transform: uppercase;">{esc(source)}</span>
                {badges}
            </div>
            <h2 style="margin: 0 0 12px 0; color: #333; font-size: 18px;">{esc(show["name"])}</h2>
            <div style="margin-bottom: 12px;">
                <div style="color: #e67e22; font-size: 13px; font-weight: 500; margin-bottom: 8px;">📅 Available Times ({len(time_slots)})</div>
                <div>
        """

        for slot in time_slots:
            label = esc(slot["label"])
            if slot["link"]:
                html_body += f'<a href="{esc(slot["link"], quote=True)}" style="display: inline-block; background: rgba(231,76,60,0.1); border: 1px solid rgba(231,76,60,0.3); color: #c0392b; padding: 6px 12px; border-radius: 6px; text-decoration: none; font-size: 13px; margin: 0 6px 6px 0;">🎟️ {label}</a>'
            else:
                html_body += f'<span style="display: inline-block; background: #eee; color: #666; padding: 6px 12px; border-radius: 6px; font-size: 13px; margin: 0 6px 6px 0;">{label}</span>'

        html_body += f"""
                </div>
            </div>
            <div>
                <a href="{esc(get_chatgpt_link(show), quote=True)}" style="color: #666; font-size: 12px; text-decoration: none;">🤖 Ask AI about this show</a>
                {f'<a href="{esc(get_hide_link(show["name"], hide_id), quote=True)}" style="color: #666; font-size: 12px; text-decoration: none; margin-left: 14px;">🙈 Hide this show</a>' if hide_id else ""}
            </div>
        </div>
        """

    html_body += f"""
    </div>
    <div style="padding: 20px; background: #f8f9fa; text-align: center; border-top: 1px solid #eee;">
        {footer_buttons}
    </div>
    <div style="padding: 15px; text-align: center; color: #999; font-size: 11px;">
        Automated message from Shows Checker (HouseSeats + 1stTix){footer_note_html}
    </div>
    </div>
    </body>
    </html>
    """

    # Plain text fallback (also grouped)
    text_body = f"🎭 {heading}\n"
    text_body += f"{len(grouped_shows)} show(s) • {total_slots} time slot(s)\n"
    text_body += "=" * 40 + "\n\n"

    for show in grouped_shows:
        flags = (" ⭐ WATCHLIST" if show["watchlist"] else "") + (
            " 🔥 RARE" if show["rare"] else ""
        )
        text_body += f"[{show['source']}] {show['name']}{flags}\n"
        text_body += f"  📅 {len(show['time_slots'])} time slot(s):\n"
        for slot in show["time_slots"]:
            text_body += f"    • {slot['label']}"
            if slot["link"]:
                text_body += f"\n      {slot['link']}"
            text_body += "\n"
        text_body += f"  🤖 Ask AI: {get_chatgpt_link(show)}\n"
        if hide_id:
            text_body += f"  🙈 Hide this show: {get_hide_link(show['name'], hide_id)}\n"
        text_body += "\n"

    text_body += footer_text

    return text_body, html_body


# ---------------------------------------------------------------------------
# Telegram: the owner's show alerts go to a group topic; email is the fallback.
# The bot token is part of the API URL, so nothing here ever logs a URL or a
# raw exception - only the method, the HTTP status and Telegram's own
# description of the error.
# ---------------------------------------------------------------------------

TELEGRAM_MAX_CHARS = 4000  # Telegram's hard limit is 4096


def _tg_call(method: str, payload: dict):
    """Call the Telegram Bot API. Returns the result, or None on any failure."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode())
        if body.get("ok"):
            return body.get("result")
        log_message(f"[telegram] {method} was not accepted")
    except urllib.error.HTTPError as e:
        try:
            description = str(json.loads(e.read().decode()).get("description", ""))[:200]
        except Exception:  # noqa: BLE001
            description = ""
        log_message(f"[telegram] {method} failed: HTTP {e.code} {description}")
    except Exception as e:  # noqa: BLE001 - the message could contain the URL
        log_message(f"[telegram] {method} failed: {type(e).__name__}")
    return None


def uses_telegram() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(text: str, button: tuple | None = None):
    """Send an HTML message to the project's topic. Returns its message_id
    (usable with delete_telegram), or None if it could not be sent."""
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:TELEGRAM_MAX_CHARS],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if TELEGRAM_THREAD_ID:
        payload["message_thread_id"] = TELEGRAM_THREAD_ID
    if button:
        label, url = button
        payload["reply_markup"] = {"inline_keyboard": [[{"text": label, "url": url}]]}
    result = _tg_call("sendMessage", payload)
    if isinstance(result, dict) and result.get("message_id"):
        return result["message_id"]
    return None


def delete_telegram(message_id) -> None:
    _tg_call("deleteMessage", {"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id})


def _tg(text) -> str:
    """Escape dynamic text for Telegram's HTML mode (& < > only)."""
    return html.escape(str(text or ""), quote=False)


def _tg_link(url: str, label: str) -> str:
    if not str(url or "").startswith(("https://", "http://")):
        return _tg(label)
    return f'<a href="{html.escape(url, quote=True)}">{_tg(label)}</a>'


def build_telegram_message(grouped_shows: list, hide_id: str = OWNER_ID) -> tuple[str, tuple]:
    """Return (html_text, (button_label, button_url)).

    The first line is the whole story - it is what the phone preview shows.
    Shows are added whole until the size limit, so the HTML is never cut in
    the middle of a tag (which would make Telegram reject the message)."""
    headline = build_email_subject(grouped_shows)  # '⭐ Watchlist: Penn & Teller (Sat, Nov 21) +2 more'
    lines = [f"<b>{_tg(headline)}</b>"]
    size = len(lines[0])
    shown = 0
    for show in grouped_shows:
        badges = (" ⭐" if show["watchlist"] else "") + (" 🔥 rare" if show["rare"] else "")
        slots = " · ".join(_tg_link(slot["link"], slot["label"]) for slot in show["time_slots"])
        block = (
            f"\n<b>{_tg(show['name'])}</b>{badges}\n"
            f"🎟️ {slots}\n"
            f"{_tg_link(get_chatgpt_link(show), 'Ask AI')} · {_tg_link(get_hide_link(show['name'], hide_id), 'Hide')}"
        )
        if size + len(block) > TELEGRAM_MAX_CHARS - 200:
            break
        lines.append(block)
        size += len(block)
        shown += 1
    if shown < len(grouped_shows):
        lines.append(f"\n…and {len(grouped_shows) - shown} more on the site")

    first_link = next(
        (slot["link"] for slot in grouped_shows[0]["time_slots"] if slot["link"]), None
    ) if len(grouped_shows) == 1 else None
    button = ("🎟️ Open show", first_link) if first_link else ("📋 View all shows", AVAILABLE_SHOWS_URL)
    return "\n".join(lines), button


def deliver_owner_alert(new_shows: list) -> str:
    """Send the owner's new-show alert: Telegram when configured, email if
    Telegram is not configured or the send fails. Same return values as
    send_email_notification ('sent' / 'disabled' / 'failed')."""
    if not new_shows:
        return "sent"
    if args.no_email or not EMAIL_ENABLED:
        log_message("Alerts are muted (EMAIL_ENABLED=false / --no-email) - nothing sent")
        return "disabled"
    if uses_telegram():
        try:
            text, button = build_telegram_message(group_shows_by_name(new_shows))
            if send_telegram(text, button) is not None:
                log_message(f"Telegram alert sent ({len(new_shows)} show date(s))")
                return "sent"
        except Exception as e:  # noqa: BLE001
            log_message(f"[telegram] could not build or send the alert: {type(e).__name__}")
        log_message("Telegram failed - falling back to email")
    return send_email_notification(new_shows)


def send_telegram_test() -> int:
    """--telegram-test: post one message to the topic to prove the setup."""
    if not uses_telegram():
        log_message("Telegram is not configured (TELEGRAM_BOT_TOKEN is empty)")
        return 1
    stamp = get_pacific_time().strftime("%b %-d, %-I:%M %p PT")
    message_id = send_telegram(
        f"✅ <b>HouseSeats alerts are connected</b>\nTest message sent {_tg(stamp)}. "
        "New shows and watchlist hits will appear in this topic.",
        ("📋 View all shows", AVAILABLE_SHOWS_URL),
    )
    if message_id is None:
        log_message("Telegram test message FAILED")
        return 1
    log_message("Telegram test message sent")
    return 0


def smtp_send(msg, recipient: str | None = None) -> None:
    """Send a prepared message, retrying transient failures. Raises on final
    failure. Bad credentials are not retried."""
    last_error = None
    for attempt in range(1, SMTP_ATTEMPTS + 1):
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT) as server:
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.sendmail(SMTP_EMAIL, recipient or NOTIFICATION_EMAIL, msg.as_string())
            return
        except smtplib.SMTPAuthenticationError:
            raise
        except (smtplib.SMTPException, OSError) as e:
            last_error = e
            log_message(f"Email attempt {attempt}/{SMTP_ATTEMPTS} failed: {e}")
            if attempt < SMTP_ATTEMPTS:
                time.sleep(5 * attempt)
    raise last_error


def send_email_notification(
    new_shows: list,
    recipient: str | None = None,
    view_url: str | None = None,
    denylist_edit_url: str | None = DENYLIST_GIST_EDIT_URL,
    watchlist_edit_url: str | None = WATCHLIST_EDIT_URL,
    welcome: bool = False,
    respect_global_mute: bool = True,
    label: str = "",
) -> str:
    """Email the new shows, grouped by show name. With no ``recipient`` this
    is the owner's alert; extra subscribers pass their own address and links
    (each person gets a separate message, so nobody sees anyone else's
    address). EMAIL_ENABLED=false mutes only the owner's alerts.

    Returns 'sent', 'disabled' (email deliberately off) or 'failed'. Callers
    only mark shows as notified when the result is not 'failed', so a mail
    outage delays an alert instead of silently losing it."""
    if not new_shows:
        return "sent"

    recipient = recipient or NOTIFICATION_EMAIL
    who = f"[{label}] " if label else ""
    if args.no_email or (respect_global_mute and not EMAIL_ENABLED):
        log_message(
            "Email notifications disabled (EMAIL_ENABLED=false / --no-email) - skipping email"
        )
        return "disabled"

    if not (SMTP_EMAIL and SMTP_PASSWORD and recipient):
        log_message(
            "Email not configured (need SMTP_EMAIL, SMTP_PASSWORD, NOTIFICATION_EMAIL) "
            "- cannot send; will retry next run"
        )
        return "failed"

    try:
        grouped_shows = group_shows_by_name(new_shows)
        text_body, html_body = build_email_bodies(
            grouped_shows,
            len(new_shows),
            view_url=view_url,
            denylist_edit_url=denylist_edit_url,
            watchlist_edit_url=watchlist_edit_url,
            welcome=welcome,
            footer_note="Reply to this email if you want to stop getting these." if label else "",
            hide_id=label or OWNER_ID,
        )

        msg = MIMEMultipart("alternative")
        subject = build_email_subject(grouped_shows)
        if welcome:
            subject = f"👋 You're subscribed - {len(grouped_shows)} show(s) available now"
        msg["Subject"] = f"{SUBJECT_PREFIX} {subject}"
        msg["From"] = SMTP_EMAIL
        msg["To"] = recipient
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        smtp_send(msg, recipient)
        log_message(f"{who}Email sent successfully ({len(grouped_shows)} show(s))")
        return "sent"

    except Exception as e:
        log_message(f"{who}Failed to send email: {type(e).__name__ if label else e}")
        return "failed"


class LoginRejected(Exception):
    """The site refused the credentials. Not retried within a run, so a bad
    password can't hammer the login form."""


def login_houseseats(session: requests.Session) -> bool:
    """Log into houseseats.com. Returns True if it appears to have worked,
    False on a transient problem (network/HTTP), and raises LoginRejected if
    the site is clearly still showing the login form (bad credentials)."""
    if not HOUSESEATS_EMAIL or not HOUSESEATS_PASSWORD:
        raise LoginRejected("HOUSESEATS_EMAIL / HOUSESEATS_PASSWORD are not set")

    try:
        # First, get the homepage to establish session cookies
        response = session.get(HOUSESEATS_BASE_URL)
        response.raise_for_status()

        # Prepare login data with required fields
        login_data = {
            "submit": "login",
            "lastplace": "",
            "email": HOUSESEATS_EMAIL,
            "password": HOUSESEATS_PASSWORD,
        }

        # Submit login form
        response = session.post(
            HOUSESEATS_LOGIN_URL, data=login_data, allow_redirects=True
        )
        response.raise_for_status()
    except requests.RequestException as e:
        log_message(f"[HouseSeats] Login request failed: {e}")
        return False

    page = response.text.lower()

    # Check if login was successful by looking for logout link or member content
    if "logout" in page or "welcome" in page:
        log_message("[HouseSeats] Successfully logged in")
        return True

    log_message("[HouseSeats] Login may have failed - checking for error messages")
    soup = BeautifulSoup(response.text, "html.parser")
    error = soup.find(class_=["error", "alert", "alert-danger"])
    if error:
        log_message(f"[HouseSeats] Login error: {error.get_text(strip=True)[:200]}")
    if "member login" in page:
        raise LoginRejected("still on the login page - credentials may be incorrect")
    # No explicit indicator either way; the show fetch is the real test.
    return True


def login_firsttix(session: requests.Session) -> bool:
    """Log into 1sttix.org and return True if successful."""
    try:
        # Check if password is configured
        if not FIRSTTIX_PASSWORD:
            log_message("[1stTix] FIRSTTIX_PASSWORD not set - skipping 1stTix")
            return False

        # First, get the login page for cookies
        response = session.get(FIRSTTIX_LOGIN_URL)
        response.raise_for_status()

        # Prepare login data
        login_data = {
            "email": FIRSTTIX_EMAIL,
            "password": FIRSTTIX_PASSWORD,
        }

        # Submit login form
        response = session.post(
            FIRSTTIX_LOGIN_URL, data=login_data, allow_redirects=True
        )
        response.raise_for_status()

        response_lower = response.text.lower()

        # Check for explicit login failure messages FIRST
        if (
            "email address or password was incorrect" in response_lower
            or "invalid credentials" in response_lower
            or "login failed" in response_lower
            or "attempts left" in response_lower
        ):
            log_message("[1stTix] Login failed - incorrect email or password")
            return False

        # Check if we're still on the login page (URL didn't change after POST)
        if response.url.rstrip("/") == FIRSTTIX_LOGIN_URL.rstrip("/"):
            # If still on login page, check if there are actual login errors
            soup = BeautifulSoup(response.text, "html.parser")
            alerts = soup.find_all("div", class_=["alert", "alert-danger"])
            for alert in alerts:
                alert_text = alert.get_text(strip=True).lower()
                if (
                    "incorrect" in alert_text
                    or "invalid" in alert_text
                    or "failed" in alert_text
                ):
                    log_message(f"[1stTix] Login failed: {alert.get_text(strip=True)}")
                    return False

        # Check for success indicators - after login, user should be redirected
        # or see their account info. Look for user-specific content.
        if (
            "/tixer/" in response.url  # Redirected to tixer area
            or "welcome" in response.url.lower()
            or "dashboard" in response.url.lower()
        ):
            log_message("[1stTix] Successfully logged in")
            return True

        # Try to access the events page to verify login worked
        test_response = session.get(FIRSTTIX_EVENTS_URL)
        test_lower = test_response.text.lower()

        # If we get the "must be logged in" message, login failed
        if "must be logged in" in test_lower or "you must be logged in" in test_lower:
            log_message("[1stTix] Login failed - session not authenticated")
            return False

        # Check for event content on the events page
        soup = BeautifulSoup(test_response.text, "html.parser")
        events = soup.find_all("div", class_="event")
        if len(events) > 0:
            log_message(f"[1stTix] Successfully logged in (found {len(events)} events)")
            return True

        # If we got here and the page title isn't the error page, might be OK
        title = soup.find("title")
        if title and "important message" not in title.get_text().lower():
            log_message("[1stTix] Successfully logged in")
            return True

        log_message("[1stTix] Login may have failed - could not verify session")
        return False

    except requests.RequestException as e:
        log_message(f"[1stTix] Login request failed: {e}")
        return False


def parse_houseseats_shows(page_html: str) -> list:
    """Parse the upcoming-shows AJAX response into show dicts."""
    soup = BeautifulSoup(page_html, "html.parser")
    shows = []

    # Find all show panels (each panel is a show)
    for panel in soup.find_all("div", class_="panel-default"):
        show_info = {"source": "HouseSeats"}

        # Get show name from panel-heading link
        heading = panel.find("div", class_="panel-heading")
        if heading:
            link = heading.find("a")
            if link:
                show_info["name"] = link.get_text(strip=True)
                href = link.get("href", "")
                if href:
                    # Convert relative URL to absolute
                    if href.startswith("./"):
                        show_info["link"] = f"{HOUSESEATS_BASE_URL}/member/{href[2:]}"
                    elif href.startswith("http"):
                        show_info["link"] = href
                    elif href.startswith("/"):
                        show_info["link"] = f"{HOUSESEATS_BASE_URL}{href}"
                    else:
                        show_info["link"] = f"{HOUSESEATS_BASE_URL}/member/{href}"

        # Get date from grid-cal-date
        date_elem = panel.find("div", class_="grid-cal-date")
        if date_elem:
            show_info["date"] = date_elem.get_text(strip=True)

        # Get image URL
        img = panel.find("img", class_="img-responsive")
        if img and img.get("src"):
            src = img.get("src")
            if src.startswith("http"):
                show_info["image"] = src
            elif src.startswith("//"):
                show_info["image"] = f"https:{src}"
            else:
                show_info["image"] = f"{HOUSESEATS_BASE_URL}/{src.lstrip('/')}"

        # Only add if we have a name
        if show_info.get("name"):
            shows.append(show_info)

    return shows


def fetch_houseseats_shows(session: requests.Session) -> list | None:
    """Fetch available shows from the HouseSeats AJAX endpoint.

    Returns the list of shows, or None when the fetch can't be trusted
    (request failed, or nothing could be parsed - an expired session and a
    changed page layout both look like "zero shows", and neither should wipe
    the published list)."""
    try:
        response = session.get(
            HOUSESEATS_SHOWS_URL, headers={"X-Requested-With": "XMLHttpRequest"}
        )
        response.raise_for_status()
    except requests.RequestException as e:
        log_message(f"[HouseSeats] Failed to fetch shows: {e}")
        return None

    shows = parse_houseseats_shows(response.text)
    if not shows:
        hint = (
            "looks like a login page - session not authenticated"
            if 'type="password"' in response.text.lower()
            else "page layout may have changed, or there are genuinely no shows"
        )
        log_message(
            f"[HouseSeats] No shows parsed from a {len(response.text)}-byte response ({hint})"
        )
        return None

    log_message(f"[HouseSeats] Found {len(shows)} shows")
    return shows


def scrape_with_retries(label: str, login_fn, fetch_fn, attempts: int = SCRAPE_ATTEMPTS):
    """Run login + fetch with a fresh session, retrying transient failures.

    Returns (shows, None) on success or (None, error_message) on failure.
    Never raises: one source failing must not stop the rest of the run."""
    error = "unknown error"
    for attempt in range(1, attempts + 1):
        session = create_session_with_random_ua()
        try:
            if login_fn(session):
                random_delay(2.0, 6.0)
                shows = fetch_fn(session)
                if shows is not None:
                    return shows, None
                error = "logged in but could not read any shows"
            else:
                error = "login failed"
        except LoginRejected as e:
            log_message(f"[{label}] Login rejected: {e}")
            return None, f"login rejected: {e}"
        except Exception as e:  # e.g. a parsing bug - record it, don't crash
            error = f"{type(e).__name__}: {e}"
            log_message(f"[{label}] Unexpected error: {error}")
        finally:
            session.close()

        if attempt < attempts:
            wait = 4 * attempt
            log_message(
                f"[{label}] Attempt {attempt}/{attempts} failed ({error}); retrying in {wait}s"
            )
            time.sleep(wait)

    log_message(f"[{label}] Giving up after {attempts} attempt(s): {error}")
    return None, error


def fetch_firsttix_shows(session: requests.Session) -> list | None:
    """Fetch the list of available shows from 1stTix (all pages)."""
    try:
        shows = []

        # Patterns that indicate sponsors/ads rather than actual shows
        sponsor_patterns = [
            "tactical",
            "coursera",
            "courses",
            "certs",
            "degrees",
            "sponsor",
            "donate",
            "discount",
            "coupon",
            "hotel",
            "free courses",
            "cooperator",
            "5.11",
        ]

        # Fetch all pages
        page = 1
        max_pages = 20  # Safety limit

        while page <= max_pages:
            url = f"{FIRSTTIX_EVENTS_URL}?page={page}"
            response = session.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Find all event divs on this page
            events = soup.find_all("div", class_="event")

            if not events:
                # No more events, stop pagination
                break

            log_message(f"[1stTix] Fetching page {page} ({len(events)} events)...")

            for event in events:
                show_info = {"source": "1stTix"}

                # Get show name from image alt or entry-title
                img = event.find("img")
                if img and img.get("alt"):
                    show_info["name"] = img.get("alt")

                # Fallback to entry-title
                if not show_info.get("name"):
                    title = event.find("div", class_="entry-title")
                    if title:
                        show_info["name"] = title.get_text(strip=True)

                # Get date/time from entry-meta
                meta = event.find("div", class_="entry-meta")
                if meta:
                    meta_text = meta.get_text(" ", strip=True)
                    # Try to extract date pattern like "Wed, 4 Feb '26" and time
                    date_match = re.search(r"(\w{3},\s*\d+\s+\w+\s+'\d+)", meta_text)
                    time_match = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", meta_text)
                    if date_match:
                        show_info["date"] = date_match.group(1)
                        if time_match:
                            show_info["date"] += " " + time_match.group(1)

                # Get link to event
                link_elem = event.find(
                    "a", href=lambda x: x and "get-tickets/event" in x
                )
                if link_elem:
                    show_info["link"] = link_elem.get("href", "")

                # Get image URL
                if img and img.get("src"):
                    show_info["image"] = img.get("src")

                # Only add if we have a name
                if show_info.get("name"):
                    name_lower = show_info["name"].lower()

                    # Skip if it matches sponsor/ad patterns
                    is_sponsor = any(
                        pattern in name_lower for pattern in sponsor_patterns
                    )

                    # Skip if no event link (likely a sponsor/promo)
                    has_event_link = bool(show_info.get("link"))

                    # Skip if no date (likely not a real event)
                    has_date = bool(show_info.get("date"))

                    if is_sponsor:
                        log_message(
                            f"[1stTix] Skipping sponsor/ad: {show_info['name']}"
                        )
                    elif not has_event_link or not has_date:
                        log_message(
                            f"[1stTix] Skipping non-event (no link/date): {show_info['name']}"
                        )
                    else:
                        shows.append(show_info)

            # Move to next page
            page += 1

            # Random delay between pages to avoid bot detection
            if page <= max_pages:
                random_page_delay()

        log_message(f"[1stTix] Found {len(shows)} shows total across {page - 1} pages")
        return shows

    except requests.RequestException as e:
        log_message(f"[1stTix] Failed to fetch shows: {e}")
        return None


def filter_shows(shows: list, denylist: set) -> list:
    """Filter out shows that are on the denylist."""
    filtered = []
    for show in shows:
        show_name_lower = show.get("name", "").lower()

        # Check if any denylist entry is in the show name
        is_denied = any(denied in show_name_lower for denied in denylist)

        if not is_denied:
            filtered.append(show)
        else:
            log_message(f"Filtered out: {show.get('name')}")

    return filtered


def get_output_file(source: str) -> Path:
    """Get the output JSON file path for a given source."""
    if source == "HouseSeats":
        return HOUSESEATS_OUTPUT_FILE
    elif source == "1stTix":
        return FIRSTTIX_OUTPUT_FILE
    raise ValueError(f"Unknown source: {source}")


def load_source_output(source: str) -> dict:
    """Previously published output for a source ({} if missing or unreadable)."""
    output_file = get_output_file(source)
    try:
        with open(output_file, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def minutes_since_last_check(source: str, now: datetime) -> float | None:
    """Minutes since this source was last checked, or None if unknown."""
    stamp = load_source_output(source).get("last_updated")
    if not stamp:
        return None
    try:
        last = datetime.strptime(stamp, TIMESTAMP_FMT)
    except ValueError:
        return None
    return (now.replace(tzinfo=None) - last).total_seconds() / 60.0


def build_source_output(
    source: str,
    shows: list,
    previous: dict,
    scrape_successful: bool,
    error: str | None,
    now: datetime,
) -> dict:
    """Build the published JSON for one source.

    On a failed scrape the last good show list is kept (the site shows a
    warning instead of going blank) and ``consecutive_failures`` counts up so
    the health check can tell "really broken" from "one bad run"."""
    timestamp = now.strftime(TIMESTAMP_FMT)

    if scrape_successful:
        published = shows
        last_successful_run = timestamp
        consecutive_failures = 0
        status, last_error = "ok", None
    else:
        published = previous.get("shows") or []
        last_successful_run = previous.get("last_successful_run")
        try:
            consecutive_failures = int(previous.get("consecutive_failures") or 0) + 1
        except (TypeError, ValueError):
            consecutive_failures = 1
        status, last_error = "error", (error or "scrape failed")

    return {
        "source": source,
        "last_updated": timestamp,
        "last_successful_run": last_successful_run,
        "status": status,
        "consecutive_failures": consecutive_failures,
        "last_error": last_error,
        "email_enabled": EMAIL_ENABLED,
        "alert_channel": "telegram" if uses_telegram() else "email",
        "count": len(published),
        "shows": published,
    }


def save_source_shows(
    source: str, shows: list, scrape_successful: bool = False, error: str | None = None
):
    """Save shows for a single source to its own JSON file (see
    build_source_output for what happens on a failed scrape)."""
    output = build_source_output(
        source,
        shows,
        load_source_output(source),
        scrape_successful,
        error,
        get_pacific_time(),
    )
    output_file = get_output_file(source)
    write_json_atomic(output_file, output)

    if scrape_successful:
        log_message(f"Saved {len(shows)} {source} shows to {output_file.name}")
    else:
        log_message(
            f"{source} scrape failed ({output['last_error']}); kept the previous "
            f"{output['count']} shows, failure #{output['consecutive_failures']} in a row"
        )


def push_to_github():
    """Commit and push updated data files (local runs with --push only; the
    GitHub Actions workflow commits the data itself)."""
    try:
        os.chdir(SCRIPT_DIR)

        data_files = [
            "houseseats_shows.json",
            "firsttix_shows.json",
            "notified_shows.json",
            "show_history.json",
        ]
        for data_file in data_files:
            subprocess.run(["git", "add", data_file], capture_output=True)

        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"], capture_output=True
        )
        if result.returncode == 0:
            log_message("[GitHub] No changes to data files, skipping push")
            return True

        commit_msg = (
            f"Update available shows - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg], check=True, capture_output=True
        )

        # Stash any unstaged changes (e.g. local code edits) before rebase
        stash_result = subprocess.run(
            ["git", "stash", "--include-untracked"], capture_output=True, text=True
        )
        did_stash = "No local changes" not in (stash_result.stdout or "")

        # Rebase on top of remote; -X theirs keeps our (latest run) data on conflicts
        subprocess.run(
            ["git", "pull", "--rebase", "-X", "theirs"], check=True, capture_output=True
        )

        if did_stash:
            subprocess.run(["git", "stash", "pop"], capture_output=True)

        subprocess.run(["git", "push"], check=True, capture_output=True)

        log_message("[GitHub] Successfully pushed show data to GitHub")
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"[GitHub] Failed to push to GitHub: {e}")
        if e.stderr:
            log_message(
                f"[GitHub] Error: {e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr}"
            )
        return False
    except Exception as e:
        log_message(f"[GitHub] Unexpected error: {e}")
        return False


def notify_user(shows: list):
    """Send a macOS desktop notification (local runs on a Mac only).

    Show names come from the web, so they are passed to osascript as an
    argument - never through a shell."""
    if sys.platform != "darwin" or not shows:
        return

    show_names = ", ".join(s.get("name", "Unknown")[:30] for s in shows[:3])
    message = f"{len(shows)} shows available: {show_names}..."
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{escaped}" with title "HouseSeats Checker"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------------------
# Extra subscribers (friends): each gets their own denylist, watchlist, email
# and "already notified" list. Addresses live only in the SUBSCRIBERS secret;
# the public state file is keyed by a short id and never contains an address.
# ---------------------------------------------------------------------------

_SUBSCRIBER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_GIST_PAGE_RE = re.compile(
    r"^https://gist\.github\.com/([A-Za-z0-9-]+)/([0-9a-f]+)(?:/edit)?/?$"
)
_GIST_RAW_RE = re.compile(
    r"^https://gist\.githubusercontent\.com/([A-Za-z0-9-]+)/([0-9a-f]+)/raw"
)
RESERVED_SUBSCRIBER_IDS = {"owner", "me", "default", "all"}


def gist_urls(url: str) -> tuple[str, str | None]:
    """Turn whatever gist link a person pasted into (raw_url, edit_url).
    A non-gist https URL is used as-is and has no edit link."""
    url = (url or "").strip()
    page = _GIST_PAGE_RE.match(url)
    if page:
        user, gist_id = page.groups()
        return (
            f"https://gist.githubusercontent.com/{user}/{gist_id}/raw",
            f"https://gist.github.com/{user}/{gist_id}/edit",
        )
    raw = _GIST_RAW_RE.match(url)
    if raw:
        user, gist_id = raw.groups()
        return url, f"https://gist.github.com/{user}/{gist_id}/edit"
    return url, None


def _keyword_set(value) -> set:
    """Accept a JSON list or a newline/comma separated string of keywords."""
    if isinstance(value, str):
        return parse_list_lines(value.replace(",", "\n"))
    if isinstance(value, list):
        return parse_list_lines("\n".join(str(v) for v in value))
    return set()


def parse_subscribers(raw: str) -> tuple[list, list]:
    """Parse the SUBSCRIBERS secret (a JSON list). Returns (subscribers,
    problems). Problems name the entry by position/id only - never by address,
    because this text ends up in a public CI log."""
    raw = (raw or "").strip()
    if not raw:
        return [], []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], [f"SUBSCRIBERS is not valid JSON ({e.msg} at line {e.lineno})"]
    if isinstance(data, dict):
        data = data.get("subscribers", [])
    if not isinstance(data, list):
        return [], ["SUBSCRIBERS must be a JSON list of subscriber objects"]

    subscribers, problems, seen = [], [], set()
    for position, entry in enumerate(data, 1):
        if not isinstance(entry, dict):
            problems.append(f"subscriber #{position}: not an object - skipped")
            continue
        sub_id = str(entry.get("id", "")).strip().lower()
        if not _SUBSCRIBER_ID_RE.match(sub_id) or sub_id in RESERVED_SUBSCRIBER_IDS:
            problems.append(
                f"subscriber #{position}: 'id' must be a short name like \"alex\" "
                "(lowercase letters, digits, dashes) - skipped"
            )
            continue
        if sub_id in seen:
            problems.append(f"subscriber '{sub_id}': duplicate id - skipped")
            continue
        email = str(entry.get("email", "")).strip()
        if not _EMAIL_RE.match(email):
            problems.append(f"subscriber '{sub_id}': missing or invalid 'email' - skipped")
            continue
        denylist_url = str(entry.get("denylist_url", "") or "").strip()
        if denylist_url and not denylist_url.startswith("https://"):
            problems.append(f"subscriber '{sub_id}': 'denylist_url' must start with https:// - skipped")
            continue
        raw_url, edit_url = gist_urls(denylist_url) if denylist_url else ("", None)
        seen.add(sub_id)
        subscribers.append(
            {
                "id": sub_id,
                "email": email,
                "enabled": entry.get("enabled", True) is not False,
                "denylist_url": raw_url,
                "denylist_edit_url": edit_url,
                "denylist": _keyword_set(entry.get("denylist")),
                "watchlist": _keyword_set(entry.get("watchlist")),
            }
        )
    return subscribers, problems


def load_subscribers() -> list:
    subscribers, problems = parse_subscribers(os.environ.get("SUBSCRIBERS", ""))
    for problem in problems:
        log_message(f"SUBSCRIBERS config problem: {problem}")
        print(f"::warning::{problem}")
    return subscribers


def load_subscriber_state() -> dict:
    """Load the public per-subscriber state. Raises StateError when the file
    exists but is unreadable (same reasoning as load_notified_shows)."""
    if not SUBSCRIBERS_FILE.exists():
        return {}
    try:
        with open(SUBSCRIBERS_FILE, "r") as f:
            data = json.load(f)
        state = data["subscribers"]
        if not isinstance(state, dict):
            raise ValueError("'subscribers' is not an object")
        return state
    except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
        raise StateError(f"{SUBSCRIBERS_FILE.name} is unreadable: {e}") from e


def fetch_keyword_list(url: str) -> set | None:
    """Fetch a one-keyword-per-line list. None means 'could not fetch' (which
    is different from a list that is legitimately empty)."""
    for attempt in range(1, 3):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return parse_list_lines(response.text)
        except requests.RequestException as e:
            # Log the error type only: the URL identifies the subscriber's account.
            log_message(f"Denylist fetch failed (attempt {attempt}): {type(e).__name__}")
            if attempt < 2:
                time.sleep(2)
    return None


def resolve_subscriber_denylist(subscriber: dict, previous: dict) -> set | None:
    """The subscriber's inline keywords plus their own list from the web.
    If their list can't be fetched, fall back to the copy saved last run.
    Returns None when there is nothing safe to filter with - the caller then
    skips this subscriber rather than emailing them shows they opted out of."""
    keywords = set(subscriber["denylist"]) | load_hidden(subscriber["id"])
    if subscriber["denylist_url"]:
        fetched = fetch_keyword_list(subscriber["denylist_url"])
        if fetched is None:
            cached = previous.get("denylist")
            if not isinstance(cached, list):
                return None
            log_message(f"[{subscriber['id']}] using the denylist saved from the last run")
            fetched = set(cached)
        keywords |= fetched
    return keywords


def process_subscriber(subscriber: dict, all_shows: list, previous: dict, now: datetime) -> dict:
    """Filter, notify and build the new public state for one subscriber.
    ``all_shows`` is the full un-denylisted list (already marked rare)."""
    sub_id = subscriber["id"]
    state = dict(previous)

    denylist = resolve_subscriber_denylist(subscriber, previous)
    if denylist is None:
        log_message(f"[{sub_id}] could not load their denylist and no saved copy - skipped this run")
        state["last_error"] = "could not load denylist"
        return state

    notified = set(previous.get("notified") or [])
    notified, _ = cleanup_old_notified(notified, now)
    is_first_run = "notified" not in previous

    # Work on copies: watchlist/first_seen differ per person.
    shows = [dict(s) for s in all_shows if not matches_any(s.get("name", ""), denylist)]
    shows = enrich_shows(shows, previous.get("shows") or [], subscriber["watchlist"], now, notified)
    new_shows = find_new_shows(shows, notified)
    log_message(
        f"[{sub_id}] {len(shows)} shows after their denylist ({len(denylist)} keywords), "
        f"{len(new_shows)} new"
    )

    if new_shows and args.dry_run:
        log_message(f"[{sub_id}] [dry run] would email {len(new_shows)} show(s)")
    elif new_shows:
        status = send_email_notification(
            new_shows,
            recipient=subscriber["email"],
            view_url=f"{AVAILABLE_SHOWS_URL}?u={sub_id}",
            denylist_edit_url=subscriber["denylist_edit_url"],
            watchlist_edit_url=None,
            welcome=is_first_run,
            respect_global_mute=False,
            label=sub_id,
        )
        if status != "failed":
            notified.update(get_show_key(s) for s in new_shows)

    state.update(
        {
            "last_updated": now.strftime(TIMESTAMP_FMT),
            "last_error": None,
            "denylist": sorted(denylist),
            "notified": sorted(notified),
            "count": len(shows),
            "shows": shows,
        }
    )
    return state


def process_subscribers(all_shows: list, scrape_ok: bool, now: datetime) -> None:
    """Run every extra subscriber. Never raises: a problem with one friend's
    setup must not break the owner's alerts or another friend's."""
    subscribers = load_subscribers()
    if not subscribers and not SUBSCRIBERS_FILE.exists():
        return
    try:
        previous_state = load_subscriber_state()
    except StateError as e:
        log_message(f"ERROR: {e} - skipping extra subscribers so nobody gets duplicate alerts")
        print(f"::error::{e}")
        return

    new_state = {}
    for subscriber in subscribers:
        sub_id = subscriber["id"]
        previous = previous_state.get(sub_id) or {}
        if not subscriber["enabled"] or not scrape_ok or args.no_email and not args.dry_run:
            # Paused, nothing scraped, or a one-off --no-email run: leave as is.
            if not subscriber["enabled"]:
                log_message(f"[{sub_id}] paused (enabled=false)")
            new_state[sub_id] = previous
            continue
        try:
            new_state[sub_id] = process_subscriber(subscriber, all_shows, previous, now)
        except Exception as e:  # noqa: BLE001 - isolate one subscriber's failure
            log_message(f"[{sub_id}] unexpected error: {type(e).__name__} - left unchanged")
            new_state[sub_id] = previous

    # Subscribers removed from the secret drop out of the file automatically.
    if not args.dry_run:
        write_json_atomic(
            SUBSCRIBERS_FILE,
            {"last_updated": now.strftime(TIMESTAMP_FMT), "subscribers": new_state},
        )
    log_message(f"Processed {len(subscribers)} extra subscriber(s)")


def main(argv=None) -> int:
    global args
    args = build_arg_parser().parse_args(argv)

    log_message("=" * 50)
    log_message(
        "Starting Shows Checker (HouseSeats + 1stTix)"
        + (" [DRY RUN - nothing will be sent or saved]" if args.dry_run else "")
    )

    if args.telegram_test:
        return send_telegram_test()

    now = get_pacific_time()

    # Self-throttle: the schedule deliberately over-fires (GitHub drops most
    # scheduled runs), so skip when a check happened recently.
    if args.min_interval > 0 and not args.no_houseseats:
        minutes = minutes_since_last_check("HouseSeats", now)
        if minutes is not None and 0 <= minutes < args.min_interval:
            log_message(
                f"Last check was {minutes:.0f} min ago (< {args.min_interval:g}) - skipping this run"
            )
            return 0

    denylist = load_denylist() | load_hidden(OWNER_ID)
    watchlist = load_watchlist()
    if watchlist:
        log_message(f"Loaded {len(watchlist)} watchlist keyword(s)")

    # Load previously notified shows. A corrupt file stops the run: guessing
    # "nothing notified yet" would email every show currently listed.
    try:
        notified_shows = load_notified_shows()
    except StateError as e:
        log_message(f"ERROR: {e} - stopping so no duplicate alerts are sent")
        return 2
    log_message(
        f"Loaded {len(notified_shows)} previously notified show+date combinations"
    )

    # Upgrade legacy year-less keys, then prune ancient ones. Save only if
    # something changed (migration is a one-time no-op afterward).
    notified_shows, _migrated = migrate_notified_keys(notified_shows, now)
    notified_shows, _pruned = cleanup_old_notified(notified_shows, now)
    if (_migrated or _pruned) and not args.dry_run:
        save_notified_shows(notified_shows)
        log_message(
            f"notified_shows: migrated={_migrated}, pruned old entries={_pruned}; "
            f"now {len(notified_shows)} keys"
        )

    # Random initial delay
    random_delay(1.0, 5.0)

    # source -> (shows or None, error or None)
    results = {}

    if args.no_houseseats:
        log_message("--- Skipping HouseSeats (--no-houseseats flag) ---")
    else:
        log_message("--- Checking HouseSeats ---")
        results["HouseSeats"] = scrape_with_retries(
            "HouseSeats", login_houseseats, fetch_houseseats_shows
        )

    if args.no_firsttix:
        log_message("--- Skipping 1stTix (--no-firsttix flag) ---")
    else:
        random_delay(3.0, 10.0)  # pause between sites
        log_message("--- Checking 1stTix ---")
        results["1stTix"] = scrape_with_retries(
            "1stTix", login_firsttix, fetch_firsttix_shows, attempts=1
        )

    all_shows = dedupe_shows(
        [show for shows, _ in results.values() if shows for show in shows]
    )
    log_message(f"Total shows from all sources: {len(all_shows)}")

    # Load and update show history for rare detection. History covers every
    # scraped show (not just the owner's filtered list) so "rare" is also
    # right for subscribers whose denylists differ.
    show_history = load_show_history()
    show_history = update_show_history(all_shows, show_history)
    show_history = cleanup_old_history(show_history)  # drop entries older than 90 days
    if not args.dry_run:
        save_show_history(show_history)

    # Mark rare shows, then apply the owner's denylist
    all_shows = mark_rare_shows(all_shows, show_history)
    filtered_shows = filter_shows(all_shows, denylist)
    log_message(f"{len(filtered_shows)} shows after filtering")
    rare_count = sum(1 for s in filtered_shows if s.get("rare"))
    log_message(f"{rare_count} rare shows detected")

    # Add date/weekday/first-seen/watchlist fields, then publish per source
    previous_shows = [
        show
        for source in results
        for show in (load_source_output(source).get("shows") or [])
    ]
    filtered_shows = enrich_shows(filtered_shows, previous_shows, watchlist, now, notified_shows)
    watch_count = sum(1 for s in filtered_shows if s.get("watchlist"))
    if watch_count:
        log_message(f"{watch_count} watchlist match(es)")

    for source, (shows, error) in results.items():
        source_filtered = [s for s in filtered_shows if s.get("source") == source]
        if args.dry_run:
            log_message(
                f"[dry run] {source}: scrape {'ok' if shows else 'FAILED: ' + str(error)}, "
                f"{len(source_filtered)} shows would be published"
            )
        else:
            save_source_shows(
                source, source_filtered, scrape_successful=bool(shows), error=error
            )

    # Find new shows (not yet notified)
    new_shows = find_new_shows(filtered_shows, notified_shows)
    log_message(f"{len(new_shows)} new shows to notify about")

    if new_shows:
        print("\n--- NEW Shows ---")
        for show in new_shows:
            flags = (" ⭐" if show.get("watchlist") else "") + (
                " 🔥" if show.get("rare") else ""
            )
            print(
                f"  🆕 [{show.get('source', 'Unknown')}] {show.get('name', 'Unknown')}"
                f" - {format_show_date(show)}{flags}"
            )
        print()

        if args.dry_run:
            subject = build_email_subject(group_shows_by_name(new_shows))
            log_message(f"[dry run] would email with subject: {SUBJECT_PREFIX} {subject}")
        else:
            email_status = deliver_owner_alert(new_shows)
            notify_user(new_shows)

            if email_status == "failed":
                # Leave them un-notified so the next run tries again.
                log_message(
                    f"Alert could not be delivered - {len(new_shows)} shows left pending for the next run"
                )
            else:
                for show in new_shows:
                    notified_shows.add(get_show_key(show))
                save_notified_shows(notified_shows)
                log_message(f"Marked {len(new_shows)} shows as notified")
    else:
        log_message("No new shows to notify about")

    # Friends with their own denylist and email address
    process_subscribers(all_shows, scrape_ok=bool(all_shows), now=now)

    # Print all available shows summary
    print(f"\n--- All Available Shows ({len(filtered_shows)} total) ---")
    for show in filtered_shows:
        status = "✓" if get_show_key(show) in notified_shows else "🆕"
        print(
            f"  {status} [{show.get('source', 'Unknown')}] {show.get('name', 'Unknown')}"
            f" - {format_show_date(show)}"
        )

    if args.push and not args.dry_run:
        push_to_github()

    failed = [source for source, (shows, _) in results.items() if not shows]
    if failed:
        log_message(f"Checker finished with failed source(s): {', '.join(failed)}")
    else:
        log_message("Checker completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
