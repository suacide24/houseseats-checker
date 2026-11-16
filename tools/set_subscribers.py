#!/usr/bin/env python3
"""Upload subscribers.local.json to the repo's SUBSCRIBERS secret.

The subscriber list holds your friends' email addresses, so it is kept out of
the (public) repo: you edit a local, git-ignored file and this script stores
it as an encrypted GitHub Actions secret. GitHub never shows a secret back to
you, which is why the local file is the copy you edit.

    cp subscribers.example.json subscribers.local.json   # first time only
    (edit subscribers.local.json)
    python tools/set_subscribers.py            # validate + upload
    python tools/set_subscribers.py --check    # validate only

Needs: pip install pynacl requests, and a GitHub login saved in your git
credential helper (the same one `git push` uses) or a GITHUB_TOKEN env var.
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPO = os.environ.get("GITHUB_REPOSITORY", "suacide24/houseseats-checker")
LOCAL_FILE = ROOT / "subscribers.local.json"
API = "https://api.github.com"


def github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, timeout=20,
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    sys.exit("No GitHub login found. Set GITHUB_TOKEN or sign in with git first.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="validate only, upload nothing")
    parser.add_argument("--file", type=Path, default=LOCAL_FILE)
    opts = parser.parse_args()

    if not opts.file.exists():
        sys.exit(f"{opts.file.name} not found. Copy subscribers.example.json to it and edit.")
    raw = opts.file.read_text()

    import houseseats_checker as hc

    subscribers, problems = hc.parse_subscribers(raw)
    for problem in problems:
        print(f"  ✗ {problem}")
    for sub in subscribers:
        source = "own gist/URL" if sub["denylist_url"] else "inline list"
        print(
            f"  ✓ {sub['id']}: {'active' if sub['enabled'] else 'paused'}, denylist from {source}"
            f" (+{len(sub['denylist'])} inline), {len(sub['watchlist'])} watchlist keyword(s)"
            f" -> {hc.AVAILABLE_SHOWS_URL}?u={sub['id']}"
        )
    if problems:
        print("Fix the problems above first - nothing was uploaded.")
        return 1
    if opts.check:
        print(f"{len(subscribers)} subscriber(s) look good (check only, nothing uploaded).")
        return 0

    import requests
    from nacl import encoding, public

    headers = {
        "Authorization": f"Bearer {github_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    key = requests.get(f"{API}/repos/{REPO}/actions/secrets/public-key", headers=headers, timeout=30)
    key.raise_for_status()
    key = key.json()
    sealed = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    encrypted = base64.b64encode(sealed.encrypt(raw.encode())).decode()
    resp = requests.put(
        f"{API}/repos/{REPO}/actions/secrets/SUBSCRIBERS",
        headers=headers, timeout=30,
        json={"encrypted_value": encrypted, "key_id": key["key_id"]},
    )
    resp.raise_for_status()
    print(f"Uploaded {len(subscribers)} subscriber(s) to the SUBSCRIBERS secret of {REPO}.")
    print("They take effect on the next check (new subscribers get a welcome email then).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
