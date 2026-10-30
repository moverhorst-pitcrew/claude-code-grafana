import json
import os
import subprocess
import sys
import unittest

from tests.helpers import ROOT, load

gen = load("gen_dashboard", "tools/gen_dashboard.py")


def all_exprs(dashboard):
    return [(p["title"], t["expr"]) for p in dashboard["panels"] for t in p.get("targets", []) if "expr" in t]


class HostScoping(unittest.TestCase):
    def test_metric_without_selector(self):
        self.assertEqual(gen.scope_to_host("sum(claude_code_cost_usage_USD_total)", False),
                         'sum(claude_code_cost_usage_USD_total{host_name=~"$host"})')

    def test_metric_with_selector(self):
        self.assertEqual(gen.scope_to_host('claude_code_cost_usage_USD_total{model=~"$model"}', False),
                         'claude_code_cost_usage_USD_total{host_name=~"$host",model=~"$model"}')

    def test_every_occurrence_in_an_expression(self):
        out = gen.scope_to_host("claude_code_session_count_total unless claude_code_session_count_total offset 5m", False)
        self.assertEqual(out.count('host_name=~"$host"'), 2)

    def test_usage_metrics_are_account_wide_and_left_alone(self):
        self.assertEqual(gen.scope_to_host("claude_usage_utilization_ratio", False), "claude_usage_utilization_ratio")

    def test_loki_selector(self):
        self.assertEqual(gen.scope_to_host('{service_name="claude-code"} | event_name="x"', True),
                         '{service_name="claude-code"} | host_name=~"$host" | event_name="x"')


class GeneratedDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = gen.dashboard

    def test_has_model_and_host_variables(self):
        names = [v["name"] for v in self.d["templating"]["list"]]
        self.assertEqual(names[:2], ["model", "host"])
        self.assertEqual(names[2:], ["ds_prometheus", "ds_loki", "ds_tempo"])

    def test_every_claude_code_query_is_scoped_to_the_host(self):
        for title, expr in all_exprs(self.d):
            if "claude_code_" in expr or 'service_name="claude-code"' in expr:
                self.assertIn("host_name=~", expr, title)

    def test_panel_ids_are_unique(self):
        ids = [p["id"] for p in self.d["panels"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_committed_dashboard_matches_the_generator(self):
        out = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "gen_dashboard.py")],
                             check=True, capture_output=True, text=True).stdout
        with open(os.path.join(ROOT, "dashboards", "claude-code.json"), encoding="utf-8") as f:
            committed = f.read()
        self.assertEqual(json.loads(out), json.loads(committed),
                         "dashboards/claude-code.json is stale: run  python3 tools/gen_dashboard.py > dashboards/claude-code.json")


if __name__ == "__main__":
    unittest.main()
