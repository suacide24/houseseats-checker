#!/usr/bin/python3
"""
HouseSeats.com Show Checker
Logs into lv.houseseats.com and fetches available shows,
filtering out any shows on the denylist.
Sends email notifications for new shows (only once per show+date).
"""

import argparse
import json
import os
import random
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote


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

# Parse command line arguments
parser = argparse.ArgumentParser(
    description="Check HouseSeats and 1stTix for available shows"
)
parser.add_argument(
    "--fast", "--no-delay", action="store_true", help="Skip random delays (for testing)"
)
parser.add_argument(
    "--no-houseseats", action="store_true", help="Skip HouseSeats checking"
)
parser.add_argument("--no-firsttix", action="store_true", help="Skip 1stTix checking")
args = parser.parse_args()

# Configuration - HouseSeats
HOUSESEATS_BASE_URL = "https://lv.houseseats.com"
HOUSESEATS_LOGIN_URL = f"{HOUSESEATS_BASE_URL}/member/index.bv"
HOUSESEATS_SHOWS_URL = f"{HOUSESEATS_BASE_URL}/member/ajax/upcoming-shows.bv"
HOUSESEATS_EMAIL = os.environ.get("HOUSESEATS_EMAIL", "rsua95@gmail.com")
HOUSESEATS_PASSWORD = os.environ.get("HOUSESEATS_PASSWORD", "")

# Configuration - 1stTix
FIRSTTIX_BASE_URL = "https://www.1sttix.org"
FIRSTTIX_LOGIN_URL = f"{FIRSTTIX_BASE_URL}/login"
FIRSTTIX_EVENTS_URL = f"{FIRSTTIX_BASE_URL}/tixer/get-tickets/events"
FIRSTTIX_EMAIL = os.environ.get("FIRSTTIX_EMAIL", "ryan.sua.rn@gmail.com")
FIRSTTIX_PASSWORD = os.environ.get("FIRSTTIX_PASSWORD", "")

# Email configuration
NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "rsua95@gmail.com")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "rsua95@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# Paths
SCRIPT_DIR = Path(__file__).parent
DENYLIST_FILE = SCRIPT_DIR / "denylist.txt"
OUTPUT_FILE = SCRIPT_DIR / "available_shows.json"
LOG_FILE = SCRIPT_DIR / "houseseats.log"
NOTIFIED_FILE = SCRIPT_DIR / "notified_shows.json"
HISTORY_FILE = SCRIPT_DIR / "show_history.json"

# Rare show detection settings
RARE_THRESHOLD_DAYS = 30  # Look back this many days
RARE_THRESHOLD_COUNT = 3  # Show is "rare" if seen fewer than this many times

# Denylist Gist configuration
DENYLIST_GIST_RAW_URL = "https://gist.githubusercontent.com/suacide24/f1bf569e229cf1319137a4230d7db1b6/raw/denylist.txt"
DENYLIST_GIST_EDIT_URL = (
    "https://gist.github.com/suacide24/f1bf569e229cf1319137a4230d7db1b6/edit"
)

# Available shows GitHub Pages URL (update this after setting up GitHub Pages)
AVAILABLE_SHOWS_URL = "https://suacide24.github.io/houseseats-checker/"


def log_message(message: str):
    """Log a message with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    print(log_entry)
    with open(LOG_FILE, "a") as f:
        f.write(log_entry + "\n")


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


def create_session_with_random_ua() -> requests.Session:
    """Create a requests session with a random user agent."""
    session = requests.Session()
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
    denylist = set()

    # Try to fetch from Gist first
    try:
        log_message("Fetching denylist from Gist...")
        response = requests.get(DENYLIST_GIST_RAW_URL, timeout=10)
        response.raise_for_status()

        for line in response.text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                denylist.add(line.lower())

        log_message(f"Loaded {len(denylist)} shows from Gist denylist")
        return denylist

    except requests.RequestException as e:
        log_message(f"Failed to fetch Gist denylist: {e}")
        log_message("Falling back to local denylist file...")

    # Fallback to local file
    if not DENYLIST_FILE.exists():
        log_message("No local denylist file found, creating empty one")
        DENYLIST_FILE.touch()
        return set()

    with open(DENYLIST_FILE, "r") as f:
        denylist = {
            line.strip().lower()
            for line in f
            if line.strip() and not line.startswith("#")
        }

    log_message(f"Loaded {len(denylist)} shows from local denylist")
    return denylist


def load_notified_shows() -> set:
    """Load the set of already-notified show+date combinations."""
    if not NOTIFIED_FILE.exists():
        return set()

    try:
        with open(NOTIFIED_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("notified", []))
    except (json.JSONDecodeError, IOError):
        return set()


def save_notified_shows(notified: set):
    """Save the set of notified show+date combinations."""
    with open(NOTIFIED_FILE, "w") as f:
        json.dump({"notified": list(notified)}, f, indent=2)


def load_show_history() -> dict:
    """Load the show history tracking data."""
    if not HISTORY_FILE.exists():
        return {"shows": {}}

    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"shows": {}}


def save_show_history(history: dict):
    """Save the show history tracking data."""
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def get_show_name_key(show: dict) -> str:
    """Generate a key based on show name and source (ignoring date)."""
    name = show.get("name", "").strip().lower()
    source = show.get("source", "").strip()
    return f"{source}|{name}"


def update_show_history(shows: list, history: dict) -> dict:
    """Update history with today's shows and return updated history."""
    today = datetime.now().strftime("%Y-%m-%d")

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
    cutoff_date = datetime.now() - timedelta(days=RARE_THRESHOLD_DAYS)
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
    cutoff_date = datetime.now() - timedelta(days=max_age_days)
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


def get_show_key(show: dict) -> str:
    """Generate a unique key for a show+date+source combination."""
    name = show.get("name", "").strip()
    date = show.get("date", "").strip()
    source = show.get("source", "").strip()
    return f"{source}|{name}|{date}"


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


def find_new_shows(shows: list, notified: set) -> list:
    """Find shows that haven't been notified yet."""
    new_shows = []
    for show in shows:
        key = get_show_key(show)
        if key not in notified:
            new_shows.append(show)
    return new_shows


def group_shows_by_name(shows: list) -> list:
    """Group shows by name + source, collecting all time slots together."""
    grouped = {}

    for show in shows:
        key = f"{show.get('source', 'Unknown')}|{show.get('name', 'Unknown')}"

        if key not in grouped:
            grouped[key] = {
                "name": show.get("name", "Unknown"),
                "source": show.get("source", "Unknown"),
                "image": show.get("image"),
                "rare": show.get("rare", False),
                "time_slots": [],
            }

        grouped[key]["time_slots"].append(
            {"date": show.get("date", "N/A"), "link": show.get("link", "")}
        )

        # If any slot is rare, mark the show as rare
        if show.get("rare"):
            grouped[key]["rare"] = True

    # Convert to list and sort by name
    return sorted(grouped.values(), key=lambda x: x["name"].lower())


def send_email_notification(new_shows: list) -> bool:
    """Send an email notification about new shows, grouped by show name."""
    if not new_shows:
        return True

    if not SMTP_PASSWORD:
        log_message("SMTP_PASSWORD not set - skipping email notification")
        log_message("To enable email: set SMTP_PASSWORD to a Gmail App Password")
        return False

    try:
        # Group shows by name + source
        grouped_shows = group_shows_by_name(new_shows)
        total_slots = len(new_shows)

        # Build email content
        subject = f"🎭 Shows Alert: {len(grouped_shows)} New Show(s) Available! ({total_slots} time slots)"

        # HTML body
        html_body = """
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
        <div style="background: linear-gradient(135deg, #e74c3c, #c0392b); padding: 20px; text-align: center;">
            <h1 style="color: white; margin: 0;">🎭 New Shows Available!</h1>
            <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0 0;">"""
        html_body += f"{len(grouped_shows)} show(s) • {total_slots} time slot(s)"
        html_body += """</p>
        </div>
        <div style="padding: 20px;">
        """

        for show in grouped_shows:
            name = show["name"]
            source = show["source"]
            is_rare = show["rare"]
            time_slots = show["time_slots"]

            # Color code by source
            source_bg = "#3498db" if source == "HouseSeats" else "#27ae60"

            # Rare badge
            rare_badge = (
                '<span style="background: linear-gradient(135deg, #e74c3c, #c0392b); color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px; margin-left: 8px; font-weight: bold;">🔥 RARE</span>'
                if is_rare
                else ""
            )

            # ChatGPT link
            chatgpt_link = get_chatgpt_link(show)

            html_body += f"""
            <div style="background: #f8f9fa; border-radius: 8px; padding: 15px; margin-bottom: 15px; border-left: 4px solid {source_bg};">
                <div style="margin-bottom: 10px;">
                    <span style="background: {source_bg}; color: white; padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; text-transform: uppercase;">{source}</span>
                    {rare_badge}
                </div>
                <h2 style="margin: 0 0 12px 0; color: #333; font-size: 18px;">{name}</h2>
                <div style="margin-bottom: 12px;">
                    <div style="color: #e67e22; font-size: 13px; font-weight: 500; margin-bottom: 8px;">📅 Available Times ({len(time_slots)})</div>
                    <div style="display: flex; flex-wrap: wrap; gap: 8px;">
            """

            for slot in time_slots:
                date = slot["date"]
                link = slot["link"]
                if link:
                    html_body += f'<a href="{link}" style="display: inline-block; background: rgba(231,76,60,0.1); border: 1px solid rgba(231,76,60,0.3); color: #c0392b; padding: 6px 12px; border-radius: 6px; text-decoration: none; font-size: 13px;">🎟️ {date}</a>'
                else:
                    html_body += f'<span style="display: inline-block; background: #eee; color: #666; padding: 6px 12px; border-radius: 6px; font-size: 13px;">{date}</span>'

            html_body += f"""
                    </div>
                </div>
                <div>
                    <a href="{chatgpt_link}" style="color: #666; font-size: 12px; text-decoration: none;">🤖 Ask AI about this show</a>
                </div>
            </div>
            """

        html_body += f"""
        </div>
        <div style="padding: 20px; background: #f8f9fa; text-align: center; border-top: 1px solid #eee;">
            <a href="{AVAILABLE_SHOWS_URL}" style="display: inline-block; background: #3498db; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 500; margin-right: 10px;">📋 View All Shows</a>
            <a href="{DENYLIST_GIST_EDIT_URL}" style="display: inline-block; background: #6c757d; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 500;">✏️ Edit Denylist</a>
        </div>
        <div style="padding: 15px; text-align: center; color: #999; font-size: 11px;">
            Automated message from Shows Checker (HouseSeats + 1stTix)
        </div>
        </div>
        </body>
        </html>
        """

        # Plain text fallback (also grouped)
        text_body = f"🎭 New Shows Available!\n"
        text_body += f"{len(grouped_shows)} show(s) • {total_slots} time slot(s)\n"
        text_body += "=" * 40 + "\n\n"

        for show in grouped_shows:
            name = show["name"]
            source = show["source"]
            is_rare = show["rare"]
            time_slots = show["time_slots"]
            rare_text = " 🔥 RARE" if is_rare else ""

            text_body += f"[{source}] {name}{rare_text}\n"
            text_body += f"  📅 {len(time_slots)} time slot(s):\n"
            for slot in time_slots:
                date = slot["date"]
                link = slot["link"]
                text_body += f"    • {date}"
                if link:
                    text_body += f"\n      {link}"
                text_body += "\n"
            text_body += f"  🤖 Ask AI: {get_chatgpt_link(show)}\n"
            text_body += "\n"

        text_body += f"\n📋 View All Shows: {AVAILABLE_SHOWS_URL}\n"
        text_body += f"✏️ Edit Denylist: {DENYLIST_GIST_EDIT_URL}\n"

        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = NOTIFICATION_EMAIL

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        # Send email
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, NOTIFICATION_EMAIL, msg.as_string())

        log_message(f"Email sent successfully to {NOTIFICATION_EMAIL}")
        return True

    except Exception as e:
        log_message(f"Failed to send email: {e}")
        return False


def login_houseseats(session: requests.Session) -> bool:
    """Log into houseseats.com and return True if successful."""
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

        # Check if login was successful by looking for logout link or member content
        if "logout" in response.text.lower() or "welcome" in response.text.lower():
            log_message("[HouseSeats] Successfully logged in")
            return True
        else:
            log_message(
                "[HouseSeats] Login may have failed - checking for error messages"
            )
            soup = BeautifulSoup(response.text, "html.parser")
            error = soup.find(class_=["error", "alert", "alert-danger"])
            if error:
                log_message(f"[HouseSeats] Login error: {error.get_text(strip=True)}")
            # Check if we're still on login page
            if "member login" in response.text.lower():
                log_message(
                    "[HouseSeats] Still on login page - credentials may be incorrect"
                )
                return False
            return True  # Might have logged in even without explicit indicator

    except requests.RequestException as e:
        log_message(f"[HouseSeats] Login request failed: {e}")
        return False


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


def fetch_houseseats_shows(session: requests.Session) -> list:
    """Fetch the list of available shows from HouseSeats AJAX endpoint."""
    try:
        # Add AJAX header
        session.headers.update({"X-Requested-With": "XMLHttpRequest"})

        response = session.get(HOUSESEATS_SHOWS_URL)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        shows = []

        # Find all show panels (each panel is a show)
        panels = soup.find_all("div", class_="panel-default")

        for panel in panels:
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
                            show_info["link"] = (
                                f"{HOUSESEATS_BASE_URL}/member/{href[2:]}"
                            )
                        elif not href.startswith("http"):
                            show_info["link"] = f"{HOUSESEATS_BASE_URL}{href}"
                        else:
                            show_info["link"] = href

            # Get date from grid-cal-date
            date_elem = panel.find("div", class_="grid-cal-date")
            if date_elem:
                show_info["date"] = date_elem.get_text(strip=True)

            # Get image URL
            img = panel.find("img", class_="img-responsive")
            if img and img.get("src"):
                src = img.get("src")
                if not src.startswith("http"):
                    show_info["image"] = f"{HOUSESEATS_BASE_URL}{src}"
                else:
                    show_info["image"] = src

            # Only add if we have a name
            if show_info.get("name"):
                shows.append(show_info)

        log_message(f"[HouseSeats] Found {len(shows)} shows")
        return shows

    except requests.RequestException as e:
        log_message(f"[HouseSeats] Failed to fetch shows: {e}")
        return []


def fetch_firsttix_shows(session: requests.Session) -> list:
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
                    import re

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
        return []


def extract_show_info(element) -> dict:
    """Extract show information from a BeautifulSoup element."""
    try:
        # Try to find show name
        name_elem = element.find(class_=["title", "name", "show-name", "event-name"])
        if not name_elem:
            name_elem = element.find(["h2", "h3", "h4", "a"])

        if not name_elem:
            return None

        name = name_elem.get_text(strip=True)
        if not name:
            return None

        # Try to find date
        date_elem = element.find(class_=["date", "time", "show-date", "event-date"])
        date = date_elem.get_text(strip=True) if date_elem else ""

        # Try to find venue
        venue_elem = element.find(class_=["venue", "location", "show-venue"])
        venue = venue_elem.get_text(strip=True) if venue_elem else ""

        # Try to find ticket availability
        tickets_elem = element.find(class_=["tickets", "availability", "seats"])
        tickets = tickets_elem.get_text(strip=True) if tickets_elem else ""

        # Get link if available
        link_elem = element.find("a", href=True)
        link = link_elem["href"] if link_elem else ""
        if link and not link.startswith("http"):
            link = BASE_URL + link

        return {
            "name": name,
            "date": date,
            "venue": venue,
            "tickets": tickets,
            "link": link,
        }
    except Exception:
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


def save_shows(shows: list, sources_checked: list = None):
    """Save the available shows to a JSON file with per-source timestamps.

    Merges new shows with existing shows from sources not checked this run.
    """
    pt_now = get_pacific_time()
    timestamp = pt_now.strftime("%Y-%m-%dT%H:%M:%S PT")

    # Load existing data to preserve shows and timestamps for sources not checked this run
    existing_timestamps = {}
    existing_shows = []
    if OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, "r") as f:
                existing_data = json.load(f)
                existing_timestamps = existing_data.get("last_updated_by_source", {})
                existing_shows = existing_data.get("shows", [])
        except (json.JSONDecodeError, IOError):
            pass

    # Update timestamps only for sources that were checked
    if sources_checked is None:
        sources_checked = []

    last_updated_by_source = existing_timestamps.copy()
    for source in sources_checked:
        last_updated_by_source[source] = timestamp

    # Merge shows: keep existing shows from sources NOT checked this run
    # and add new shows from sources that WERE checked
    merged_shows = []

    # Add existing shows from sources not checked this run
    for show in existing_shows:
        if show.get("source") not in sources_checked:
            merged_shows.append(show)

    # Add new shows from sources that were checked
    merged_shows.extend(shows)

    output = {
        "last_updated": timestamp,
        "last_updated_by_source": last_updated_by_source,
        "count": len(merged_shows),
        "shows": merged_shows,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    log_message(f"Saved {len(merged_shows)} shows to {OUTPUT_FILE}")


def push_to_github():
    """Commit and push updated shows to GitHub for GitHub Pages."""
    import subprocess

    try:
        # Change to script directory
        os.chdir(SCRIPT_DIR)

        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "status", "--porcelain", "available_shows.json"],
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            log_message("[GitHub] No changes to available_shows.json, skipping push")
            return True

        # Stage the file
        subprocess.run(
            ["git", "add", "available_shows.json"],
            check=True,
            capture_output=True,
        )

        # Commit with timestamp
        commit_msg = (
            f"Update available shows - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            check=True,
            capture_output=True,
        )

        # Push to origin
        subprocess.run(
            ["git", "push"],
            check=True,
            capture_output=True,
        )

        log_message("[GitHub] Successfully pushed available_shows.json to GitHub")
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
    """Send a macOS notification about available shows."""
    if shows:
        show_names = ", ".join(s.get("name", "Unknown")[:30] for s in shows[:3])
        message = f"{len(shows)} shows available: {show_names}..."
    else:
        message = "No shows available (or all filtered)"

    # Use osascript to send notification
    os.system(
        f"""osascript -e 'display notification "{message}" with title "HouseSeats Checker"' """
    )


def main():
    log_message("=" * 50)
    log_message("Starting Shows Checker (HouseSeats + 1stTix)")

    # Load denylist
    denylist = load_denylist()

    # Load previously notified shows
    notified_shows = load_notified_shows()
    log_message(
        f"Loaded {len(notified_shows)} previously notified show+date combinations"
    )

    # Create session with random user agent
    session = create_session_with_random_ua()
    log_message(f"Using User-Agent: {session.headers.get('User-Agent', '')[:50]}...")

    all_shows = []
    sources_checked = []

    # Random initial delay
    random_delay(1.0, 5.0)

    # Fetch from HouseSeats
    if args.no_houseseats:
        log_message("--- Skipping HouseSeats (--no-houseseats flag) ---")
    else:
        log_message("--- Checking HouseSeats ---")
        if login_houseseats(session):
            sources_checked.append(
                "HouseSeats"
            )  # Only mark as checked if login succeeds
            random_delay(2.0, 6.0)
            houseseats_shows = fetch_houseseats_shows(session)
            all_shows.extend(houseseats_shows)
        else:
            log_message("[HouseSeats] Failed to login, preserving existing shows")

    # Random delay between sites
    random_delay(3.0, 10.0)

    # Fetch from 1stTix
    if args.no_firsttix:
        log_message("--- Skipping 1stTix (--no-firsttix flag) ---")
    else:
        # Create new session for 1stTix (separate cookies, fresh user agent)
        session_1sttix = create_session_with_random_ua()
        log_message(
            f"Using User-Agent: {session_1sttix.headers.get('User-Agent', '')[:50]}..."
        )

        log_message("--- Checking 1stTix ---")
        if login_firsttix(session_1sttix):
            sources_checked.append("1stTix")  # Only mark as checked if login succeeds
            random_delay(2.0, 6.0)
            firsttix_shows = fetch_firsttix_shows(session_1sttix)
            all_shows.extend(firsttix_shows)
        else:
            log_message("[1stTix] Failed to login, preserving existing shows")

    log_message(f"Total shows from all sources: {len(all_shows)}")

    # Filter shows
    filtered_shows = filter_shows(all_shows, denylist)
    log_message(f"{len(filtered_shows)} shows after filtering")

    # Load and update show history for rare detection
    show_history = load_show_history()
    show_history = update_show_history(filtered_shows, show_history)
    show_history = cleanup_old_history(
        show_history
    )  # Remove entries older than 90 days
    save_show_history(show_history)

    # Mark rare shows
    filtered_shows = mark_rare_shows(filtered_shows, show_history)
    rare_count = sum(1 for s in filtered_shows if s.get("rare"))
    log_message(f"{rare_count} rare shows detected")

    # Save results
    save_shows(filtered_shows, sources_checked)

    # Push to GitHub for GitHub Pages
    push_to_github()

    # Find new shows (not yet notified)
    new_shows = find_new_shows(filtered_shows, notified_shows)
    log_message(f"{len(new_shows)} new shows to notify about")

    # Send notifications for new shows only
    if new_shows:
        # Send email notification
        send_email_notification(new_shows)

        # Send macOS notification
        notify_user(new_shows)

        # Mark these shows as notified
        for show in new_shows:
            notified_shows.add(get_show_key(show))
        save_notified_shows(notified_shows)
        log_message(f"Marked {len(new_shows)} shows as notified")

        # Print new shows to stdout
        print("\n--- NEW Shows (Just Notified) ---")
        for show in new_shows:
            source = show.get("source", "Unknown")
            print(f"  🆕 [{source}] {show.get('name', 'Unknown')}")
            if show.get("date"):
                print(f"     Date: {show['date']}")
            if show.get("link"):
                print(f"     Link: {show['link']}")
            print()
    else:
        log_message("No new shows to notify about")
        print("\n--- No New Shows ---")
        print("All available shows have already been notified.")

    # Print all available shows summary
    print(f"\n--- All Available Shows ({len(filtered_shows)} total) ---")
    for show in filtered_shows:
        key = get_show_key(show)
        source = show.get("source", "Unknown")
        status = "✓" if key in notified_shows else "🆕"
        print(
            f"  {status} [{source}] {show.get('name', 'Unknown')} - {show.get('date', 'N/A')}"
        )

    log_message("Checker completed successfully")


if __name__ == "__main__":
    main()
