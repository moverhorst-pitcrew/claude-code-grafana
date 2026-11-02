import contextlib
import io
import sys
import unittest

from tests.helpers import load

wait = load("wait_for_telemetry", "tools/wait_for_telemetry.py")


def stub(traces, spanmetrics_samples, counter_samples):
    """Stands in for wait.get(): the Tempo search and the two Prometheus sample counts."""
    def get(path, **params):
        if "tempo" in path:
            return {"traces": [{}] * traces}
        n = spanmetrics_samples if "spanmetrics" in params["query"] else counter_samples
        return {"data": {"result": [{"value": [0, str(n)]}] if n else []}}
    return get


class Pending(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, wait, "get", wait.get)

    def test_nothing_pending_once_everything_is_there(self):
        wait.get = stub(1, 2, 2)
        self.assertEqual(wait.pending(), [])

    def test_names_what_is_still_missing(self):
        wait.get = stub(0, 1, 0)
        self.assertEqual(wait.pending(), ["no searchable trace in Tempo",
                                          "1 of 2 traces_spanmetrics_calls_total samples",
                                          "0 of 2 claude_code_cost_usage_USD_total samples"])

    def test_the_timeout_message_says_what_was_missing(self):
        wait.get = stub(1, 0, 2)
        self.addCleanup(setattr, sys, "argv", sys.argv)
        sys.argv = ["wait_for_telemetry.py", "0"]  # time out on the first round, so the test does not sleep
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as e:
            wait.main()
        self.assertIn("traces_spanmetrics_calls_total", str(e.exception.code))

    def test_a_loaded_stack_returns_without_waiting(self):
        wait.get = stub(1, 2, 2)
        self.addCleanup(setattr, sys, "argv", sys.argv)
        sys.argv = ["wait_for_telemetry.py", "0"]
        with contextlib.redirect_stdout(io.StringIO()):
            wait.main()


if __name__ == "__main__":
    unittest.main()
