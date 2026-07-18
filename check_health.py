#!/usr/bin/env python3
"""Dead-man's switch for the HouseSeats checker.

The main workflow writes ``last_successful_run`` into ``houseseats_shows.json``
only when a scrape actually succeeds (logged in + found shows). This script
reads that timestamp and emails an alert if it is too stale — catching BOTH
failure modes: the scrape running but silently failing (login broken, IP
blocked, 0 shows) and the workflow not running at all (the JSON stops updating).

It is intentionally independent of the show-notification EMAIL_ENABLED switch:
muting routine show emails must never mute failure alerts. Use
HEALTH_ALERTS_ENABLED to turn these alerts off separately.

To avoid spam it alerts once when the checker goes stale and once when it
recovers, tracking that transition in ``health_state.json``.
"""

from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
SHOWS_FILE = SCRIPT_DIR / "houseseats_shows.json"
STATE_FILE = SCRIPT_DIR / "health_state.json"
LIVE_PAGE_URL = "https://suacide24.github.io/houseseats-checker/"

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "rsua95@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "rsua95@gmail.com")

TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S PT"


def _env_flag(name: str, default: bool = True) -> bool:
    """Read an on/off env var. Empty/unset -> default; off values: 0/false/no/off."""
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


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

# Hours since the last successful scrape before we consider the checker down.
# Runs are nominally every 30 min but GitHub cron jitter pushes them 75-90 min
# apart with occasional multi-hour gaps, so 6h avoids false alarms while still
# catching a real outage within a few hours.
STALE_THRESHOLD_HOURS = _env_float("HEALTH_STALE_HOURS", 6.0)


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
) -> dict:
    """Pure decision function. Returns a dict with:
      - age_hours: hours since last successful run (None if unknown/unparseable)
      - stale: whether the checker is considered down
      - action: 'alert_down' | 'alert_recovered' | 'none'
    'alert_down' fires only on the healthy->stale transition, 'alert_recovered'
    only on stale->healthy, so repeated runs during an outage stay quiet."""
    age_hours: float | None = None
    if not last_run_str:
        stale = True  # never recorded a success -> treat as down
    else:
        try:
            last = datetime.strptime(last_run_str, TIMESTAMP_FMT)
            age_hours = round((now - last).total_seconds() / 3600.0, 2)
            stale = age_hours > threshold_hours
        except ValueError:
            stale = True  # unparseable timestamp -> treat as down
    if stale and not already_alerted:
        action = "alert_down"
    elif not stale and already_alerted:
        action = "alert_recovered"
    else:
        action = "none"
    return {"age_hours": age_hours, "stale": stale, "action": action}


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
    except (json.JSONDecodeError, IOError):
        return {"alerted": False, "last_change": None}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_alert(subject: str, body: str) -> bool:
    """Send a plain-text alert email. Returns True on success."""
    if not SMTP_PASSWORD:
        print("[health] SMTP_PASSWORD not set - cannot send alert")
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = NOTIFICATION_EMAIL
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, NOTIFICATION_EMAIL, msg.as_string())
        print(f"[health] alert sent to {NOTIFICATION_EMAIL}: {subject}")
        return True
    except Exception as e:  # noqa: BLE001 - report any send failure, keep going
        print(f"[health] failed to send alert: {e}")
        return False


def _read_last_successful_run() -> str | None:
    if not SHOWS_FILE.exists():
        return None
    try:
        with open(SHOWS_FILE, "r") as f:
            return json.load(f).get("last_successful_run")
    except (json.JSONDecodeError, IOError):
        return None


def main() -> int:
    now = get_pacific_time()
    last_run = _read_last_successful_run()
    state = load_state()
    ev = evaluate_health(last_run, now, STALE_THRESHOLD_HOURS, state["alerted"])
    print(
        f"[health] last_successful_run={last_run} age_h={ev['age_hours']} "
        f"threshold_h={STALE_THRESHOLD_HOURS} stale={ev['stale']} "
        f"action={ev['action']} alerts_enabled={HEALTH_ALERTS_ENABLED}"
    )

    now_str = now.strftime(TIMESTAMP_FMT)
    if ev["action"] == "alert_down":
        if not HEALTH_ALERTS_ENABLED:
            print("[health] would alert (down) but HEALTH_ALERTS_ENABLED is off")
            return 0
        age = ev["age_hours"]
        age_txt = f"{age:.1f} hours ago" if age is not None else "never / unknown"
        subject = "⚠️ HouseSeats checker looks DOWN"
        body = (
            "The HouseSeats checker has not had a successful scrape recently.\n\n"
            f"Last successful run: {last_run or 'unknown'} ({age_txt})\n"
            f"Alert threshold: {STALE_THRESHOLD_HOURS:g} hours\n"
            f"Checked at: {now_str}\n\n"
            "Likely causes: HouseSeats login expired, the site blocked the "
            "GitHub Actions IP, a scrape/HTML change, or the workflow stopped "
            "running. Check the Actions tab for the 'Check Shows' workflow.\n\n"
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
        subject = "✅ HouseSeats checker RECOVERED"
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
