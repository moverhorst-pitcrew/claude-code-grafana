import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

from tests.helpers import ROOT, load

validate = load("validate_dashboard", "tools/validate_dashboard.py")


def dashboard_refids():
    """{panel title: {refId, ...}} for the real dashboard."""
    with open(os.path.join(ROOT, "dashboards", "claude-code.json"), encoding="utf-8") as f:
        d = json.load(f)
    return {p["title"]: {t["refId"] for t in p.get("targets", [])} for p in d["panels"]}


def panel(title, *queries):
    return {"title": title,
            "targets": [{"refId": chr(ord("A") + i), "datasource": {"type": "prometheus"}, "expr": q}
                        for i, q in enumerate(queries)]}


def fake_get(_path, params):
    """A query mentioning "rows" returns one series, "boom" errors, anything else returns no rows."""
    if "boom" in params["query"]:
        return {"status": "error", "error": "parse error"}
    return {"status": "success", "data": {"result": [{}] if "rows" in params["query"] else []}}


def run(panels, *flags):
    """Run main() over a throwaway dashboard without touching Grafana. Returns (exit code, stdout)."""
    out, code = io.StringIO(), None
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "dashboard.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"panels": panels}, f)
        argv, get = sys.argv, validate.get
        sys.argv, validate.get = ["validate_dashboard.py", path, *flags], fake_get
        try:
            with contextlib.redirect_stdout(out):
                validate.main()
        except SystemExit as e:
            code = e.code
        finally:
            sys.argv, validate.get = argv, get
    return code, out.getvalue()


class RequireData(unittest.TestCase):
    def test_empty_target_fails(self):
        code, out = run([panel("Cost by model", "sum(nothing)")], "--require-data")
        self.assertEqual(code, 1)
        self.assertIn("EMPTY: 1", out)
        self.assertIn("Cost by model", out)

    def test_empty_target_passes_without_the_flag(self):
        code, out = run([panel("Cost by model", "sum(nothing)")])
        self.assertEqual(code, 0)
        self.assertNotIn("EMPTY", out)

    def test_everything_with_rows_passes(self):
        code, out = run([panel("Cost by model", "sum(rows)"), panel("Requests", "sum(rows)")], "--require-data")
        self.assertEqual(code, 0)
        self.assertIn("EMPTY: 0", out)

    def test_allowlisted_panel_may_be_empty(self):
        code, _ = run([panel("Span errors per hour", "sum(nothing)")], "--require-data")
        self.assertEqual(code, 0)

    def test_one_empty_target_of_a_multi_target_panel_fails(self):
        code, out = run([panel("Tokens per minute by type", "sum(rows)", "sum(nothing)")], "--require-data")
        self.assertEqual(code, 1)
        self.assertIn("Tokens per minute by type [B]", out)

    def test_a_datasource_error_still_fails_without_the_flag(self):
        code, out = run([panel("Cost by model", "sum(boom)")])
        self.assertEqual(code, 1)
        self.assertIn("FAILED: 1", out)


class AllowlistMatchesTheDashboard(unittest.TestCase):
    def test_every_entry_names_a_real_panel_and_target(self):
        refids = dashboard_refids()
        for entry in validate.ALLOW_EMPTY:
            title, ref = (entry, None) if isinstance(entry, str) else entry
            self.assertIn(title, refids, f"allowlisted panel {title!r} is not in the dashboard")
            if ref is not None:
                self.assertIn(ref, refids[title], f"allowlisted target {title!r} {ref} is not in the dashboard")


if __name__ == "__main__":
    unittest.main()
