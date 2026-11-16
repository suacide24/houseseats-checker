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
        )
        self.sent = []
        self.email_ok = True
        self.fetch_result = [show(), show(name="Ikons of Rock", date="November 17th", link="https://x/2")]
        self.login_ok = True

        def fake_send(msg):
            if not self.email_ok:
                raise OSError("smtp down")
            self.sent.append(msg["Subject"])

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
        self.assertTrue(self.sent[0].startswith("⭐ Watchlist: Ikons of Rock"))
        flags = {s["name"]: s["watchlist"] for s in self.read("HOUSESEATS_OUTPUT_FILE")["shows"]}
        self.assertEqual(flags, {"Finding Wonder": False, "Ikons of Rock": True})


if __name__ == "__main__":
    unittest.main()
