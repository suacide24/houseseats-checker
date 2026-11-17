# A dependable timer for the checker

GitHub's own schedule is unreliable for this repo: it has run as few as 4
checks a day. An outside timer service can start the check on time instead.
GitHub's schedule stays on as a backup; the checker skips a run when another
one happened in the last 20 minutes, so the two never double up.

Setup takes about 5 minutes and you only do it once. These steps use
[cron-job.org](https://cron-job.org) (free); any service that can send a POST
request with custom headers works the same way.

## 1. Create a GitHub token that can do only this one thing

1. Open <https://github.com/settings/personal-access-tokens/new>
2. **Token name:** `houseseats timer`
3. **Expiration:** pick the longest you are comfortable with and put a reminder
   in your calendar. When it expires the timer stops working (the GitHub
   schedule keeps going) until you paste in a new token.
4. **Repository access:** *Only select repositories* → `houseseats-checker`
5. **Permissions → Repository permissions → Actions:** *Read and write*
   (leave everything else alone)
6. Generate and copy the token (starts with `github_pat_`).

This token can start and cancel workflow runs in this one repo. It cannot read
or change code, secrets, or anything in your other repos.

## 2. Create the timer

**Quick way.** Sign up at <https://cron-job.org>, copy your API key from
Settings there, then run this and paste the two keys when asked (input is
hidden and nothing is saved on your computer):

```bash
python3 tools/setup_timer.py
```

It tests the GitHub token, then creates the job. Run it again later to swap in
a new token. If you would rather click through the website, do this instead:

1. Sign up at <https://cron-job.org> and choose **Create cronjob**.
2. **Title:** `houseseats check`
3. **URL:**
   ```
   https://api.github.com/repos/suacide24/houseseats-checker/actions/workflows/check-shows.yml/dispatches
   ```
4. **Schedule:** every 30 minutes. If you can pick minutes, use 5 and 35.
5. Under **Advanced**:
   - **Request method:** POST
   - **Headers:**
     | Key | Value |
     |-----|-------|
     | `Authorization` | `Bearer` followed by a space and your token |
     | `Accept` | `application/vnd.github+json` |
     | `Content-Type` | `application/json` |
   - **Request body:**
     ```json
     {"ref":"main","inputs":{"throttle":"true"}}
     ```
6. Save, then use **Test run**. A status of **204** means it worked.

## 3. Check it

Open the [Actions page](https://github.com/suacide24/houseseats-checker/actions/workflows/check-shows.yml).
Within a minute of the test there should be a new run marked "Manually run".
From then on you should see one about every 30 minutes.

## If it stops

- **401 or 403** in the timer's history: the token expired or was removed. Make
  a new one (step 1) and replace the `Authorization` value.
- **404:** the URL is mistyped, or the token was not given access to this repo.
- **422:** the request body is mistyped.
- To turn it off, disable the job at cron-job.org and delete the token at
  <https://github.com/settings/personal-access-tokens>.
