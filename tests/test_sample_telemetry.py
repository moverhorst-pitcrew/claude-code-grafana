import json
import os
import re
import unittest

from tests.helpers import ROOT, load

sample = load("send_sample_telemetry", "tools/send_sample_telemetry.py")


def dashboard_queries():
    with open(os.path.join(ROOT, "dashboards", "claude-code.json"), encoding="utf-8") as f:
        d = json.load(f)
    return [t.get("expr") or t.get("query", "") for p in d["panels"] for t in p.get("targets", [])]


def sent_metric_names(payload):
    return {m["name"] for rm in payload["resourceMetrics"] for sm in rm["scopeMetrics"] for m in sm["metrics"]}


def sent_event_names(payload):
    names = set()
    for rl in payload["resourceLogs"]:
        for sl in rl["scopeLogs"]:
            for rec in sl["logRecords"]:
                for a in rec["attributes"]:
                    if a["key"] == "event_name":
                        names.add(a["value"]["stringValue"])
    return names


class SampleTelemetryCoversTheDashboard(unittest.TestCase):
    def setUp(self):
        self.sent = {}
        self.addCleanup(setattr, sample, "post", sample.post)
        sample.post = lambda path, body: self.sent.setdefault(path, body)
        sample.main()

    def test_covers_every_metric_name_the_dashboard_queries(self):
        used = set()
        for q in dashboard_queries():
            used |= set(re.findall(r'\bclaude_(?:code|usage)_\w+', q))
        self.assertTrue(used, "no claude_code_*/claude_usage_* metric found in the dashboard, check the regex")
        self.assertEqual(used - sent_metric_names(self.sent["/v1/metrics"]), set())

    def test_covers_every_event_name_the_dashboard_queries(self):
        used = set(re.findall(r'event_name\s*=\s*"([^"]+)"', " ".join(dashboard_queries())))
        self.assertTrue(used, "no event_name=\"...\" found in the dashboard, check the regex")
        self.assertEqual(used - sent_event_names(self.sent["/v1/logs"]), set())

    def test_payloads_are_json_serialisable(self):
        for body in self.sent.values():
            json.dumps(body)

    def test_every_cumulative_counter_carries_two_samples_so_rate_works(self):
        for rm in self.sent["/v1/metrics"]["resourceMetrics"]:
            for sm in rm["scopeMetrics"]:
                for m in sm["metrics"]:
                    if "sum" not in m:
                        continue
                    stamps = {int(p["timeUnixNano"]) for p in m["sum"]["dataPoints"]}
                    self.assertEqual(len(stamps), 2, f"{m['name']} needs two samples for rate() to return anything")
                    gap = (max(stamps) - min(stamps)) / 1e9
                    self.assertLess(gap, 60, f"{m['name']}: both samples must fit in one $__rate_interval, and the "
                                             "smallest one tools/validate_dashboard.py generates is 60 s (left-open)")

    def test_sends_one_trace_per_dashboard_span_name(self):
        used = set(re.findall(r'name\s*=\s*"(claude_code\.\w+)"', " ".join(dashboard_queries())))
        sent = {s["name"] for rs in self.sent["/v1/traces"]["resourceSpans"] for ss in rs["scopeSpans"] for s in ss["spans"]}
        self.assertEqual(used - sent, set())


if __name__ == "__main__":
    unittest.main()
