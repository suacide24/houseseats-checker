"""Unit + integration tests for houseseats_checker (no network, no email).

Run:  python -m unittest discover -s tests -v
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import houseseats_checker as hc  # noqa: E402

NOW = datetime(2026, 11, 16, 10, 0, 0)

PANEL = """
<div class="panel panel-default">
  <div class="panel-heading"><a href="{href}">{name}</a></div>
  <div class="panel-body">
    <img class="img-responsive" src="{img}">
    <div class="grid-cal-date">{date}</div>
  </div>
</div>
"""


def page(*shows):
    return "<html><body>" + "".join(PANEL.format(**s) for s in shows) + "</body></html>"


SHOW_A = dict(href="./tickets/view/?showid=1", name="Finding Wonder", img="/resources/media/1.jpg", date="November 16th")
SHOW_B = dict(href="./tickets/view/?showid=2", name="Ikons of Rock", img="/resources/media/2.jpg", date="November 17th")


def show(name="Finding Wonder", date="November 16th", link="https://x/1", **extra):
    d = {"source": "HouseSeats", "name": name, "date": date, "link": link}
    d.update(extra)
    return d


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error

    def get(self, url, **kwargs):
        if self.error:
            raise self.error
        return self.response

    def close(self):
        pass


class DateResolutionTests(unittest.TestCase):
    def test_bare_date_uses_nearest_year(self):
        self.assertEqual(hc.resolve_show_date("July 18th", datetime(2026, 7, 18)), "2026-07-18")
        self.assertEqual(hc.resolve_show_date("January 5th", datetime(2026, 12, 20)), "2027-01-05")
        self.assertEqual(hc.resolve_show_date("December 20th", datetime(2026, 1, 5)), "2025-12-20")

    def test_explicit_year_and_abbreviations(self):
        self.assertEqual(hc.resolve_show_date("Fri, 13 Feb '26 10:30 PM", datetime(2030, 1, 1)), "2026-02-13")
        self.assertEqual(hc.resolve_show_date("Feb 13 2026", datetime(2030, 1, 1)), "2026-02-13")
        self.assertEqual(hc.resolve_show_date("Jul 4", datetime(2026, 3, 1)), "2026-07-04")

    def test_unparseable(self):
        self.assertIsNone(hc.resolve_show_date("TBD", NOW))
        self.assertIsNone(hc.resolve_show_date("", NOW))

    def test_show_key_differs_year_over_year(self):
        s = show(name="ROUGE", date="July 18th")
        with mock.patch.object(hc, "get_pacific_time", return_value=datetime(2026, 7, 18)):
            k26 = hc.get_show_key(s)
        with mock.patch.object(hc, "get_pacific_time", return_value=datetime(2027, 7, 18)):
            k27 = hc.get_show_key(s)
        self.assertEqual(k26, "HouseSeats|ROUGE|2026-07-18")
        self.assertEqual(k27, "HouseSeats|ROUGE|2027-07-18")

    def test_format_show_date_has_weekday(self):
        self.assertEqual(hc.format_show_date({"date": "November 16th", "date_iso": "2026-11-16"}), "Mon, Nov 16")
        self.assertEqual(
            hc.format_show_date({"date": "Fri, 13 Feb '26 10:30 PM", "date_iso": "2026-02-13"}),
            "Fri, Feb 13 10:30 PM",
        )
        self.assertEqual(hc.format_show_date({"date": "TBD"}), "TBD")


class NotifiedStateTests(unittest.TestCase):
    def test_migration_is_idempotent_and_keeps_matching(self):
        legacy = {"HouseSeats|ROUGE|July 18th", "weird|bad|TBD"}
        migrated, changed = hc.migrate_notified_keys(legacy, datetime(2026, 7, 18))
        self.assertTrue(changed)
        self.assertIn("HouseSeats|ROUGE|2026-07-18", migrated)
        self.assertIn("weird|bad|TBD", migrated)
        self.assertFalse(hc.migrate_notified_keys(migrated, datetime(2026, 7, 18))[1])

    def test_cleanup_prunes_only_ancient(self):
        keys = {"HouseSeats|A|2024-01-01", "HouseSeats|B|2026-07-01", "weird|bad|TBD"}
        kept, changed = hc.cleanup_old_notified(keys, datetime(2026, 7, 18))
        self.assertTrue(changed)
        self.assertEqual(kept, {"HouseSeats|B|2026-07-01", "weird|bad|TBD"})

    def test_missing_file_is_empty_but_corrupt_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notified_shows.json"
            with mock.patch.object(hc, "NOTIFIED_FILE", path):
                self.assertEqual(hc.load_notified_shows(), set())
                for bad in ("", '{"notified": [', '{"other": 1}', '{"notified": "x"}', "[]"):
                    path.write_text(bad)
                    with self.assertRaises(hc.StateError, msg=bad):
                        hc.load_notified_shows()
                path.write_text('{"notified": ["a", "b"]}')
                self.assertEqual(hc.load_notified_shows(), {"a", "b"})

    def test_atomic_write_roundtrip_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            hc.write_json_atomic(path, {"a": 1})
            hc.write_json_atomic(path, {"a": 2})
            self.assertEqual(json.loads(path.read_text()), {"a": 2})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["x.json"])


class ParsingTests(unittest.TestCase):
    def test_parse_panels(self):
        shows = hc.parse_houseseats_shows(page(SHOW_A, SHOW_B))
        self.assertEqual(len(shows), 2)
        self.assertEqual(
            shows[0],
            {
                "source": "HouseSeats",
                "name": "Finding Wonder",
                "link": "https://lv.houseseats.com/member/tickets/view/?showid=1",
                "date": "November 16th",
                "image": "https://lv.houseseats.com/resources/media/1.jpg",
            },
        )

    def test_parse_ignores_panels_without_a_name(self):
        html_text = '<div class="panel-default"><div class="grid-cal-date">Nov 1</div></div>'
        self.assertEqual(hc.parse_houseseats_shows(html_text), [])

    def test_fetch_returns_none_when_nothing_parsed_or_request_fails(self):
        login_page = '<form><input type="password" name="password"></form>'
        self.assertIsNone(hc.fetch_houseseats_shows(FakeSession(FakeResponse(login_page))))
        self.assertIsNone(hc.fetch_houseseats_shows(FakeSession(FakeResponse("", 503))))
        self.assertIsNone(hc.fetch_houseseats_shows(FakeSession(error=requests.ConnectionError("down"))))
        self.assertEqual(len(hc.fetch_houseseats_shows(FakeSession(FakeResponse(page(SHOW_A))))), 1)

    def test_dedupe_keeps_same_day_listings_with_different_links(self):
        a1, a2, a3 = show(link="https://x/1"), show(link="https://x/1"), show(link="https://x/2")
        self.assertEqual(hc.dedupe_shows([a1, a2, a3]), [a1, a3])

    def test_list_parsing(self):
        self.assertEqual(hc.parse_list_lines("# c\n\n Cirque \nBlue Man\n"), {"cirque", "blue man"})
        self.assertTrue(hc.matches_any("Cirque du Soleil: O", {"cirque"}))
        self.assertFalse(hc.matches_any("ROUGE", {"cirque"}))
        self.assertFalse(hc.matches_any("ROUGE", set()))


class ScrapeRetryTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(hc.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_then_succeeds(self):
        fetch = mock.Mock(side_effect=[None, [show()]])
        shows, error = hc.scrape_with_retries("T", lambda s: True, fetch)
        self.assertEqual(len(shows), 1)
        self.assertIsNone(error)
        self.assertEqual(fetch.call_count, 2)

    def test_gives_up_with_error(self):
        login = mock.Mock(return_value=False)
        shows, error = hc.scrape_with_retries("T", login, mock.Mock())
        self.assertIsNone(shows)
        self.assertEqual(error, "login failed")
        self.assertEqual(login.call_count, hc.SCRAPE_ATTEMPTS)

    def test_rejected_login_is_not_retried(self):
        login = mock.Mock(side_effect=hc.LoginRejected("bad password"))
        shows, error = hc.scrape_with_retries("T", login, mock.Mock())
        self.assertIsNone(shows)
        self.assertIn("rejected", error)
        self.assertEqual(login.call_count, 1)

    def test_unexpected_exception_is_contained(self):
        shows, error = hc.scrape_with_retries("T", lambda s: True, mock.Mock(side_effect=KeyError("x")))
        self.assertIsNone(shows)
        self.assertIn("KeyError", error)

    def test_missing_credentials_reject_without_network(self):
        with mock.patch.object(hc, "HOUSESEATS_PASSWORD", ""):
            with self.assertRaises(hc.LoginRejected):
                hc.login_houseseats(FakeSession(error=AssertionError("no network expected")))


class OutputTests(unittest.TestCase):
    def test_success_resets_failure_state(self):
        prev = {"shows": [show(name="Old")], "consecutive_failures": 4, "last_successful_run": "old"}
        out = hc.build_source_output("HouseSeats", [show()], prev, True, None, NOW)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["consecutive_failures"], 0)
        self.assertIsNone(out["last_error"])
        self.assertEqual(out["last_successful_run"], "2026-11-16T10:00:00 PT")
        self.assertEqual([s["name"] for s in out["shows"]], ["Finding Wonder"])

    def test_failure_keeps_previous_shows_and_counts(self):
        prev = {"shows": [show(name="Old")], "consecutive_failures": 1, "last_successful_run": "2026-11-15T09:00:00 PT"}
        out = hc.build_source_output("HouseSeats", [], prev, False, "login failed", NOW)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["consecutive_failures"], 2)
        self.assertEqual(out["last_error"], "login failed")
        self.assertEqual(out["last_successful_run"], "2026-11-15T09:00:00 PT")
        self.assertEqual(out["last_updated"], "2026-11-16T10:00:00 PT")
        self.assertEqual([s["name"] for s in out["shows"]], ["Old"])
        self.assertEqual(out["count"], 1)

    def test_failure_with_no_history(self):
        out = hc.build_source_output("HouseSeats", [], {}, False, None, NOW)
        self.assertEqual((out["consecutive_failures"], out["shows"], out["last_successful_run"]), (1, [], None))

    def test_already_notified_show_without_history_is_not_new(self):
        old, fresh = show(), show(name="Brand New", date="November 20th")
        shows = hc.enrich_shows([old, fresh], [], set(), NOW, {hc.get_show_key(old)})
        self.assertIsNone(shows[0]["first_seen"])
        self.assertEqual(shows[1]["first_seen"], "2026-11-16T10:00:00 PT")

    def test_enrich_adds_fields_and_carries_first_seen(self):
        with mock.patch.object(hc, "get_pacific_time", return_value=NOW):
            previous = [show(first_seen="2026-11-10T08:00:00 PT")]
            shows = hc.enrich_shows(
                [show(), show(name="Penn & Teller", date="November 20th")], previous, {"penn"}, NOW
            )
        self.assertEqual(shows[0]["date_iso"], "2026-11-16")
        self.assertEqual(shows[0]["weekday"], "Mon")
        self.assertEqual(shows[0]["first_seen"], "2026-11-10T08:00:00 PT")
        self.assertFalse(shows[0]["watchlist"])
        self.assertEqual(shows[1]["first_seen"], "2026-11-16T10:00:00 PT")
        self.assertTrue(shows[1]["watchlist"])


class EmailTests(unittest.TestCase):
    def grouped(self, *shows):
        with mock.patch.object(hc, "get_pacific_time", return_value=NOW):
            return hc.group_shows_by_name(hc.enrich_shows(list(shows), [], {"penn"}, NOW))

    def test_subject_describes_whats_new(self):
        g = self.grouped(show(), show(name="Ikons of Rock", date="November 17th"), show(name="ROUGE", date="November 18th"))
        self.assertEqual(hc.build_email_subject(g), "🎭 New: Finding Wonder (Mon, Nov 16), Ikons of Rock +1 more")

    def test_watchlist_and_rare_lead_the_subject(self):
        g = self.grouped(show(), show(name="Penn & Teller", date="November 20th"))
        self.assertTrue(hc.build_email_subject(g).startswith("⭐ Watchlist: Penn & Teller (Fri, Nov 20)"))
        g = self.grouped(show(), show(name="Zed", date="November 20th", rare=True))
        self.assertTrue(hc.build_email_subject(g).startswith("🔥 Rare: Zed"))

    def test_subject_cannot_be_split_by_newlines(self):
        g = self.grouped(show(name="Evil\r\nBcc: someone"))
        self.assertNotIn("\n", hc.build_email_subject(g))
        self.assertNotIn("\r", hc.build_email_subject(g))

    def test_slots_sorted_chronologically(self):
        g = self.grouped(show(date="November 20th", link="b"), show(date="November 16th", link="a"))
        self.assertEqual([s["link"] for s in g[0]["time_slots"]], ["a", "b"])

    def test_html_is_escaped(self):
        g = self.grouped(show(name='<script>alert(1)</script>', link='https://x/?a=1"><b>'))
        text, html_body = hc.build_email_bodies(g, 1)
        self.assertNotIn("<script>", html_body)
        self.assertIn("&lt;script&gt;", html_body)
        self.assertNotIn('"><b>', html_body)
        self.assertIn("<script>alert(1)</script>", text)  # plain text is untouched

    def test_send_status(self):
        shows = [show()]
        with mock.patch.object(hc, "EMAIL_ENABLED", False):
            self.assertEqual(hc.send_email_notification(shows), "disabled")
        with mock.patch.object(hc, "EMAIL_ENABLED", True), mock.patch.object(hc, "SMTP_PASSWORD", ""):
            self.assertEqual(hc.send_email_notification(shows), "failed")
        creds = mock.patch.multiple(
            hc, EMAIL_ENABLED=True, SMTP_EMAIL="from@example.com", SMTP_PASSWORD="pw", NOTIFICATION_EMAIL="to@example.com"
        )
        with creds, mock.patch.object(hc, "smtp_send") as send:
            self.assertEqual(hc.send_email_notification(shows), "sent")
            self.assertEqual(send.call_count, 1)
        with creds, mock.patch.object(hc, "smtp_send", side_effect=OSError("smtp down")):
            self.assertEqual(hc.send_email_notification(shows), "failed")

    def test_smtp_send_retries_transient_errors(self):
        attempts = []

        class FlakySMTP:
            def __init__(self, *a, **k):
                attempts.append(1)
                if len(attempts) < 3:
                    raise OSError("connection reset")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self): pass
            def login(self, *a): pass
            def sendmail(self, *a): pass

        with mock.patch.object(hc.smtplib, "SMTP", FlakySMTP), mock.patch.object(hc.time, "sleep"):
            hc.smtp_send(mock.Mock(as_string=lambda: "x"))
        self.assertEqual(len(attempts), 3)

    def test_desktop_notification_never_uses_a_shell(self):
        nasty = [show(name="It's a \"trap\"; $(rm -rf ~) `id`")]
        with mock.patch.object(hc.sys, "platform", "darwin"), mock.patch.object(hc.subprocess, "run") as run:
            hc.notify_user(nasty)
        argv = run.call_args[0][0]
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertNotIn("shell", run.call_args[1])
        self.assertIn('\\"trap\\"', argv[2])
        with mock.patch.object(hc.sys, "platform", "linux"), mock.patch.object(hc.subprocess, "run") as run:
            hc.notify_user(nasty)
        run.assert_not_called()


class MainFlowTests(unittest.TestCase):
    """End-to-end runs of main() against a temp directory with the network,
    the denylist fetch and SMTP replaced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.files = dict(
            NOTIFIED_FILE=d / "notified_shows.json",
            HISTORY_FILE=d / "show_history.json",
            HOUSESEATS_OUTPUT_FILE=d / "houseseats_shows.json",
            FIRSTTIX_OUTPUT_FILE=d / "firsttix_shows.json",
            LOG_FILE=d / "houseseats.log",
            WATCHLIST_FILE=d / "watchlist.txt",
            DENYLIST_FILE=d / "denylist.txt",
            SUBSCRIBERS_FILE=d / "subscribers.json",
            HIDDEN_DIR=d / "denylists",
        )
        self.sent = []
        self.email_ok = True
        self.fetch_result = [show(), show(name="Ikons of Rock", date="November 17th", link="https://x/2")]
        self.login_ok = True

        self.sent_to = []
        self.friend_lists = {}  # url -> set of keywords, or None for "fetch failed"

        def fake_send(msg, recipient=None):
            if not self.email_ok or recipient in getattr(self, "bounce", ()):
                raise OSError("smtp down")
            if recipient in (None, "to@example.com"):
                self.sent.append(msg["Subject"])
            self.sent_to.append((recipient, msg["Subject"], msg.as_string()))

        patches = [
            mock.patch.multiple(hc, **self.files),
            mock.patch.multiple(
                hc, EMAIL_ENABLED=True, SMTP_EMAIL="from@example.com", SMTP_PASSWORD="pw",
                NOTIFICATION_EMAIL="to@example.com",
            ),
            mock.patch.object(hc, "get_pacific_time", return_value=NOW),
            mock.patch.object(hc, "load_denylist", return_value={"denied show"}),
            mock.patch.object(hc, "login_houseseats", side_effect=lambda s: self.login_ok),
            mock.patch.object(hc, "fetch_houseseats_shows", side_effect=lambda s: self.fetch_result),
            mock.patch.object(hc, "smtp_send", side_effect=fake_send),
            mock.patch.object(hc, "notify_user"),
            mock.patch.object(hc, "fetch_keyword_list", side_effect=lambda url: self.friend_lists.get(url)),
            mock.patch.dict(hc.os.environ, {"SUBSCRIBERS": ""}),
            mock.patch.object(hc, "TELEGRAM_BOT_TOKEN", ""),
            mock.patch.object(hc.time, "sleep"),
            mock.patch("builtins.print"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, *extra):
        return hc.main(["--fast", "--no-firsttix", *extra])

    def read(self, key):
        return json.loads(self.files[key].read_text())

    def test_new_shows_are_emailed_once(self):
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Finding Wonder", self.sent[0])
        self.assertEqual(len(self.read("NOTIFIED_FILE")["notified"]), 2)
        out = self.read("HOUSESEATS_OUTPUT_FILE")
        self.assertEqual((out["status"], out["count"], out["consecutive_failures"]), ("ok", 2, 0))
        self.assertEqual(out["shows"][0]["date_iso"], "2026-11-16")
        self.assertIn("first_seen", out["shows"][0])

        self.assertEqual(self.run_main(), 0)  # second run: nothing new
        self.assertEqual(len(self.sent), 1)

    def test_denylisted_shows_are_dropped(self):
        self.fetch_result = [show(), show(name="A Denied Show Tonight", link="https://x/9")]
        self.run_main()
        self.assertEqual([s["name"] for s in self.read("HOUSESEATS_OUTPUT_FILE")["shows"]], ["Finding Wonder"])

    def test_failed_email_is_retried_next_run(self):
        self.email_ok = False
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.sent, [])
        self.assertFalse(self.files["NOTIFIED_FILE"].exists())  # nothing marked notified

        self.email_ok = True
        self.run_main()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.read("NOTIFIED_FILE")["notified"]), 2)

    def test_muted_email_marks_shows_so_unmuting_does_not_flood(self):
        with mock.patch.object(hc, "EMAIL_ENABLED", False):
            self.run_main()
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.read("NOTIFIED_FILE")["notified"]), 2)
        self.run_main()
        self.assertEqual(self.sent, [])

    def test_failed_scrape_keeps_published_shows_and_sends_nothing(self):
        self.run_main()
        self.sent.clear()

        self.fetch_result = None
        self.assertEqual(self.run_main(), 0)
        out = self.read("HOUSESEATS_OUTPUT_FILE")
        self.assertEqual((out["status"], out["consecutive_failures"], out["count"]), ("error", 1, 2))
        self.assertTrue(out["last_error"])
        self.assertEqual(self.sent, [])

        self.login_ok = False
        self.run_main()
        self.assertEqual(self.read("HOUSESEATS_OUTPUT_FILE")["consecutive_failures"], 2)

        self.login_ok, self.fetch_result = True, [show()]
        self.run_main()
        out = self.read("HOUSESEATS_OUTPUT_FILE")
        self.assertEqual((out["status"], out["consecutive_failures"], out["count"]), ("ok", 0, 1))
        self.assertEqual(self.sent, [])  # already-notified show, no duplicate email

    def test_corrupt_notified_file_stops_the_run(self):
        self.files["NOTIFIED_FILE"].write_text('{"notified": [')
        self.assertEqual(self.run_main(), 2)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.files["NOTIFIED_FILE"].read_text(), '{"notified": [')

    def test_dry_run_writes_and_sends_nothing(self):
        self.assertEqual(self.run_main("--dry-run"), 0)
        self.assertEqual(self.sent, [])
        for key in ("NOTIFIED_FILE", "HISTORY_FILE", "HOUSESEATS_OUTPUT_FILE"):
            self.assertFalse(self.files[key].exists(), key)

    def test_min_interval_skips_recent_check(self):
        self.run_main()
        before = self.files["HOUSESEATS_OUTPUT_FILE"].read_text()
        with mock.patch.object(hc, "get_pacific_time", return_value=NOW.replace(minute=10)):
            with mock.patch.object(hc, "fetch_houseseats_shows", side_effect=AssertionError("should skip")):
                self.assertEqual(self.run_main("--min-interval", "20"), 0)
        self.assertEqual(self.files["HOUSESEATS_OUTPUT_FILE"].read_text(), before)
        with mock.patch.object(hc, "get_pacific_time", return_value=NOW.replace(minute=30)):
            self.run_main("--min-interval", "20")
        self.assertEqual(self.read("HOUSESEATS_OUTPUT_FILE")["last_updated"], "2026-11-16T10:30:00 PT")

    def test_watchlist_match_is_flagged(self):
        self.files["WATCHLIST_FILE"].write_text("# favourites\nikons\n")
        self.run_main()
        self.assertTrue(self.sent[0].startswith("[HouseSeats] ⭐ Watchlist: Ikons of Rock"))
        flags = {s["name"]: s["watchlist"] for s in self.read("HOUSESEATS_OUTPUT_FILE")["shows"]}
        self.assertEqual(flags, {"Finding Wonder": False, "Ikons of Rock": True})


GIST = "https://gist.github.com/alexuser/abc123"
GIST_RAW = "https://gist.githubusercontent.com/alexuser/abc123/raw"


class SubscriberConfigTests(unittest.TestCase):
    def test_empty_or_missing_is_no_subscribers(self):
        self.assertEqual(hc.parse_subscribers(""), ([], []))
        self.assertEqual(hc.parse_subscribers("  "), ([], []))
        self.assertEqual(hc.parse_subscribers("[]"), ([], []))

    def test_valid_entry_is_normalised(self):
        subs, problems = hc.parse_subscribers(json.dumps([
            {"id": "Alex", "email": "alex@example.com", "denylist_url": GIST + "/edit",
             "denylist": ["Comedy", "magic"], "watchlist": "cirque, Penn"},
        ]))
        self.assertEqual(problems, [])
        sub = subs[0]
        self.assertEqual((sub["id"], sub["enabled"]), ("alex", True))
        self.assertEqual(sub["denylist_url"], GIST_RAW)
        self.assertEqual(sub["denylist_edit_url"], GIST + "/edit")
        self.assertEqual(sub["denylist"], {"comedy", "magic"})
        self.assertEqual(sub["watchlist"], {"cirque", "penn"})

    def test_bad_entries_are_skipped_without_leaking_addresses(self):
        subs, problems = hc.parse_subscribers(json.dumps([
            {"id": "ok", "email": "ok@example.com"},
            {"id": "bad id!", "email": "secret1@example.com"},
            {"id": "noemail", "email": "not-an-address"},
            {"id": "ok", "email": "secret2@example.com"},
            {"id": "owner", "email": "secret3@example.com"},
            {"id": "http", "email": "secret4@example.com", "denylist_url": "http://insecure/x"},
            "nonsense",
        ]))
        self.assertEqual([s["id"] for s in subs], ["ok"])
        self.assertEqual(len(problems), 6)
        self.assertNotIn("@", " ".join(problems))

    def test_invalid_json_reports_a_problem(self):
        subs, problems = hc.parse_subscribers("[{oops")
        self.assertEqual(subs, [])
        self.assertEqual(len(problems), 1)

    def test_gist_urls(self):
        self.assertEqual(hc.gist_urls(GIST), (GIST_RAW, GIST + "/edit"))
        self.assertEqual(hc.gist_urls(GIST_RAW + "/list.txt"), (GIST_RAW + "/list.txt", GIST + "/edit"))
        self.assertEqual(hc.gist_urls("https://example.com/list.txt"), ("https://example.com/list.txt", None))


class SubscriberFlowTests(MainFlowTests):
    """main() with a friend configured. Inherits the owner tests too, so
    everything the owner relies on is re-checked with a subscriber present."""

    def setUp(self):
        super().setUp()
        self.set_subscribers([{"id": "alex", "email": "alex@example.com", "denylist_url": GIST,
                               "watchlist": ["ikons"]}])
        self.friend_lists[GIST_RAW] = {"finding"}
        # a show the owner denylists but the friend does not
        self.fetch_result = self.fetch_result + [show(name="Denied Show Live", date="November 18th", link="https://x/3")]

    def set_subscribers(self, subs):
        p = mock.patch.dict(hc.os.environ, {"SUBSCRIBERS": json.dumps(subs)})
        p.start()
        self.addCleanup(p.stop)

    def friend_mail(self, address="alex@example.com"):
        return [m for m in self.sent_to if m[0] == address]

    def state(self, sub_id="alex"):
        return self.read("SUBSCRIBERS_FILE")["subscribers"][sub_id]

    def test_friend_gets_own_filtered_list_once(self):
        self.assertEqual(self.run_main(), 0)
        mail = self.friend_mail()
        self.assertEqual(len(mail), 1)
        self.assertIn("subscribed", mail[0][1])
        st = self.state()
        self.assertEqual(sorted(s["name"] for s in st["shows"]), ["Denied Show Live", "Ikons of Rock"])
        self.assertEqual(len(st["notified"]), 2)
        self.assertTrue([s for s in st["shows"] if s["name"] == "Ikons of Rock"][0]["watchlist"])
        # owner's published list is untouched by the friend's settings
        self.assertEqual(sorted(s["name"] for s in self.read("HOUSESEATS_OUTPUT_FILE")["shows"]),
                         ["Finding Wonder", "Ikons of Rock"])
        self.assertFalse(any(s.get("watchlist") for s in self.read("HOUSESEATS_OUTPUT_FILE")["shows"]))
        self.run_main()
        self.assertEqual(len(self.friend_mail()), 1)

    def test_public_state_file_never_contains_an_address(self):
        self.run_main()
        text = self.files["SUBSCRIBERS_FILE"].read_text()
        self.assertNotIn("@", text)
        self.assertNotIn("alexuser", text)
        self.assertNotIn("alex@example.com", self.files["LOG_FILE"].read_text())

    def test_every_email_subject_has_the_same_prefix(self):
        import check_health
        self.run_main()
        self.assertEqual(check_health.SUBJECT_PREFIX, hc.SUBJECT_PREFIX)
        self.assertGreaterEqual(len(self.sent_to), 2)  # owner alert + friend welcome
        for _, subject, _ in self.sent_to:
            self.assertTrue(subject.startswith("[HouseSeats] "), subject)

    def test_friend_email_has_their_links_not_the_owners(self):
        self.run_main()
        import email as email_lib
        msg = email_lib.message_from_string(self.friend_mail()[0][2])
        body = "".join(part.get_payload(decode=True).decode() for part in msg.walk() if not part.is_multipart())
        self.assertEqual(msg["To"], "alex@example.com")
        self.assertIn("?u=alex", body)
        self.assertIn(GIST + "/edit", body)
        self.assertNotIn(hc.DENYLIST_GIST_EDIT_URL, body)
        self.assertNotIn("to@example.com", body)

    def test_later_new_show_sends_a_normal_alert(self):
        self.run_main()
        self.fetch_result = self.fetch_result + [show(name="Brand New", date="November 19th", link="https://x/4")]
        self.run_main()
        mail = self.friend_mail()
        self.assertEqual(len(mail), 2)
        self.assertIn("Brand New", mail[1][1])

    def test_failed_friend_email_is_retried_and_does_not_affect_owner(self):
        self.bounce = {"alex@example.com"}
        self.run_main()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state()["notified"], [])
        self.bounce = set()
        self.run_main()
        self.assertEqual(len(self.friend_mail()), 1)
        self.assertEqual(len(self.sent), 1)

    def test_denylist_outage_uses_saved_copy_or_skips(self):
        self.friend_lists[GIST_RAW] = None  # unreachable on the very first run
        self.run_main()
        self.assertEqual(self.friend_mail(), [])
        self.assertEqual(self.state()["last_error"], "could not load denylist")
        self.friend_lists[GIST_RAW] = {"finding"}
        self.run_main()
        self.assertEqual(len(self.friend_mail()), 1)
        self.friend_lists[GIST_RAW] = None  # outage later: saved copy keeps filtering
        self.fetch_result = self.fetch_result + [show(name="Finding Nemo", date="November 19th", link="https://x/5")]
        self.run_main()
        self.assertEqual(len(self.friend_mail()), 1)
        self.assertNotIn("Finding Nemo", [s["name"] for s in self.state()["shows"]])

    def test_owner_mute_does_not_mute_friends(self):
        with mock.patch.object(hc, "EMAIL_ENABLED", False):
            self.run_main()
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.friend_mail()), 1)

    def test_paused_and_removed_subscribers(self):
        self.run_main()
        self.set_subscribers([{"id": "alex", "email": "alex@example.com", "denylist_url": GIST, "enabled": False}])
        self.fetch_result = self.fetch_result + [show(name="Brand New", date="November 19th", link="https://x/4")]
        self.run_main()
        self.assertEqual(len(self.friend_mail()), 1)
        self.assertEqual(len(self.state()["notified"]), 2)
        self.set_subscribers([])
        self.run_main()
        self.assertEqual(self.read("SUBSCRIBERS_FILE")["subscribers"], {})

    def test_two_friends_are_independent(self):
        self.set_subscribers([
            {"id": "alex", "email": "alex@example.com", "denylist_url": GIST},
            {"id": "sam", "email": "sam@example.com", "denylist": ["ikons", "denied"]},
        ])
        self.run_main()
        self.assertEqual(len(self.friend_mail()), 1)
        self.assertEqual(len(self.friend_mail("sam@example.com")), 1)
        self.assertEqual([s["name"] for s in self.state("sam")["shows"]], ["Finding Wonder"])
        self.assertNotIn("sam@example.com", self.friend_mail()[0][2])

    def test_failed_scrape_leaves_friend_state_alone(self):
        self.run_main()
        before = self.state()
        self.fetch_result = None
        self.run_main()
        self.assertEqual(self.state(), before)
        self.assertEqual(len(self.friend_mail()), 1)

    def test_corrupt_subscriber_state_skips_friends_but_not_owner(self):
        self.files["SUBSCRIBERS_FILE"].write_text("{broken")
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(self.friend_mail(), [])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.files["SUBSCRIBERS_FILE"].read_text(), "{broken")

    def test_hidden_lists_apply_per_person(self):
        hidden = self.files["HIDDEN_DIR"]
        hidden.mkdir()
        (hidden / "owner.txt").write_text("Ikons of Rock\n")
        (hidden / "alex.txt").write_text("Denied Show Live\n")
        self.run_main()
        self.assertEqual([s["name"] for s in self.read("HOUSESEATS_OUTPUT_FILE")["shows"]], ["Finding Wonder"])
        self.assertEqual([s["name"] for s in self.state()["shows"]], ["Ikons of Rock"])

    def test_hide_links_name_the_right_person(self):
        self.run_main()
        import email as email_lib
        from urllib.parse import unquote_plus

        def body(raw):
            msg = email_lib.message_from_string(raw)
            return unquote_plus("".join(p.get_payload(decode=True).decode() for p in msg.walk() if not p.is_multipart()))

        self.assertIn("Hide for alex: Ikons of Rock", body(self.friend_mail()[0][2]))
        owner = body(self.friend_mail("to@example.com")[0][2])
        self.assertIn("Hide for owner: Ikons of Rock", owner)
        self.assertNotIn("Hide for alex", owner)

    def test_dry_run_sends_and_writes_nothing_for_friends(self):
        self.run_main("--dry-run")
        self.assertEqual(self.sent_to, [])
        self.assertFalse(self.files["SUBSCRIBERS_FILE"].exists())


class HideIssueTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        import handle_hide_issue
        self.h = handle_hide_issue
        self.subs, _ = hc.parse_subscribers(json.dumps([
            {"id": "alex", "email": "alex@example.com", "denylist_url": GIST},
            {"id": "sam", "email": "sam@example.com", "denylist": ["x"]},
        ]))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(hc, "HIDDEN_DIR", Path(self.tmp.name) / "denylists")
        p.start()
        self.addCleanup(p.stop)

    def decide(self, title, author):
        return self.h.decide(title, author, "RepoOwner", self.subs)[:2]

    def test_owner_can_hide_for_anyone(self):
        self.assertEqual(self.decide("Hide for owner: Sinatra  Live!", "repoowner"), ("owner", "Sinatra Live!"))
        self.assertEqual(self.decide("hide for ALEX: A Show", "RepoOwner"), ("alex", "A Show"))
        self.assertEqual(self.decide("Hide for sam: A Show", "repoowner"), ("sam", "A Show"))

    def test_friend_can_hide_only_for_their_own_list(self):
        self.assertEqual(self.decide("Hide for alex: A Show", "AlexUser"), ("alex", "A Show"))
        self.assertEqual(self.decide("Hide for owner: A Show", "alexuser"), (None, None))
        self.assertEqual(self.decide("Hide for sam: A Show", "alexuser"), (None, None))

    def test_strangers_and_bad_input_are_refused(self):
        self.assertEqual(self.decide("Hide for alex: A Show", "stranger"), (None, None))
        self.assertEqual(self.decide("Hide for alex: A Show", ""), (None, None))
        self.assertEqual(self.decide("Hide for nobody: A Show", "repoowner"), (None, None))
        self.assertEqual(self.decide("Hide for owner: " + "x" * 200, "repoowner"), (None, None))
        self.assertEqual(self.decide("Hide for owner: # comment", "repoowner"), (None, None))
        self.assertEqual(self.decide("Hide for ../etc: A Show", "repoowner"), (None, None))
        self.assertEqual(self.decide("Something else", "repoowner"), (None, None))

    def test_add_hidden_appends_once(self):
        self.assertTrue(self.h.add_hidden("owner", "Sinatra Live!"))
        self.assertFalse(self.h.add_hidden("owner", "sinatra live!"))
        self.assertTrue(self.h.add_hidden("owner", "Other"))
        self.assertEqual((hc.HIDDEN_DIR / "owner.txt").read_text(), "Sinatra Live!\nOther\n")
        self.assertEqual(hc.load_hidden("owner"), {"sinatra live!", "other"})

    def test_main_writes_outputs(self):
        out = Path(self.tmp.name) / "out"
        env = {"ISSUE_TITLE": "Hide for owner: A Show", "ISSUE_AUTHOR": "me", "REPO_OWNER": "me",
               "SUBSCRIBERS": "", "GITHUB_OUTPUT": str(out)}
        with mock.patch.dict(hc.os.environ, env), mock.patch("builtins.print"):
            self.assertEqual(self.h.main(), 0)
        self.assertIn("changed=true", out.read_text())
        self.assertIn("A Show", out.read_text())


class FakeTelegram:
    """Stands in for urllib.request.urlopen and records what was posted."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, request, timeout=None):
        self.calls.append((request.full_url, json.loads(request.data.decode())))
        if self.fail:
            raise self.fail
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps({"ok": True, "result": {"message_id": 77}}).encode()

    @property
    def payloads(self):
        return [payload for _, payload in self.calls]


class TelegramMessageTests(unittest.TestCase):
    def grouped(self, shows):
        with mock.patch.object(hc, "get_pacific_time", return_value=NOW):
            return hc.group_shows_by_name(hc.enrich_shows(list(shows), [], {"penn"}, NOW))

    def test_first_line_says_what_is_new(self):
        text, button = hc.build_telegram_message(self.grouped([show(), show(name="Ikons of Rock", date="November 17th")]))
        first = text.splitlines()[0]
        self.assertIn("Finding Wonder", first)
        self.assertIn("Mon, Nov 16", first)
        self.assertEqual(button[0], "📋 View all shows")

    def test_single_show_button_opens_the_show(self):
        _, button = hc.build_telegram_message(self.grouped([show(link="https://lv.houseseats.com/x?showid=1")]))
        self.assertEqual(button, ("🎟️ Open show", "https://lv.houseseats.com/x?showid=1"))

    def test_dynamic_text_is_escaped_and_only_allowed_tags_used(self):
        import re
        text, _ = hc.build_telegram_message(self.grouped([show(name="Tom & <b>Jerry</b> <script>", link="https://x/1?a=1&b=2")]))
        self.assertIn("Tom &amp; &lt;b&gt;Jerry&lt;/b&gt; &lt;script&gt;", text)
        self.assertIn('href="https://x/1?a=1&amp;b=2"', text)
        self.assertEqual(set(re.findall(r"</?([a-z]+)", text)) - {"b", "a"}, set())

    def test_long_lists_stay_under_the_limit_with_whole_shows(self):
        many = [show(name=f"Show number {i} " + "x" * 80, date="November 17th", link=f"https://x/{i}") for i in range(80)]
        text, _ = hc.build_telegram_message(self.grouped(many))
        self.assertLess(len(text), 4000)
        self.assertIn("more on the site", text)
        self.assertEqual(text.count("<a "), text.count("</a>"))
        self.assertEqual(text.count("<b>"), text.count("</b>"))

    def test_send_targets_the_project_topic(self):
        tg = FakeTelegram()
        with mock.patch.object(hc, "TELEGRAM_BOT_TOKEN", "123:abc"), mock.patch.object(hc.urllib.request, "urlopen", tg):
            self.assertEqual(hc.send_telegram("hi", ("Open", "https://x")), 77)
        url, payload = tg.calls[0]
        self.assertTrue(url.endswith("/bot123:abc/sendMessage"))
        self.assertEqual(payload["chat_id"], -1003983050734)
        self.assertEqual(payload["message_thread_id"], 4)
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertEqual(payload["reply_markup"], {"inline_keyboard": [[{"text": "Open", "url": "https://x"}]]})

    def test_no_token_means_no_network_call(self):
        tg = FakeTelegram()
        with mock.patch.object(hc, "TELEGRAM_BOT_TOKEN", ""), mock.patch.object(hc.urllib.request, "urlopen", tg):
            self.assertIsNone(hc.send_telegram("hi"))
        self.assertEqual(tg.calls, [])

    def test_failures_never_log_the_token(self):
        import io
        import urllib.error
        errors = [
            OSError("https://api.telegram.org/botSECRETTOKEN/sendMessage unreachable"),
            urllib.error.HTTPError("https://api.telegram.org/botSECRETTOKEN/sendMessage", 400, "Bad Request", {},
                                   io.BytesIO(b'{"ok":false,"description":"Bad Request: chat not found"}')),
        ]
        for error in errors:
            logged = []
            with mock.patch.object(hc, "TELEGRAM_BOT_TOKEN", "SECRETTOKEN"), \
                    mock.patch.object(hc.urllib.request, "urlopen", FakeTelegram(fail=error)), \
                    mock.patch.object(hc, "log_message", side_effect=logged.append):
                self.assertIsNone(hc.send_telegram("hi"))
            self.assertTrue(logged)
            self.assertNotIn("SECRETTOKEN", " ".join(logged))
            self.assertNotIn("api.telegram.org", " ".join(logged))

    def test_empty_variables_fall_back_to_built_in_ids(self):
        with mock.patch.dict(hc.os.environ, {"T_ID": ""}):
            self.assertEqual(hc._env_int("T_ID", 4), 4)
        with mock.patch.dict(hc.os.environ, {"T_ID": " 9 "}):
            self.assertEqual(hc._env_int("T_ID", 4), 9)


class TelegramRoutingTests(MainFlowTests):
    """main() with a Telegram token set. Inherits the owner tests, so the
    notified/retry rules are re-checked with Telegram as the channel."""

    def setUp(self):
        super().setUp()
        self.tg = FakeTelegram()
        for p in (mock.patch.object(hc, "TELEGRAM_BOT_TOKEN", "123:abc"),
                  mock.patch.object(hc.urllib.request, "urlopen", self.tg)):
            p.start()
            self.addCleanup(p.stop)
        # the inherited tests look at self.sent for "an alert went out"
        real_send = hc.send_telegram

        def recording_send(text, button=None):
            if not self.email_ok:  # inherited tests use this to mean "delivery is down"
                return None
            message_id = real_send(text, button)
            if message_id is not None:
                self.sent.append("[HouseSeats] " + hc.html.unescape(text.splitlines()[0].replace("<b>", "").replace("</b>", "")))
            return message_id

        p = mock.patch.object(hc, "send_telegram", side_effect=recording_send)
        p.start()
        self.addCleanup(p.stop)

    def test_alert_goes_to_telegram_not_email(self):
        self.run_main()
        self.assertEqual(len(self.tg.calls), 1)
        self.assertEqual(self.tg.payloads[0]["message_thread_id"], 4)
        self.assertIn("Finding Wonder", self.tg.payloads[0]["text"].splitlines()[0])
        self.assertEqual(self.sent_to, [])  # no email at all
        self.assertEqual(len(self.read("NOTIFIED_FILE")["notified"]), 2)
        self.assertEqual(self.read("HOUSESEATS_OUTPUT_FILE")["alert_channel"], "telegram")

    def test_telegram_failure_falls_back_to_email(self):
        self.tg.fail = OSError("network down")
        self.run_main()
        self.assertEqual(len(self.tg.calls), 1)
        self.assertEqual(len(self.sent_to), 1)
        self.assertEqual(self.sent_to[0][0], "to@example.com")
        self.assertEqual(len(self.read("NOTIFIED_FILE")["notified"]), 2)
        self.run_main()
        self.assertEqual(len(self.sent_to), 1)  # not sent twice

    def test_both_channels_down_leaves_shows_pending(self):
        self.tg.fail = OSError("network down")
        self.bounce = {"to@example.com"}
        self.run_main()
        self.assertEqual(self.read("NOTIFIED_FILE")["notified"] if self.files["NOTIFIED_FILE"].exists() else [], [])
        self.tg.fail, self.bounce = None, set()
        self.run_main()
        self.assertEqual(len([c for c in self.tg.calls]), 2)
        self.assertEqual(len(self.read("NOTIFIED_FILE")["notified"]), 2)

    def test_mute_silences_telegram_too(self):
        with mock.patch.object(hc, "EMAIL_ENABLED", False):
            self.run_main()
        self.assertEqual(self.tg.calls, [])
        self.assertEqual(self.sent_to, [])

    def test_dry_run_posts_nothing(self):
        self.run_main("--dry-run")
        self.assertEqual(self.tg.calls, [])

    def test_friends_still_get_email(self):
        p = mock.patch.dict(hc.os.environ, {"SUBSCRIBERS": json.dumps([{"id": "sam", "email": "sam@example.com"}])})
        p.start()
        self.addCleanup(p.stop)
        self.run_main()
        self.assertEqual([m[0] for m in self.sent_to], ["sam@example.com"])
        self.assertEqual(len(self.tg.calls), 1)

    def test_telegram_test_flag(self):
        self.assertEqual(self.run_main("--telegram-test"), 0)
        self.assertEqual(self.tg.payloads[0]["message_thread_id"], 4)
        self.assertFalse(self.files["HOUSESEATS_OUTPUT_FILE"].exists())
        self.tg.fail = OSError("down")
        self.assertEqual(self.run_main("--telegram-test"), 1)


if __name__ == "__main__":
    unittest.main()
