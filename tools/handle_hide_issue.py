#!/usr/bin/env python3
"""Handle a "Hide for <id>: <show name>" issue (run by hide-show.yml).

The issue title and author are untrusted input from a public repo, so they
arrive as environment variables (never interpolated into a shell command) and
are validated here. Result: a line appended to denylists/<id>.txt, plus a
reply for the workflow to post.

Who may hide for whom:
- the repo owner, for anyone
- a subscriber, for their own id - recognised because their GitHub name is
  the owner of the denylist gist configured for that id
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import houseseats_checker as hc  # noqa: E402

TITLE_RE = re.compile(r"^\s*hide for\s+([a-z0-9][a-z0-9-]{0,30})\s*:\s*(.+?)\s*$", re.IGNORECASE)
GIST_OWNER_RE = re.compile(r"^https://gist\.github\.com/([A-Za-z0-9-]+)/")
MAX_NAME = 120


def clean_show_name(raw: str) -> str | None:
    name = " ".join(raw.split())  # one line, single spaces
    if not name or len(name) > MAX_NAME or name.startswith("#"):
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return None
    return name


def decide(title: str, author: str, repo_owner: str, subscribers: list) -> tuple[str | None, str | None, str]:
    """Return (person_id, show_name, reply). person_id is None when refused."""
    match = TITLE_RE.match(title or "")
    if not match:
        return None, None, "I couldn't read that. The title should look like `Hide for alex: Show Name`."
    person_id, name = match.group(1).lower(), clean_show_name(match.group(2))
    if name is None:
        return None, None, "That show name isn't usable (empty, too long, or contains odd characters)."

    author = (author or "").lower()
    is_owner = bool(author) and author == (repo_owner or "").lower()
    subscriber = next((s for s in subscribers if s["id"] == person_id), None)
    if person_id != hc.OWNER_ID and subscriber is None:
        return None, None, f"There is no list named `{person_id}`."
    if not is_owner:
        gist_owner = GIST_OWNER_RE.match((subscriber or {}).get("denylist_edit_url") or "")
        if not gist_owner or gist_owner.group(1).lower() != author:
            return None, None, f"Only the owner of the `{person_id}` list can change it."
    return person_id, name, ""


def add_hidden(person_id: str, name: str) -> bool:
    """Append the show to the person's hidden list. False if already there."""
    path = hc.HIDDEN_DIR / f"{person_id}.txt"
    if name.lower() in hc.load_list_file(path):
        return False
    hc.HIDDEN_DIR.mkdir(exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + name + "\n")
    return True


def main() -> int:
    subscribers, _ = hc.parse_subscribers(os.environ.get("SUBSCRIBERS", ""))
    person_id, name, reply = decide(
        os.environ.get("ISSUE_TITLE", ""),
        os.environ.get("ISSUE_AUTHOR", ""),
        os.environ.get("REPO_OWNER", ""),
        subscribers,
    )
    changed = False
    if person_id:
        changed = add_hidden(person_id, name)
        reply = (
            f"Done - **{name}** is hidden for `{person_id}` from the next check on."
            if changed
            else f"**{name}** was already hidden for `{person_id}`."
        )
        reply += " To undo, the repo owner can delete its line in `denylists/" + person_id + ".txt`."

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
            f.write(f"reply<<HIDE_REPLY_EOF\n{reply}\nHIDE_REPLY_EOF\n")
    print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
