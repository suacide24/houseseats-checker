#!/usr/bin/env python3
"""Store one GitHub Actions secret for this repo, typed with hidden input.

    python3 tools/set_secret.py TELEGRAM_BOT_TOKEN

The value is encrypted on this computer and sent straight to GitHub; it is not
written to any file or shown on screen. Needs `pip install pynacl requests`
and a GitHub login saved in your git credential helper (or GITHUB_TOKEN).
"""

from __future__ import annotations

import base64
import getpass
import re
import sys

from set_subscribers import API, REPO, github_token


def main() -> int:
    if len(sys.argv) != 2 or not re.match(r"^[A-Z][A-Z0-9_]*$", sys.argv[1]):
        print("Usage: python3 tools/set_secret.py SECRET_NAME")
        return 1
    name = sys.argv[1]
    value = getpass.getpass(f"Value for {name} (hidden): ").strip()
    if not value:
        print("Nothing entered - nothing was changed.")
        return 1

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
    response = requests.put(
        f"{API}/repos/{REPO}/actions/secrets/{name}",
        headers=headers, timeout=30,
        json={"encrypted_value": base64.b64encode(sealed.encrypt(value.encode())).decode(), "key_id": key["key_id"]},
    )
    response.raise_for_status()
    print(f"✓ {name} saved as a secret of {REPO}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
