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
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# Parse command line arguments
parser = argparse.ArgumentParser(
    description="Check HouseSeats and 1stTix for available shows"
)
parser.add_argument(
    "--fast", "--no-delay", action="store_true", help="Skip random delays (for testing)"
)
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


def random_delay(min_seconds: float = 2.0, max_seconds: float = 8.0):
    """Wait a random amount of time to avoid bot detection."""
    if args.fast:
        return  # Skip delays in fast mode
    delay = random.uniform(min_seconds, max_seconds)
    log_message(f"Waiting {delay:.1f} seconds...")
    time.sleep(delay)


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


def send_email_notification(new_shows: list) -> bool:
    """Send an email notification about new shows."""
    if not new_shows:
        return True

    if not SMTP_PASSWORD:
        log_message("SMTP_PASSWORD not set - skipping email notification")
        log_message("To enable email: set SMTP_PASSWORD to a Gmail App Password")
        return False

    try:
        # Build email content
        subject = f"🎭 Shows Alert: {len(new_shows)} New Show(s) Available!"

        # HTML body
        html_body = """
        <html>
        <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #e74c3c;">🎭 New Shows Available!</h2>
        <p>The following new shows are now available:</p>
        <table style="border-collapse: collapse; width: 100%;">
        <tr style="background-color: #f8f9fa;">
            <th style="padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6;">Source</th>
            <th style="padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6;">Show</th>
            <th style="padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6;">Date</th>
            <th style="padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6;">Tickets</th>
            <th style="padding: 10px; text-align: left; border-bottom: 2px solid #dee2e6;">Ask ChatGPT</th>
        </tr>
        """

        for show in new_shows:
            name = show.get("name", "Unknown")
            date = show.get("date", "N/A")
            source = show.get("source", "Unknown")
            link = show.get("link", "")
            link_html = f'<a href="{link}">View Tickets</a>' if link else "N/A"
            chatgpt_link = get_chatgpt_link(show)
            chatgpt_html = f'<a href="{chatgpt_link}">🤖 Should I go?</a>'

            # Color code by source
            source_color = "#3498db" if source == "HouseSeats" else "#27ae60"
            source_html = f'<span style="color: {source_color}; font-weight: bold;">{source}</span>'

            html_body += f"""
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #dee2e6;">{source_html}</td>
                <td style="padding: 10px; border-bottom: 1px solid #dee2e6;"><strong>{name}</strong></td>
                <td style="padding: 10px; border-bottom: 1px solid #dee2e6;">{date}</td>
                <td style="padding: 10px; border-bottom: 1px solid #dee2e6;">{link_html}</td>
                <td style="padding: 10px; border-bottom: 1px solid #dee2e6;">{chatgpt_html}</td>
            </tr>
            """

        html_body += f"""
        </table>
        <p style="margin-top: 20px;">
            <a href="{AVAILABLE_SHOWS_URL}" style="background-color: #3498db; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-right: 10px;">📋 View All Shows</a>
            <a href="{DENYLIST_GIST_EDIT_URL}" style="background-color: #6c757d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">✏️ Edit Denylist</a>
        </p>
        <p style="margin-top: 20px; color: #6c757d; font-size: 12px;">
            This is an automated message from Shows Checker (HouseSeats + 1stTix).
        </p>
        </body>
        </html>
        """

        # Plain text fallback
        text_body = f"New Shows Available!\n\n"
        for show in new_shows:
            source = show.get("source", "Unknown")
            text_body += f"• [{source}] {show.get('name', 'Unknown')} - {show.get('date', 'N/A')}\n"
            if show.get("link"):
                text_body += f"  Tickets: {show['link']}\n"
            text_body += f"  Ask ChatGPT: {get_chatgpt_link(show)}\n"
            text_body += "\n"

        text_body += f"\nView All Shows: {AVAILABLE_SHOWS_URL}\n"
        text_body += f"Edit Denylist: {DENYLIST_GIST_EDIT_URL}\n"

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

        # Check if login was successful
        if (
            "logout" in response.text.lower()
            or "my account" in response.text.lower()
            or "welcome" in response.url.lower()
        ):
            log_message("[1stTix] Successfully logged in")
            return True
        else:
            log_message("[1stTix] Login may have failed")
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
    """Fetch the list of available shows from 1stTix."""
    try:
        response = session.get(FIRSTTIX_EVENTS_URL)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
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

        # Find all event divs
        events = soup.find_all("div", class_="event")

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
            link_elem = event.find("a", href=lambda x: x and "get-tickets/event" in x)
            if link_elem:
                show_info["link"] = link_elem.get("href", "")

            # Get image URL
            if img and img.get("src"):
                show_info["image"] = img.get("src")

            # Only add if we have a name
            if show_info.get("name"):
                name_lower = show_info["name"].lower()

                # Skip if it matches sponsor/ad patterns
                is_sponsor = any(pattern in name_lower for pattern in sponsor_patterns)

                # Skip if no event link (likely a sponsor/promo)
                has_event_link = bool(show_info.get("link"))

                # Skip if no date (likely not a real event)
                has_date = bool(show_info.get("date"))

                if is_sponsor:
                    log_message(f"[1stTix] Skipping sponsor/ad: {show_info['name']}")
                elif not has_event_link or not has_date:
                    log_message(
                        f"[1stTix] Skipping non-event (no link/date): {show_info['name']}"
                    )
                else:
                    shows.append(show_info)

        log_message(f"[1stTix] Found {len(shows)} shows")
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


def save_shows(shows: list):
    """Save the available shows to a JSON file."""
    output = {
        "last_updated": datetime.now().isoformat(),
        "count": len(shows),
        "shows": shows,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    log_message(f"Saved {len(shows)} shows to {OUTPUT_FILE}")


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

    # Create session with headers
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
    )

    all_shows = []

    # Random initial delay
    random_delay(1.0, 5.0)

    # Fetch from HouseSeats
    log_message("--- Checking HouseSeats ---")
    if login_houseseats(session):
        random_delay(2.0, 6.0)
        houseseats_shows = fetch_houseseats_shows(session)
        all_shows.extend(houseseats_shows)
    else:
        log_message("[HouseSeats] Failed to login, skipping")

    # Random delay between sites
    random_delay(3.0, 10.0)

    # Create new session for 1stTix (separate cookies)
    session_1sttix = requests.Session()
    session_1sttix.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
    )

    # Fetch from 1stTix
    log_message("--- Checking 1stTix ---")
    if login_firsttix(session_1sttix):
        random_delay(2.0, 6.0)
        firsttix_shows = fetch_firsttix_shows(session_1sttix)
        all_shows.extend(firsttix_shows)
    else:
        log_message("[1stTix] Failed to login, skipping")

    log_message(f"Total shows from all sources: {len(all_shows)}")

    # Filter shows
    filtered_shows = filter_shows(all_shows, denylist)
    log_message(f"{len(filtered_shows)} shows after filtering")

    # Save results
    save_shows(filtered_shows)

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
