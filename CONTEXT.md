# HouseSeats Checker - Project Context

## Overview
Automated checker for **lv.houseseats.com** (Las Vegas HouseSeats) AND **1sttix.org** that:
1. Logs into both member portals
2. Fetches available shows from each site
3. Filters out shows on a denylist (contains-based matching, case-insensitive)
4. Sends email notifications for NEW shows only (tracks show+date+source combinations)
5. Includes ChatGPT links to ask "Should I go to this show?"
6. Runs hourly via launchd
7. Uses random delays between requests to avoid bot detection

## Key Files

| File | Purpose |
|------|---------|
| `houseseats_checker.py` | Main script |
| `denylist.txt` | Shows to ignore (one per line, contains-match) |
| `notified_shows.json` | Tracks which show+date+source combos have been notified |
| `available_shows.json` | Latest fetched shows (JSON output) |
| `houseseats.log` | Log file with timestamps |
| `stdout.log` / `stderr.log` | Output from scheduled runs |
| `requirements.txt` | Python dependencies |
| `com.rsua.houseseats-checker.plist` | launchd config (runs hourly) |

## Configuration (in houseseats_checker.py)

```python
# HouseSeats credentials
HOUSESEATS_EMAIL = "rsua95@gmail.com"
HOUSESEATS_PASSWORD = "easypass"

# 1stTix credentials
FIRSTTIX_EMAIL = "ryan.sua.rn@gmail.com"
FIRSTTIX_PASSWORD = "Clayton24!"

# Email notifications
NOTIFICATION_EMAIL = "rsua95@gmail.com"
SMTP_EMAIL = "rsua95@gmail.com"
SMTP_PASSWORD = "<Gmail App Password>"  # 16-char app password
```

## How to Run

```bash
# Manual run
/usr/bin/python3 /Users/rsua/houseseats-checker/houseseats_checker.py

# Install dependencies
pip3 install -r /Users/rsua/houseseats-checker/requirements.txt
```

**Important:** Must use `/usr/bin/python3` (system Python 3.9) because Meta's internal Python at `/usr/local/bin/python3` doesn't have pip/packages.

## Scheduled Runs (launchd)

The plist is installed at `~/Library/LaunchAgents/com.rsua.houseseats-checker.plist`

```bash
# Check status
launchctl list | grep houseseats

# Manual trigger
launchctl start com.rsua.houseseats-checker

# Stop scheduler
launchctl unload ~/Library/LaunchAgents/com.rsua.houseseats-checker.plist

# Restart after changes
launchctl unload ~/Library/LaunchAgents/com.rsua.houseseats-checker.plist
launchctl load ~/Library/LaunchAgents/com.rsua.houseseats-checker.plist
```

## Site Structure (for future reference)

### HouseSeats
- **Login URL:** `https://lv.houseseats.com/member/index.bv`
- **Login form fields:** `email`, `password`, `submit=login`, `lastplace=`
- **Shows AJAX endpoint:** `https://lv.houseseats.com/member/ajax/upcoming-shows.bv`
- **Show data:** Returns HTML with `.panel-default` divs containing show info
  - Name: `.panel-heading a`
  - Date: `.grid-cal-date`
  - Image: `.img-responsive`

### 1stTix
- **Login URL:** `https://www.1sttix.org/login`
- **Login form fields:** `email`, `password`
- **Events URL:** `https://www.1sttix.org/tixer/get-tickets/events`
- **Event data:** Returns HTML with `div.event` containers
  - Name: `img[alt]` or `.entry-title`
  - Date: `.entry-meta` (parsed with regex)
  - Link: `a[href*="get-tickets/event"]`

## Denylist Behavior

- One pattern per line in `denylist.txt`
- Lines starting with `#` are ignored (comments)
- **Contains-based matching** - if "comedy" is in denylist, it filters "L.A. Comedy Club"
- Case-insensitive

## Notification System

- Tracks `show_name|date` combinations in `notified_shows.json`
- Only sends email/notification for NEW combinations
- To reset and get notified about all shows again:
  ```bash
  rm /Users/rsua/houseseats-checker/notified_shows.json
  ```

## Email Features

- HTML formatted table with show name, date, ticket link
- **ChatGPT link** for each show: "🤖 Should I go?" opens ChatGPT with a prompt asking about the show
- Also sends macOS desktop notification

## Common Tasks

```bash
# Add show to denylist
echo "carrot top" >> /Users/rsua/houseseats-checker/denylist.txt

# View current shows
cat /Users/rsua/houseseats-checker/available_shows.json

# Check logs
tail -50 /Users/rsua/houseseats-checker/houseseats.log

# Reset notifications (will re-notify for all shows)
rm /Users/rsua/houseseats-checker/notified_shows.json
```

---
*Last updated: 2026-02-04*
