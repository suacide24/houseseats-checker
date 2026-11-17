#!/usr/bin/env python3
"""Create (or update) the cron-job.org timer that starts the checker.

Run it yourself in a terminal; it asks for the two keys with hidden input and
stores them nowhere on this computer:

    python3 tools/setup_timer.py

It first proves the GitHub token works by starting one (throttled) check, then
creates a cron-job.org job that does the same every 30 minutes. Run it again
any time to replace an expired GitHub token - the existing job is updated.
See TRIGGER.md for what the two keys are and how to make them.
"""

from __future__ import annotations

import getpass
import json
import sys
import urllib.error
import urllib.request

REPO = "suacide24/houseseats-checker"
DISPATCH_URL = f"https://api.github.com/repos/{REPO}/actions/workflows/check-shows.yml/dispatches"
DISPATCH_BODY = json.dumps({"ref": "main", "inputs": {"throttle": "true"}})
CRON_API = "https://api.cron-job.org"
JOB_TITLE = "houseseats check"
MINUTES = [5, 35]


def call(method: str, url: str, headers: dict, body: str | None = None) -> tuple[int, str]:
    request = urllib.request.Request(
        url, data=body.encode() if body is not None else None, method=method,
        headers={"User-Agent": "houseseats-timer-setup", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }


def build_job(github_token: str) -> dict:
    return {
        "job": {
            "title": JOB_TITLE,
            "url": DISPATCH_URL,
            "enabled": True,
            "saveResponses": True,
            "requestMethod": 1,  # POST
            "schedule": {
                "timezone": "UTC", "expiresAt": 0,
                "hours": [-1], "mdays": [-1], "months": [-1], "wdays": [-1],
                "minutes": MINUTES,
            },
            "extendedData": {"headers": github_headers(github_token), "body": DISPATCH_BODY},
        }
    }


def main() -> int:
    github_token = getpass.getpass("GitHub token (github_pat_...): ").strip()
    cron_key = getpass.getpass("cron-job.org API key: ").strip()
    if not github_token or not cron_key:
        print("Both keys are needed - nothing was changed.")
        return 1

    status, text = call("POST", DISPATCH_URL, github_headers(github_token), DISPATCH_BODY)
    if status != 204:
        hint = {
            401: "GitHub did not accept the token (mistyped or expired).",
            403: "The token lacks the Actions read/write permission for this repo.",
            404: "The token was not given access to the houseseats-checker repo.",
        }.get(status, text[:200])
        print(f"GitHub token test failed (HTTP {status}): {hint}\nNothing was created.")
        return 1
    print("✓ GitHub token works (a check was just started).")

    cron_headers = {"Authorization": f"Bearer {cron_key}", "Content-Type": "application/json"}
    status, text = call("GET", f"{CRON_API}/jobs", cron_headers)
    if status != 200:
        print(f"cron-job.org did not accept the API key (HTTP {status}). Nothing was created.")
        return 1
    existing = [j for j in json.loads(text).get("jobs", []) if j.get("title") == JOB_TITLE]

    job = json.dumps(build_job(github_token))
    if existing:
        job_id = existing[0]["jobId"]
        status, text = call("PATCH", f"{CRON_API}/jobs/{job_id}", cron_headers, job)
        action = "updated"
    else:
        status, text = call("PUT", f"{CRON_API}/jobs", cron_headers, job)
        action = "created"
    if status != 200:
        print(f"cron-job.org refused the job (HTTP {status}): {text[:200]}")
        return 1
    print(f"✓ Timer {action}: \"{JOB_TITLE}\" runs at minutes {MINUTES} of every hour.")
    print(f"  Watch runs arrive at https://github.com/{REPO}/actions/workflows/check-shows.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
