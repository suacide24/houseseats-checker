# HouseSeats Checker - Project Context

## Overview
Checks **lv.houseseats.com** (Las Vegas HouseSeats) for free show tickets and:
1. Logs in and scrapes the available shows (with timeouts and retries)
2. Filters out shows on the denylist; stars shows on the watchlist
3. Emails NEW show+date combinations only (once each, year-aware)
4. Flags RARE shows 🔥 (seen fewer than 3 times in 30 days)
5. Publishes the list to GitHub Pages
6. Lets friends subscribe at their own email with their own denylist and page
7. Runs on **GitHub Actions** only; a second workflow alerts if the checker stops working

1stTix (1sttix.org) support is still in the code but is not run by any workflow
(`--no-firsttix`); the site hides the 1stTix section unless that data is fresh.

## Live Pages

| Link | Purpose |
|------|---------|
| [Available shows](https://suacide24.github.io/houseseats-checker/) | The site: search, sort, NEW / watchlist / RARE badges, status banner |
| [Edit denylist](https://gist.github.com/suacide24/f1bf569e229cf1319137a4230d7db1b6/edit) | Shows to hide |
| [Edit watchlist](https://github.com/suacide24/houseseats-checker/edit/main/watchlist.txt) | Shows to star |
| [Actions](https://github.com/suacide24/houseseats-checker/actions) | Runs, logs, "Run workflow" |
| [Variables](https://github.com/suacide24/houseseats-checker/settings/variables/actions) | Email on/off and alert thresholds |

## Key Files

| File | Purpose |
|------|---------|
| `houseseats_checker.py` | Scraper, notifier, data writer |
| `check_health.py` | Dead-man's switch: emails when the checker is failing or stale, and when it recovers |
| `index.html` | GitHub Pages frontend (reads the JSON files below) |
| `houseseats_shows.json` | Published shows + checker status |
| `notified_shows.json` | `source|name|YYYY-MM-DD` keys already emailed (pruned after 400 days) |
| `show_history.json` | Appearance history for RARE detection |
| `health_state.json` | Whether a "down" alert is currently outstanding |
| `watchlist.txt` | Keywords for shows you especially want |
| `subscribers.json` | Per-friend show list and notified keys (no addresses) |
| `subscribers.example.json` | Template for the git-ignored `subscribers.local.json` |
| `FRIENDS.md` | How to add, pause and remove a friend |
| `denylists/<id>.txt` | Shows hidden with the one-click link (`owner.txt` is yours) |
| `tools/handle_hide_issue.py` | Validates a "Hide for <id>: <show>" issue and updates `denylists/` |
| `TRIGGER.md` | How to set up the outside timer |
| `tools/set_subscribers.py` | Uploads `subscribers.local.json` as the `SUBSCRIBERS` secret |
| `denylist.txt` | Local fallback for the denylist gist |
| `tests/` | Unit tests (`python -m unittest discover -s tests`) |
| `.github/workflows/check-shows.yml` | Runs the checker |
| `.github/workflows/health-check.yml` | Runs the health check every 3 hours |
| `.github/workflows/hide-show.yml` | Runs when a "Hide this show" issue is submitted |
| `.github/workflows/tests.yml` | Runs the tests on code changes |

## Friends (extra subscribers)

Friends can get alerts at their own address with their own denylist and their
own page (`?u=<id>`). Setup is in [FRIENDS.md](FRIENDS.md). In short: the
`SUBSCRIBERS` secret (a JSON list, uploaded from the git-ignored
`subscribers.local.json` by `tools/set_subscribers.py`) holds the addresses;
the checker writes the public, address-free `subscribers.json` with each
friend's show list, cached denylist and notified keys. One friend's broken
setup never affects the owner or another friend.

## One-click "Hide this show"

Every show in an alert email and on the site has a 🙈 link. It opens a
pre-filled GitHub issue titled `Hide for <id>: <show name>`; pressing Create
runs `hide-show.yml`, which appends the name to `denylists/<id>.txt`, replies
and closes the issue. The checker combines that file with the person's own
denylist. The repo owner may hide for anyone; a friend only for their own id
(recognised because their GitHub name owns the gist configured for that id).
The issue title is untrusted input: it reaches the script only as an
environment variable and is validated there. To un-hide, delete the line.

## Scheduling (read this before "fixing" the cron)

GitHub delays and silently drops scheduled workflow runs. With a `*/30` cron this
repo really got between 4 and 20 runs a day, never 48. So `check-shows.yml` asks
four times an hour at off-peak minutes (`7,22,37,52`) and the checker skips a
scheduled run if it already checked in the last 20 minutes (`--min-interval 20`).
Manual runs are never skipped.
An outside timer can start checks on time instead; setup is in
[TRIGGER.md](TRIGGER.md) (it dispatches the workflow with `throttle=true`, which
applies the same 20-minute skip). Because gaps of many hours are normal, the health
check only calls the checker stale after 24 hours.

## Published JSON

```json
{
  "source": "HouseSeats",
  "last_updated": "2026-11-16T08:46:01 PT",
  "last_successful_run": "2026-11-16T08:46:01 PT",
  "status": "ok",
  "consecutive_failures": 0,
  "last_error": null,
  "email_enabled": true,
  "count": 5,
  "shows": [{"name": "...", "date": "November 20th", "date_iso": "2026-11-20",
             "weekday": "Fri", "link": "...", "image": "...", "rare": false,
             "watchlist": false, "first_seen": "2026-11-13T08:58:01 PT"}]
}
```

`first_seen` drives the NEW badge (shown for 24 hours). It is carried over
from the previous output; a show date never notified before gets the current
time; one that was already notified but has no recorded time gets `null`, so
it is not shown as NEW.

`subscribers.json` has the same per-show fields, per friend:

```json
{
  "last_updated": "2026-11-16T12:09:29 PT",
  "subscribers": {
    "alex": {"last_updated": "...", "last_error": null, "count": 5,
             "denylist": ["comedy"], "notified": ["HouseSeats|name|2026-11-20"],
             "shows": [...]}
  }
}
```

The file only exists once a friend has been added. It must never contain an
email address or a gist address.

A failed scrape (login rejected, request error, or zero shows parsed) keeps the
previously published shows, sets `status` to `error`, and counts up
`consecutive_failures`. The site shows a warning banner in that state.

## Reliability rules the code follows

- A show is marked notified only if the email was sent, or email is switched off.
  If sending fails, the next run tries again.
- A corrupt `notified_shows.json` stops the run (exit code 2) rather than being
  treated as empty, which would re-email everything.
- JSON files are written atomically (temp file + rename).
- The workflow retries its `git push` up to 5 times; both workflows share one
  concurrency group so their pushes do not race.
- Text scraped from the site is HTML-escaped in the email and on the page.
- Every email subject (show alerts, friend welcome, health alerts) starts with
  `[HouseSeats]` so one mail filter catches them all.
- RARE history covers every scraped show, not only the owner's filtered list,
  so the badge is also right for friends with different denylists.
- Friends are isolated: a bad entry, an unreachable denylist or a failed send
  for one friend is logged by id only and never stops the owner's alerts or
  another friend's. An unreachable denylist falls back to the copy saved at
  the last check; with no saved copy that friend is skipped for the run.
- A corrupt `subscribers.json` skips all friends for the run (the owner still
  runs) rather than re-emailing them everything.

## Configuration

Secrets (Settings > Secrets and variables > Actions > Secrets):
`HOUSESEATS_EMAIL`, `HOUSESEATS_PASSWORD`, `SMTP_EMAIL`, `SMTP_PASSWORD` (Gmail
app password), `NOTIFICATION_EMAIL`, and optionally `FIRSTTIX_EMAIL`,
`FIRSTTIX_PASSWORD`, `SUBSCRIBERS` (friends; see FRIENDS.md). No address or
password is stored in the code.

Variables (same page, Variables tab; all optional):

| Variable | Default | Meaning |
|----------|---------|---------|
| `EMAIL_ENABLED` | on | `false` mutes your own new-show emails (not friends') |
| `HEALTH_ALERTS_ENABLED` | on | `false` mutes failure alerts |
| `HEALTH_STALE_HOURS` | 24 | Hours without a successful check before alerting |
| `HEALTH_MAX_FAILURES` | 3 | Failed scrapes in a row before alerting |

## Command line

```bash
python houseseats_checker.py --fast --no-firsttix   # what CI runs
  --dry-run            # scrape and log only: no email, no files written
  --no-email           # skip email for this run
  --min-interval 20    # skip if the last check was under 20 minutes ago
  --push               # commit and push data files (local runs only; CI does its own)
```

In Actions, "Run workflow" on Check Shows has a **dry run** checkbox.

## Manual operations

- Add, pause or remove a friend: edit `subscribers.local.json`, then run
  `python tools/set_subscribers.py` (`--check` validates without uploading)
- Re-notify everything: set `notified_shows.json` to `{"notified": []}`
- Reset RARE detection: set `show_history.json` to `{"shows": {}}`

## Site structure (HouseSeats)

- Login: POST `https://lv.houseseats.com/member/index.bv` with `email`, `password`, `submit=login`, `lastplace=`
- Shows: `https://lv.houseseats.com/member/ajax/upcoming-shows.bv` returns HTML;
  each show is a `.panel-default` with name in `.panel-heading a`, date in
  `.grid-cal-date`, image in `.img-responsive`

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
*Last updated: 2026-11-16*
