# Adding a friend

Each friend gets:

- alerts at **their own email address**, in a separate message (nobody sees anyone else's address)
- **their own denylist**, which they edit themselves, plus an optional watchlist
- **their own page**: `https://suacide24.github.io/houseseats-checker/?u=<their id>`

Your own alerts, denylist and page are unaffected by anything a friend sets.

## What the friend does (once, about 2 minutes)

1. Sign in to GitHub (a free account is enough) and open <https://gist.github.com>.
2. Create a gist with one file, for example `denylist.txt`. One keyword per line;
   any show whose name contains a keyword is hidden from them. Lines starting
   with `#` are comments. It can start empty.
3. Send you the gist's address and the email address they want alerts at.

After that they manage their list on their own: every alert email has an
**Edit Denylist** button that opens their gist, and each show has a
**🙈 Hide this show** link: it opens a pre-filled GitHub issue, and pressing
Create hides that show for them (they must be signed in to the GitHub account
that owns their gist). Changes apply at the next check.

No GitHub account? Skip the gist and give them an inline `"denylist"` instead
(see `sam` in the example). You then edit it for them.

## What you do

```bash
cp subscribers.example.json subscribers.local.json   # first time only
```

Edit `subscribers.local.json` (it is git-ignored, so addresses never reach the
public repo), then upload it:

```bash
python tools/set_subscribers.py
```

That validates the file and stores it as the encrypted `SUBSCRIBERS` secret.
GitHub never shows a secret back, so `subscribers.local.json` is the copy you
keep and edit. Use `--check` to validate without uploading. (First time:
`pip install pynacl requests`.)

Prefer the browser? Paste the same JSON into Settings > Secrets and variables >
Actions > Secrets > `SUBSCRIBERS`.

### Fields

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Short public name (`alex`): lowercase letters, digits, dashes. Appears in their page address and in `subscribers.json`. |
| `email` | yes | Where their alerts go. Stored only in the secret. |
| `denylist_url` | no | Their gist address (page, edit or raw link all work), or any https URL of a text file. |
| `denylist` | no | Extra keywords kept in this file. Combined with `denylist_url`. |
| `watchlist` | no | Keywords to star and sort first for them. |
| `enabled` | no | `false` pauses their alerts without losing their history. |

## What to expect

- At the next check a new friend gets one **welcome email** listing everything
  currently available to them; after that, only new show dates.
- Pause someone with `"enabled": false`; remove their entry to unsubscribe them.
  Either way, run the upload again.
- `EMAIL_ENABLED=false` mutes only your own alerts, not your friends'.
- If a friend's gist can't be reached, the copy saved at the last check is used.
  If there is no saved copy yet, they are skipped until it loads — they are
  never sent shows they opted out of.
- All alerts are sent from your `SMTP_EMAIL` account; a friend who replies
  reaches you.

## What is public

The repo is public, so `subscribers.json` (written by the checker) is too. It
holds, per id: their current show list, their denylist keywords, and which
show dates they were already told about. It never holds an email address or a
gist address. Pick an `id` your friend is comfortable having visible.

Shows hidden with the 🙈 link are public as well: the issue they submit and
the line added to `denylists/<id>.txt`. The issue shows their GitHub name next
to their id.
