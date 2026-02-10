# HouseSeats Checker - Project Context

## Overview
Automated checker for **lv.houseseats.com** (Las Vegas HouseSeats) AND **1sttix.org** that:
1. Logs into both member portals
2. Fetches available shows from each site
3. Filters out shows on a denylist (contains-based matching, case-insensitive)
4. Sends email notifications for NEW shows only (tracks show+date+source combinations)
5. **Detects RARE shows** - flags shows that don't appear frequently 🔥
6. Includes ChatGPT links to ask "Should I go to this show?"
7. **Dual runners:** GitHub Actions (HouseSeats) + local macOS launchd (1stTix)
8. Uses random delays between requests to avoid bot detection
9. Auto-publishes available shows to GitHub Pages
10. **All timestamps in Pacific Time (PT)**
11. **Groups shows by name** - both website and emails group multiple time slots under each show
12. **Graceful failure handling** - login failures skip writing that source's file, preserving previous data
13. **`last_successful_run` timestamp** - confirms scripts are actively running (gated on ≥1 show)

## Live Pages

| Link | Purpose |
|------|---------|
| **[View Available Shows](https://suacide24.github.io/houseseats-checker/)** | Beautiful, mobile-friendly page with all current shows |
| **[Edit Denylist](https://gist.github.com/suacide24/f1bf569e229cf1319137a4230d7db1b6/edit)** | Add shows to filter out |
| **[GitHub Actions](https://github.com/suacide24/houseseats-checker/actions)** | View workflow runs and logs |

## Key Files

| File | Purpose |
|------|---------|
| `houseseats_checker.py` | Main script |
| `index.html` | GitHub Pages frontend (fetches both per-source JSON files, merges client-side) |
| `houseseats_shows.json` | HouseSeats shows data (written by GitHub Actions) |
| `firsttix_shows.json` | 1stTix shows data (written by local launchd) |
| `notified_shows.json` | Tracks which show+date+source combos have been notified |
| `show_history.json` | Tracks show appearances over time for RARE detection |
| `requirements.txt` | Python dependencies |
| `.github/workflows/check-shows.yml` | GitHub Actions workflow (runs every 30 mins, HouseSeats only) |
| `denylist.txt` | Local fallback denylist (primary is on GitHub Gist) |
| `run.sh` | Local wrapper script with credentials (in .gitignore) |
| `com.rsua.houseseats-checker.plist` | macOS launchd config for local scheduled runs |

## Architecture: Per-Source JSON Files

Each show source gets its own JSON file to **eliminate merge conflicts** between runners:

```
┌──────────────────────────┐     ┌──────────────────────────┐
│     GitHub Actions        │     │     Local macOS launchd   │
│  (runs every 30 mins)     │     │   (runs every 30 mins)    │
│  --fast --no-firsttix     │     │   --fast                  │
├──────────────────────────┤     ├──────────────────────────┤
│ Writes:                   │     │ Writes:                   │
│  houseseats_shows.json    │     │  firsttix_shows.json      │
│  notified_shows.json      │     │  notified_shows.json      │
│  show_history.json        │     │  show_history.json        │
└───────────┬──────────────┘     └───────────┬──────────────┘
            │     git commit & push          │
            └───────────┬────────────────────┘
                        ▼
              GitHub Pages serves
              index.html which fetches
              BOTH JSON files and merges
              them client-side
```

**Why separate files?**
- GitHub Actions only checks HouseSeats (`--no-firsttix`), writes `houseseats_shows.json`
- Local launchd checks 1stTix (and optionally HouseSeats), writes `firsttix_shows.json`
- Each runner only modifies its own file → **no merge conflicts possible**
- `push_to_github()` uses `git stash` + `git pull --rebase -X theirs` + `git push` for safety

**Per-source JSON structure:**
```json
{
  "source": "HouseSeats",
  "last_updated": "2026-02-09T17:50:01 PT",
  "last_successful_run": "2026-02-09T17:50:01 PT",
  "count": 5,
  "shows": [...]
}
```

- `last_updated` — timestamp of when the file was last written
- `last_successful_run` — only updates when ≥1 show is found (confirms a real successful run, not a failed scrape)

## 🔥 RARE Show Detection

Shows are flagged as **RARE** when they appear infrequently. This helps identify special/limited engagements.

| Setting | Value |
|---------|-------|
| Lookback period | 30 days |
| Rare threshold | < 3 appearances |
| History cleanup | 90 days (old entries auto-removed) |

**How it works:**
- `show_history.json` tracks every unique show appearance by date
- Shows appearing fewer than 3 times in the last 30 days get the 🔥 RARE badge
- First-time shows are always marked as RARE
- Old history (> 90 days) is automatically cleaned up
- Pulsing animation on the website to draw attention

## Configuration

All credentials are stored as **GitHub Secrets** (not in code):

| Secret | Purpose |
|--------|---------|
| `HOUSESEATS_EMAIL` | HouseSeats login email |
| `HOUSESEATS_PASSWORD` | HouseSeats password |
| `FIRSTTIX_EMAIL` | 1stTix login email |
| `FIRSTTIX_PASSWORD` | 1stTix password |
| `SMTP_EMAIL` | Gmail sender address |
| `SMTP_PASSWORD` | Gmail App Password (16-char) |
| `NOTIFICATION_EMAIL` | Email to receive notifications |

To update secrets:
```bash
gh secret set SECRET_NAME --body "value"
```

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│          Runner (GitHub Actions or local launchd)            │
├─────────────────────────────────────────────────────────────┤
│  1. Run houseseats_checker.py                                │
│     ├── Fetch denylist from Gist                            │
│     ├── Login to HouseSeats and/or 1stTix                   │
│     ├── Scrape available shows                               │
│     ├── Filter out denylisted shows                          │
│     ├── Update show history & detect RARE shows             │
│     ├── Send email for NEW shows only (with RARE badges)    │
│     ├── Save houseseats_shows.json (if HouseSeats checked)  │
│     └── Save firsttix_shows.json (if 1stTix checked)        │
│  2. git stash → pull --rebase → stash pop → push            │
│  3. GitHub Pages auto-rebuilds                               │
└─────────────────────────────────────────────────────────────┘
```

## Manual Operations

```bash
# Trigger workflow manually
gh workflow run check-shows.yml

# View recent runs
gh run list --limit 5

# View logs from a run
gh run view <run-id> --log

# Reset notifications (will re-notify for all shows)
# Edit notified_shows.json on GitHub and set: {"notified": []}

# Reset RARE detection (all shows will be marked RARE again)
# Edit show_history.json on GitHub and set: {"shows": {}}
```

## Local Development (Optional)

```bash
# Clone repo
git clone https://github.com/suacide24/houseseats-checker.git
cd houseseats-checker

# Install dependencies
pip3 install -r requirements.txt

# Option 1: Use the wrapper script (recommended)
# Edit run.sh with your credentials first, then:
./run.sh --fast                    # Check both sources
./run.sh --fast --no-houseseats    # Check only 1stTix
./run.sh --fast --no-firsttix      # Check only HouseSeats

# Option 2: Set environment variables manually
export HOUSESEATS_EMAIL="your@email.com"
export HOUSESEATS_PASSWORD="password"
export FIRSTTIX_EMAIL="your@email.com"
export FIRSTTIX_PASSWORD="password"
export SMTP_EMAIL="your@gmail.com"
export SMTP_PASSWORD="app-password"
export NOTIFICATION_EMAIL="your@email.com"
python3 houseseats_checker.py --fast
```

## Data Safety

**Login failures preserve existing data:**
- If HouseSeats login fails, `houseseats_shows.json` is not overwritten
- If 1stTix login fails, `firsttix_shows.json` is not overwritten
- Only sources that successfully authenticate get their file refreshed
- `last_successful_run` only updates when ≥1 show is found, so you can always tell when the last real scrape happened

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

- **Primary:** GitHub Gist at https://gist.github.com/suacide24/f1bf569e229cf1319137a4230d7db1b6
- **Fallback:** Local `denylist.txt` file
- Lines starting with `#` are ignored (comments)
- **Contains-based matching** - if "comedy" is in denylist, it filters "L.A. Comedy Club"
- Case-insensitive

## Email Features

- **Grouped by show name** - shows with multiple time slots are consolidated into one card
- HTML formatted cards with source badge, show name, and all time slots
- **🔥 RARE badges** on infrequent shows
- Each time slot is a clickable ticket link
- **ChatGPT link** for each show: "🤖 Ask AI about this show"
- **📋 View All Shows button:** Links to GitHub Pages
- **✏️ Edit Denylist button:** Links to Gist editor
- Color-coded by source (blue = HouseSeats, green = 1stTix)
- Subject line shows unique show count and total time slots

## GitHub Pages Features

- Dark gradient theme
- Mobile-responsive grid layout
- **Fetches both `houseseats_shows.json` and `firsttix_shows.json`** and merges client-side
- **Shows grouped by name** - each show card displays all available time slots
- Show images from source websites
- 🔥 RARE badges with pulsing animation
- Clickable time slots link directly to ticket pages
- "Ask AI" button for each show
- Cache-busting ensures fresh data on every page load
- Timestamps displayed in Pacific Time (PT)
- Shows unique show count and total time slot count
- **✅ Last successful run** timestamp to confirm scripts are actively running

---

## ❌ Abandoned Project: GoWild Flight Checker

### What We Tried
Attempted to create a similar checker for **Frontier Airlines GoWild Pass** flights from Las Vegas:
- Project location: `/Users/rsua/gowild-checker/` (deleted)
- GitHub repo: `suacide24/gowild-checker` (deleted)
- Goal: Check available GoWild flights daily at midnight, send email notifications, similar format to HouseSeats

### Why It Failed: PerimeterX Bot Protection

Frontier Airlines uses **PerimeterX** - a sophisticated anti-bot/CAPTCHA system that actively blocks automated browsers:

```
<iframe id="px-captcha-modal"></iframe> intercepts pointer events
```

**What we tried:**
- Playwright with Chromium (headless and visible modes)
- Firefox with stealth mode settings
- Custom user agents and anti-detection scripts
- Various login URL endpoints
- Press-and-hold CAPTCHA bypass attempts

**All attempts failed** because:
1. PerimeterX presents a "Press & Hold" human verification popup
2. The CAPTCHA iframe intercepts ALL click events on the page
3. Even with stealth mode, the bot detection is too sophisticated
4. Unlike HouseSeats (which has no bot protection), Frontier actively invests in preventing automation

### Research Findings

| Option | Exists? |
|--------|---------|
| Official email alerts from Frontier | ❌ No |
| Public API for GoWild availability | ❌ No |
| Push notifications for specific routes | ❌ No |
| Third-party sanctioned apps | ❌ No |

### How Commercial Services (like The 1491 Club) Do It

Services like [the1491club.com](https://www.the1491club.com/) succeed by investing in:
- **CAPTCHA-solving services** (2Captcha, Anti-Captcha) - paid human solvers
- **Residential proxy networks** - rotating IPs to avoid detection
- **Mobile app API reverse-engineering** - capturing internal API calls
- **Professional infrastructure** - servers, session management, 24/7 monitoring

This level of infrastructure is beyond a simple DIY script.

### Conclusion

**GoWild automation is not feasible** with a simple self-hosted script. Options for GoWild users:
1. Subscribe to a paid service like The 1491 Club
2. Join community Discord/Telegram groups for manual sightings
3. Check the website/app manually at strategic times (midnight, 6am)

---
*Last updated: 2026-02-10*
