import json
import os
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT

SCRIPT = os.path.join(ROOT, "claude-settings", "apply-claude-settings.py")


class ApplySettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args, host="testhost"):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.tmp.name, CLAUDE_HOST_NAME=host)
        subprocess.run([sys.executable, "-I", SCRIPT, *args], env=env, check=True, capture_output=True)
        with open(self.path) as f:
            return json.load(f)

    def write(self, data):
        with open(self.path, "w") as f:
            json.dump(data, f)

    def test_adds_telemetry_variables_and_host_name(self):
        env = self.run_script()["env"]
        self.assertEqual(env["CLAUDE_CODE_ENABLE_TELEMETRY"], "1")
        self.assertEqual(env["OTEL_RESOURCE_ATTRIBUTES"], "host.name=testhost")

    def test_keeps_other_settings_and_makes_a_backup(self):
        self.write({"theme": "dark", "env": {"FOO": "bar"}})
        settings = self.run_script()
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(settings["env"]["FOO"], "bar")
        self.assertTrue(os.path.exists(self.path + ".bak-pre-otel"))

    def test_keeps_other_resource_attributes_and_replaces_the_host(self):
        self.write({"env": {"OTEL_RESOURCE_ATTRIBUTES": "team=core,host.name=old"}})
        self.assertEqual(self.run_script()["env"]["OTEL_RESOURCE_ATTRIBUTES"], "team=core,host.name=testhost")

    def test_is_idempotent(self):
        first = self.run_script()
        self.assertEqual(self.run_script(), first)

    def test_remove_takes_everything_out_again(self):
        self.write({"env": {"FOO": "bar"}})
        self.run_script()
        self.assertEqual(self.run_script("--remove")["env"], {"FOO": "bar"})

    def test_remove_keeps_unrelated_resource_attributes(self):
        self.write({"env": {"OTEL_RESOURCE_ATTRIBUTES": "team=core"}})
        self.run_script()
        self.assertEqual(self.run_script("--remove")["env"], {"OTEL_RESOURCE_ATTRIBUTES": "team=core"})


if __name__ == "__main__":
    unittest.main()
