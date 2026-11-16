"""Tests for the dead-man's switch decision logic (stdlib only)."""

import importlib
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_health as ch  # noqa: E402

NOW = datetime(2026, 11, 16, 15, 0, 0)


def ev(last, alerted=False, failures=0, threshold=24, max_failures=3):
    return ch.evaluate_health(last, NOW, threshold, alerted, failures, max_failures)


class EvaluateHealthTests(unittest.TestCase):
    def test_healthy(self):
        r = ev("2026-11-16T14:00:00 PT")
        self.assertEqual((r["down"], r["action"], r["age_hours"]), (False, "none", 1.0))

    def test_normal_github_scheduling_gap_is_not_an_outage(self):
        # 16h since the last run: routine for GitHub cron, must not alert
        self.assertEqual(ev("2026-11-15T23:00:00 PT")["action"], "none")

    def test_stale_alerts_once(self):
        r = ev("2026-11-15T10:00:00 PT")  # 29h
        self.assertEqual((r["down"], r["reason"], r["action"]), (True, "stale", "alert_down"))
        self.assertEqual(ev("2026-11-15T10:00:00 PT", alerted=True)["action"], "none")

    def test_repeated_scrape_failures_alert_even_when_recently_successful(self):
        r = ev("2026-11-16T12:00:00 PT", failures=3)
        self.assertEqual((r["down"], r["reason"], r["action"]), (True, "failing", "alert_down"))
        self.assertEqual(ev("2026-11-16T12:00:00 PT", failures=2)["action"], "none")

    def test_recovery(self):
        self.assertEqual(ev("2026-11-16T14:00:00 PT", alerted=True)["action"], "alert_recovered")
        # still failing -> no recovery yet
        self.assertEqual(ev("2026-11-16T14:00:00 PT", alerted=True, failures=5)["action"], "none")

    def test_missing_or_garbage_timestamp_is_down(self):
        self.assertEqual(ev(None)["action"], "alert_down")
        self.assertEqual(ev("not a date")["action"], "alert_down")

    def test_boundary_is_not_stale(self):
        self.assertEqual(ev("2026-11-15T15:00:00 PT")["action"], "none")  # exactly 24h


class EnvParsingTests(unittest.TestCase):
    def test_empty_github_variables_fall_back_to_defaults(self):
        # GitHub passes unset repo Variables as empty strings
        env = {"HEALTH_STALE_HOURS": "", "HEALTH_MAX_FAILURES": "", "HEALTH_ALERTS_ENABLED": ""}
        with mock.patch.dict(os.environ, env):
            mod = importlib.reload(ch)
            self.assertEqual(mod.STALE_THRESHOLD_HOURS, 24.0)
            self.assertEqual(mod.MAX_CONSECUTIVE_FAILURES, 3)
            self.assertTrue(mod.HEALTH_ALERTS_ENABLED)
        with mock.patch.dict(os.environ, {"HEALTH_STALE_HOURS": "36", "HEALTH_ALERTS_ENABLED": "false"}):
            mod = importlib.reload(ch)
            self.assertEqual(mod.STALE_THRESHOLD_HOURS, 36.0)
            self.assertFalse(mod.HEALTH_ALERTS_ENABLED)
        importlib.reload(ch)

    def test_env_float(self):
        with mock.patch.dict(os.environ, {"X": "garbage"}):
            self.assertEqual(ch._env_float("X", 6.0), 6.0)
        with mock.patch.dict(os.environ, {"X": " 3.5 "}):
            self.assertEqual(ch._env_float("X", 6.0), 3.5)


if __name__ == "__main__":
    unittest.main()
