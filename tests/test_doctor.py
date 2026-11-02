import io
import json
import os
import tempfile
import time
import types
import unittest
from contextlib import redirect_stdout

from tests.helpers import ROOT, load

doctor = load("doctor", "tools/doctor.py")

with open(os.path.join(ROOT, "claude-settings", "claude-telemetry-env.json")) as f:
    TELEMETRY_ENV = json.load(f)

DATASOURCES = [{"type": "prometheus", "uid": "prom-1"}, {"type": "loki", "uid": "loki-1"}, {"type": "tempo", "uid": "tempo-1"}]


def vector(*values):
    return {"data": {"result": [{"metric": {}, "value": [0, str(v)]} for v in values]}}


class Stack:
    """The Grafana answers doctor.py queries, with knobs for what the stack is missing."""

    def __init__(self, datasources=DATASOURCES, series=12, events=34, poll_age=60):
        self.datasources, self.series, self.events, self.poll_age = datasources, series, events, poll_age

    def api(self, path, params=None):
        if path == "/api/datasources":
            return self.datasources
        if path.endswith("/loki/api/v1/query"):
            return vector(self.events) if self.events else vector()
        if "last_success" in params["query"]:
            return vector() if self.poll_age is None else vector(self.poll_age)
        return vector(self.series) if self.series else vector()


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings(dict(TELEMETRY_ENV))
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp.name
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.open_ports(True)
        self.stack(Stack())

    def settings(self, env):
        with open(os.path.join(self.tmp.name, "settings.json"), "w") as f:
            json.dump({"env": env}, f)

    def open_ports(self, is_open):
        def create_connection(address, timeout=None):
            if not is_open:
                raise OSError("connection refused")
            return types.SimpleNamespace(close=lambda: None)

        self.addCleanup(setattr, doctor, "socket", doctor.socket)
        doctor.socket = types.SimpleNamespace(create_connection=create_connection)

    def stack(self, stack):
        self.addCleanup(setattr, doctor, "api", doctor.api)
        doctor.api = stack.api

    def run_doctor(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = doctor.main()
        return code, out.getvalue()

    def line(self, output, name):
        return next(ln for ln in output.splitlines() if ln.lstrip().startswith(("ok", "warn", "FAIL")) and name in ln)

    # Check 1: Claude Code settings

    def test_all_telemetry_keys_present_is_ok(self):
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertIn("ok", self.line(out, "Claude Code settings"))

    def test_a_wrong_telemetry_value_warns_with_found_and_expected(self):
        self.settings({**TELEMETRY_ENV, "OTEL_METRICS_EXPORTER": "console"})
        code, out = self.run_doctor()
        self.assertEqual(code, 0)  # an override can be deliberate, so this is not a failure
        line = self.line(out, "Claude Code settings")
        self.assertTrue(line.startswith("warn"), line)
        self.assertIn("OTEL_METRICS_EXPORTER=console", line)
        self.assertIn("expected otlp", line)

    def test_a_missing_telemetry_key_is_named(self):
        self.settings({k: v for k, v in TELEMETRY_ENV.items() if k != "OTEL_LOGS_EXPORTER"})
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, "Claude Code settings")
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertIn("OTEL_LOGS_EXPORTER", line)
        self.assertIn("apply-claude-settings.py", out)

    def test_missing_settings_file_fails(self):
        os.remove(os.path.join(self.tmp.name, "settings.json"))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertTrue(self.line(out, "Claude Code settings").startswith("FAIL"))

    # Check 2: OTLP ports

    def test_a_closed_port_fails_and_points_at_the_stack(self):
        self.open_ports(False)
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, "OTLP ports")
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertIn("4317", line)
        self.assertIn("4318", line)
        self.assertIn("docker compose up -d", out)

    # Checks 3 and 4: Prometheus and Loki

    def test_no_metrics_fails_with_the_temporality_hint(self):
        self.stack(Stack(series=0))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertTrue(self.line(out, "Prometheus").startswith("FAIL"))
        self.assertIn("OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative", out)

    def test_no_events_fails_with_the_logs_exporter_hint(self):
        self.stack(Stack(events=0))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertTrue(self.line(out, "Loki").startswith("FAIL"))
        self.assertIn("OTEL_LOGS_EXPORTER=otlp", out)

    def test_loki_check_uses_the_same_selector_as_the_dashboard_panels(self):
        with open(os.path.join(ROOT, "dashboards", "claude-code.json"), encoding="utf-8") as f:
            dashboard = json.load(f)
        queries = [t.get("expr") or t.get("query", "") for p in dashboard["panels"] for t in p.get("targets", [])
                   if t["datasource"]["type"] == "loki"]
        self.assertTrue(queries, "no loki target in the dashboard")
        self.assertTrue(all(doctor.LOKI_SELECTOR in q for q in queries), "a log panel selects on something else than doctor.py does")

    def test_queries_use_the_uids_grafana_reports(self):
        seen = []
        stack = Stack()

        def record(path, params=None):
            seen.append(path)
            return stack.api(path, params)

        self.stack(types.SimpleNamespace(api=record))
        self.run_doctor()
        self.assertTrue(any("/uid/prom-1/" in p for p in seen), seen)
        self.assertTrue(any("/uid/loki-1/" in p for p in seen), seen)

    # Check 5: poller freshness

    def test_a_fresh_poll_is_ok(self):
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertTrue(self.line(out, "Usage poller").startswith("ok"))

    def test_a_stale_poll_fails_with_the_faq_wording(self):
        self.stack(Stack(poll_age=doctor.POLLER_MAX_AGE + 60))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, "Usage poller")
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertIn("16m00s", line)
        self.assertIn("http-429", out)
        self.assertIn("token-expired", out)

    def test_an_absent_poller_warns_but_does_not_fail_the_run(self):
        self.stack(Stack(poll_age=None))
        code, out = self.run_doctor()
        self.assertEqual(code, 0)
        line = self.line(out, "Usage poller")
        self.assertTrue(line.startswith("warn"), line)
        self.assertIn("usage-poller", out)

    # Check 6: datasources

    def test_a_missing_datasource_type_fails_and_is_named(self):
        self.stack(Stack(datasources=[d for d in DATASOURCES if d["type"] != "tempo"]))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, "Grafana datasources")
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertIn("tempo", line)

    def test_an_unauthorized_grafana_is_reported_as_could_not_check(self):
        import urllib.error

        for status in (401, 403):
            with self.subTest(status=status):
                def unauthorized(path, params=None, status=status):
                    raise urllib.error.HTTPError(doctor.G + path, status, "Unauthorized", {}, None)

                self.stack(types.SimpleNamespace(api=unauthorized))
                code, out = self.run_doctor()
                self.assertEqual(code, 0, out)
                for name in ("Prometheus", "Loki", "Usage poller", "Grafana datasources"):
                    self.assertTrue(self.line(out, name).startswith("warn"), self.line(out, name))
                self.assertIn("GRAFANA_PASSWORD", out)

    def test_an_unreachable_grafana_points_at_the_stack(self):
        import urllib.error

        def refused(path, params=None):
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

        self.stack(types.SimpleNamespace(api=refused))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        line = self.line(out, "Grafana datasources")
        self.assertIn("could not reach Grafana", line)
        self.assertIn("docker compose up -d", out)

    # The report as a whole

    def test_prints_one_line_per_check_in_order_and_a_fix_for_every_non_ok_result(self):
        self.stack(Stack(series=0, poll_age=None))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        markers = [ln for ln in out.splitlines() if ln[:4].strip() in ("ok", "warn", "FAIL")]
        self.assertEqual([ln.split(":")[0].split(None, 1)[1] for ln in markers],
                         ["Claude Code settings", "OTLP ports", "Prometheus has recent Claude Code metrics",
                          "Loki has Claude Code events", "Usage poller freshness", "Grafana datasources"])
        lines = out.splitlines()
        for i, ln in enumerate(lines):
            if ln[:4].strip() in ("warn", "FAIL"):
                self.assertTrue(lines[i + 1].lstrip().startswith("fix:"), f"no fix line after {ln!r}")

    def test_every_check_runs_even_when_one_blows_up(self):
        self.stack(types.SimpleNamespace(api=lambda path, params=None: 1 / 0))
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertEqual(len([ln for ln in out.splitlines() if ln[:4].strip() in ("ok", "warn", "FAIL")]), 6)
        self.assertTrue(self.line(out, "Claude Code settings").startswith("ok"))

    def test_never_prints_the_grafana_password(self):
        os.environ["GRAFANA_PASSWORD"] = "sekrit-" + str(int(time.time()))
        self.addCleanup(os.environ.pop, "GRAFANA_PASSWORD", None)
        reloaded = load("doctor_with_password", "tools/doctor.py")
        self.addCleanup(setattr, reloaded, "api", reloaded.api)
        reloaded.api = Stack().api
        out = io.StringIO()
        with redirect_stdout(out):
            reloaded.main()
        self.assertNotIn("sekrit", out.getvalue())


if __name__ == "__main__":
    unittest.main()
