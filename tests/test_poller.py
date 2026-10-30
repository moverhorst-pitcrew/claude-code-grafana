import os
import tempfile
import unittest

from tests.helpers import load

poll = load("poll_usage", "usage-poller/poll_usage.py")


class Backoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        poll.STATE = os.path.join(self.tmp.name, "state.json")
        poll.now = 1_000_000.0

    def tearDown(self):
        self.tmp.cleanup()

    def test_waits_two_four_eight_then_ten_minutes(self):
        self.assertEqual([poll.set_backoff(0) for _ in range(5)], [120, 240, 480, 600, 600])

    def test_honours_a_longer_retry_after(self):
        self.assertEqual(poll.set_backoff(500), 500)

    def test_retry_after_is_capped(self):
        self.assertEqual(poll.set_backoff(7200), 600)

    def test_active_until_the_wait_has_passed(self):
        poll.set_backoff(0)
        self.assertTrue(poll.backoff_active())
        poll.now += 121
        self.assertFalse(poll.backoff_active())

    def test_success_resets_the_failure_count(self):
        poll.set_backoff(0)
        poll.set_backoff(0)
        poll.set_next_poll()
        poll.now += poll.MIN_INTERVAL
        self.assertEqual(poll.set_backoff(0), 120)

    def test_successful_poll_waits_the_minimum_interval(self):
        poll.set_next_poll()
        self.assertTrue(poll.backoff_active())
        poll.now += poll.MIN_INTERVAL
        self.assertFalse(poll.backoff_active())

    def test_old_plain_number_state_file_is_ignored(self):
        with open(poll.STATE, "w") as f:
            f.write("1793350000.5")
        self.assertFalse(poll.backoff_active())

    def test_garbage_state_file_is_ignored(self):
        with open(poll.STATE, "w") as f:
            f.write("not json")
        self.assertFalse(poll.backoff_active())


class Collect(unittest.TestCase):
    USAGE = {
        "limits": [
            {"kind": "five_hour", "percent": 17, "resets_at": "2026-10-30T12:00:00+00:00", "is_active": False},
            {"kind": "seven_day", "percent": 74, "resets_at": "2026-11-01T12:00:00+00:00", "is_active": True},
            {"kind": "weekly_scoped", "percent": 0, "scope": {"model": {"display_name": "Fable"}}, "is_active": False},
        ],
        "spend": {"enabled": True, "used": {"amount_minor": 13021, "exponent": 2, "currency": "EUR"}},
        "some_allowance": {"limit_dollars": 250, "used_dollars": 251.5, "utilization": 100},
    }

    def run_collect(self, usage):
        out = poll.Out()
        poll.collect(out, usage, {"subscriptionType": "max", "rateLimitTier": "x"})
        return out.m

    def test_maps_limit_kinds_to_names(self):
        m = self.run_collect(self.USAGE)
        limits = {a["limit"]: v for a, v in m[("claude_usage_utilization", "1")]}
        self.assertEqual(limits, {"session": 0.17, "weekly_all": 0.74, "weekly_scoped": 0.0})

    def test_scope_comes_from_the_model_display_name(self):
        m = self.run_collect(self.USAGE)
        scopes = {a["limit"]: a["scope"] for a, _ in m[("claude_usage_utilization", "1")]}
        self.assertEqual(scopes["weekly_scoped"], "Fable")

    def test_credits_use_the_currency_exponent(self):
        m = self.run_collect(self.USAGE)
        self.assertEqual(m[("claude_usage_credits_used", "")], [({"currency": "EUR"}, 130.21)])

    def test_unnamed_allowances_are_exported_by_raw_name(self):
        m = self.run_collect(self.USAGE)
        self.assertEqual(m[("claude_usage_allowance_limit", "")], [({"allowance": "some_allowance"}, 250.0)])

    def test_marks_the_poll_ok(self):
        m = self.run_collect(self.USAGE)
        self.assertEqual(m[("claude_usage_poll_ok", "")], [({}, 1.0)])

    def test_failure_reports_the_reason(self):
        out = poll.Out()
        poll.failure(out, "http-429")
        self.assertEqual(out.m[("claude_usage_poll_ok", "")], [({}, 0.0)])
        self.assertEqual(out.m[("claude_usage_poll_failure_info", "")], [({"reason": "http-429"}, 1.0)])

    def test_payload_is_otlp_shaped(self):
        out = poll.Out()
        out.g("claude_usage_poll_ok", "", 1)
        res = out.payload()["resourceMetrics"][0]
        self.assertEqual(res["resource"]["attributes"][0]["value"]["stringValue"], "claude-usage")
        self.assertEqual(res["scopeMetrics"][0]["metrics"][0]["name"], "claude_usage_poll_ok")


if __name__ == "__main__":
    unittest.main()
