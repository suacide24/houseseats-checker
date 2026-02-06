# HouseSeats Checker - Project Context

## Overview
Automated checker for **lv.houseseats.com** (Las Vegas HouseSeats) AND **1sttix.org** that:
1. Logs into both member portals
2. Fetches available shows from each site
3. Filters out shows on a denylist (contains-based matching, case-insensitive)
4. Sends email notifications for NEW shows only (tracks show+date+source combinations)
5. **Detects RARE shows** - flags shows that don't appear frequently 🔥
6. Includes ChatGPT links to ask "Should I go to this show?"
7. **Runs every 30 minutes via GitHub Actions** (no local machine needed!)
8. Uses random delays between requests to avoid bot detection
9. Auto-publishes available shows to GitHub Pages
10. **All timestamps in Pacific Time (PT)**

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
| `index.html` | GitHub Pages frontend (loads shows from JSON with cache-busting) |
| `available_shows.json` | Latest fetched shows (auto-updated by GitHub Actions) |
| `notified_shows.json` | Tracks which show+date+source combos have been notified |
| `show_history.json` | Tracks show appearances over time for RARE detection |
| `requirements.txt` | Python dependencies |
| `.github/workflows/check-shows.yml` | GitHub Actions workflow (runs every 30 mins) |
| `denylist.txt` | Local fallback denylist (primary is on GitHub Gist) |

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
│                    GitHub Actions                            │
│                  (runs every 30 mins)                        │
├─────────────────────────────────────────────────────────────┤
│  1. Checkout repo                                            │
│  2. Run houseseats_checker.py                                │
│     ├── Fetch denylist from Gist                            │
│     ├── Login to HouseSeats & 1stTix                        │
│     ├── Scrape available shows                               │
│     ├── Filter out denylisted shows                          │
│     ├── Update show history & detect RARE shows             │
│     ├── Send email for NEW shows only (with RARE badges)    │
│     └── Save to available_shows.json (with PT timestamp)    │
│  3. Commit & push JSON updates                               │
│  4. GitHub Pages auto-rebuilds                               │
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

# Set environment variables
export HOUSESEATS_EMAIL="your@email.com"
export HOUSESEATS_PASSWORD="password"
export FIRSTTIX_EMAIL="your@email.com"
export FIRSTTIX_PASSWORD="password"
export SMTP_EMAIL="your@gmail.com"
export SMTP_PASSWORD="app-password"
export NOTIFICATION_EMAIL="your@email.com"

# Run manually (--fast skips delays)
python3 houseseats_checker.py --fast
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

- **Primary:** GitHub Gist at https://gist.github.com/suacide24/f1bf569e229cf1319137a4230d7db1b6
- **Fallback:** Local `denylist.txt` file
- Lines starting with `#` are ignored (comments)
- **Contains-based matching** - if "comedy" is in denylist, it filters "L.A. Comedy Club"
- Case-insensitive

## Email Features

- HTML formatted table with show name, date, ticket link
- **🔥 RARE badges** on infrequent shows
- **ChatGPT link** for each show: "🤖 Should I go?" opens ChatGPT with a prompt asking about the show
- **📋 View All Shows button:** Links to GitHub Pages
- **✏️ Edit Denylist button:** Links to Gist editor
- Color-coded by source (blue = HouseSeats, green = 1stTix)

## GitHub Pages Features

- Dark gradient theme
- Mobile-responsive grid layout
- Show images from source websites
- 🔥 RARE badges with pulsing animation
- "Get Tickets" and "Ask AI" buttons for each show
- Cache-busting ensures fresh data on every page load
- Timestamps displayed in Pacific Time (PT)

---
*Last updated: 2026-02-05*
