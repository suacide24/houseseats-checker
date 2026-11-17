#!/usr/bin/env python3
"""Dead-man's switch for the HouseSeats checker.

Every run of the main checker records its outcome in ``houseseats_shows.json``:
``last_successful_run`` advances only when a scrape really worked, and
``consecutive_failures`` counts runs that executed but could not scrape.
This script alerts by email when either signal says the checker is down:

* **Failing** - the checker is running but the last N scrapes in a row failed
  (login broken, IP blocked, page layout changed). Caught within a few runs.
* **Stale** - no successful scrape for a long time (the workflow stopped
  running altogether). The threshold is deliberately long because GitHub
  routinely delays or drops scheduled runs for many hours; a short threshold
  produces false alarms.

It is independent of the show-notification EMAIL_ENABLED switch: muting
routine show emails must never mute failure alerts. Use HEALTH_ALERTS_ENABLED
to turn these alerts off separately.

To avoid spam it alerts once when the checker goes down and once when it
recovers, tracking that transition in ``health_state.json``.
"""

from __future__ import annotations

import json
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SHOWS_FILE = SCRIPT_DIR / "houseseats_shows.json"
STATE_FILE = SCRIPT_DIR / "health_state.json"
LIVE_PAGE_URL = "https://suacide24.github.io/houseseats-checker/"
ACTIONS_URL = "https://github.com/suacide24/houseseats-checker/actions"

# Addresses and the password come only from the environment (Actions secrets).
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "") or SMTP_EMAIL

TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S PT"


def _env_flag(name: str, default: bool = True) -> bool:
    """Read an on/off env var. Empty/unset -> default; off values: 0/false/no/off."""
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


# Same prefix as the show alerts (houseseats_checker.SUBJECT_PREFIX), so one
# mail filter catches every email from this project.
SUBJECT_PREFIX = "[HouseSeats]"


def _env_float(name: str, default: float) -> float:
    """Read a numeric env var. Empty/unset/invalid -> default. (GitHub Actions
    passes an unset repo Variable as an empty string, not an absent key.)"""
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


# Master switch for failure alerts (independent of show-notification EMAIL_ENABLED).
HEALTH_ALERTS_ENABLED = _env_flag("HEALTH_ALERTS_ENABLED", True)

# Hours without a successful scrape before the checker counts as down.
# GitHub's scheduler regularly leaves gaps of 8-16 hours between runs, so
# anything much shorter than a day raises false alarms.
STALE_THRESHOLD_HOURS = _env_float("HEALTH_STALE_HOURS", 24.0)

# Scrapes that ran but failed, in a row, before the checker counts as down.
MAX_CONSECUTIVE_FAILURES = int(_env_float("HEALTH_MAX_FAILURES", 3))


def get_pacific_time() -> datetime:
    """Current Pacific wall-clock time as a naive datetime (matches the
    naive ``... PT`` timestamps written by the main checker)."""
    utc_now = datetime.now(timezone.utc)
    year = utc_now.year
    march_first = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = march_first + timedelta(days=(6 - march_first.weekday() + 7) % 7 + 7)
    nov_first = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov_first + timedelta(days=(6 - nov_first.weekday()) % 7)
    offset = timedelta(hours=-7) if dst_start <= utc_now < dst_end else timedelta(hours=-8)
    return (utc_now + offset).replace(tzinfo=None)


def evaluate_health(
    last_run_str: str | None,
    now: datetime,
    threshold_hours: float,
    already_alerted: bool,
    consecutive_failures: int = 0,
    max_failures: int = 3,
) -> dict:
    """Pure decision function. Returns a dict with:
      - age_hours: hours since last successful run (None if unknown/unparseable)
      - down: whether the checker is considered down
      - reason: 'failing' | 'stale' | None
      - action: 'alert_down' | 'alert_recovered' | 'none'
    'alert_down' fires only on the healthy->down transition, 'alert_recovered'
    only on down->healthy, so repeated runs during an outage stay quiet."""
    age_hours: float | None = None
    stale = False
    if not last_run_str:
        stale = True  # never recorded a success -> treat as down
    else:
        try:
            last = datetime.strptime(last_run_str, TIMESTAMP_FMT)
            age_hours = round((now - last).total_seconds() / 3600.0, 2)
            stale = age_hours > threshold_hours
        except ValueError:
            stale = True  # unparseable timestamp -> treat as down

    failing = max_failures > 0 and consecutive_failures >= max_failures
    down = failing or stale
    reason = "failing" if failing else ("stale" if stale else None)

    if down and not already_alerted:
        action = "alert_down"
    elif not down and already_alerted:
        action = "alert_recovered"
    else:
        action = "none"
    return {
        "age_hours": age_hours,
        "down": down,
        "stale": stale,
        "reason": reason,
        "action": action,
    }


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"alerted": False, "last_change": None}
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            return {
                "alerted": bool(data.get("alerted", False)),
                "last_change": data.get("last_change"),
            }
    except (json.JSONDecodeError, OSError, AttributeError):
        return {"alerted": False, "last_change": None}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def send_alert(subject: str, body: str) -> bool:
    """Send a plain-text alert email (with retries). Returns True on success."""
    if not (SMTP_EMAIL and SMTP_PASSWORD and NOTIFICATION_EMAIL):
        print("[health] SMTP_EMAIL / SMTP_PASSWORD / NOTIFICATION_EMAIL not set - cannot send alert")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"{SUBJECT_PREFIX} {subject}"
    msg["From"] = SMTP_EMAIL
    msg["To"] = NOTIFICATION_EMAIL
    for attempt in range(1, 4):
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
                server.starttls()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.sendmail(SMTP_EMAIL, NOTIFICATION_EMAIL, msg.as_string())
            print(f"[health] alert sent: {subject}")
            return True
        except smtplib.SMTPAuthenticationError as e:
            print(f"[health] failed to send alert (bad SMTP credentials): {e}")
            return False
        except (smtplib.SMTPException, OSError) as e:
            print(f"[health] alert attempt {attempt}/3 failed: {e}")
            if attempt < 3:
                time.sleep(5 * attempt)
    return False


def read_checker_status() -> dict:
    """Health-relevant fields from the checker's published output."""
    try:
        with open(SHOWS_FILE, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
    except (json.JSONDecodeError, OSError):
        return {}
    try:
        failures = int(data.get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        failures = 0
    return {
        "last_successful_run": data.get("last_successful_run"),
        "last_updated": data.get("last_updated"),
        "consecutive_failures": failures,
        "last_error": data.get("last_error"),
    }


def main() -> int:
    now = get_pacific_time()
    status = read_checker_status()
    last_run = status.get("last_successful_run")
    failures = status.get("consecutive_failures", 0)
    state = load_state()
    ev = evaluate_health(
        last_run,
        now,
        STALE_THRESHOLD_HOURS,
        state["alerted"],
        consecutive_failures=failures,
        max_failures=MAX_CONSECUTIVE_FAILURES,
    )
    print(
        f"[health] last_successful_run={last_run} age_h={ev['age_hours']} "
        f"threshold_h={STALE_THRESHOLD_HOURS:g} consecutive_failures={failures} "
        f"(max {MAX_CONSECUTIVE_FAILURES}) down={ev['down']} reason={ev['reason']} "
        f"action={ev['action']} alerts_enabled={HEALTH_ALERTS_ENABLED}"
    )

    now_str = now.strftime(TIMESTAMP_FMT)
    if ev["action"] == "alert_down":
        if not HEALTH_ALERTS_ENABLED:
            print("[health] would alert (down) but HEALTH_ALERTS_ENABLED is off")
            return 0
        age = ev["age_hours"]
        age_txt = f"{age:.1f} hours ago" if age is not None else "never / unknown"
        if ev["reason"] == "failing":
            what = (
                f"The checker is running, but its last {failures} scrapes in a row failed.\n"
                f"Most recent error: {status.get('last_error') or 'unknown'}\n"
                f"Last attempt: {status.get('last_updated') or 'unknown'}\n\n"
                "Likely causes: the HouseSeats login was rejected or expired, the "
                "site is blocking GitHub's servers, or the page layout changed."
            )
        else:
            what = (
                f"There has been no successful scrape for more than "
                f"{STALE_THRESHOLD_HOURS:g} hours.\n\n"
                "Likely cause: the 'Check Shows' workflow has stopped running "
                "(disabled, or GitHub is not scheduling it)."
            )
        subject = "⚠️ Checker looks DOWN"
        body = (
            f"{what}\n\n"
            f"Last successful run: {last_run or 'unknown'} ({age_txt})\n"
            f"Checked at: {now_str}\n\n"
            f"Workflow runs: {ACTIONS_URL}\n"
            f"Live page: {LIVE_PAGE_URL}\n"
        )
        if send_alert(subject, body):
            save_state({"alerted": True, "last_change": now_str})
    elif ev["action"] == "alert_recovered":
        if not HEALTH_ALERTS_ENABLED:
            # Alerts turned off during an outage; clear the flag silently so a
            # future outage can alert again.
            save_state({"alerted": False, "last_change": now_str})
            return 0
        subject = "✅ Checker RECOVERED"
        body = (
            "The HouseSeats checker is succeeding again.\n\n"
            f"Last successful run: {last_run} ({ev['age_hours']:.1f} hours ago)\n"
            f"Checked at: {now_str}\n"
        )
        if send_alert(subject, body):
            save_state({"alerted": False, "last_change": now_str})
    # action == 'none': steady state, leave state file untouched (no commit churn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
