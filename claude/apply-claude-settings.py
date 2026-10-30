#!/usr/bin/env python3
"""Merge claude-telemetry-env.json into the `env` block of Claude Code's user settings.

Backs the file up first (settings.json.bak-pre-otel, only if no backup exists yet) and leaves every
other setting untouched. Honors CLAUDE_CONFIG_DIR. Use --remove to take the keys out again.
"""
import json, os, shutil, sys

here = os.path.dirname(os.path.abspath(__file__))
cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
path = os.path.join(cfg, "settings.json")
keys = json.load(open(os.path.join(here, "claude-telemetry-env.json")))

settings = json.load(open(path)) if os.path.exists(path) else {}
if os.path.exists(path) and not os.path.exists(path + ".bak-pre-otel"):
    shutil.copy2(path, path + ".bak-pre-otel")
env = settings.setdefault("env", {})
if "--remove" in sys.argv:
    for k in keys:
        env.pop(k, None)
else:
    env.update(keys)
os.makedirs(cfg, exist_ok=True)
with open(path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
print(("removed from " if "--remove" in sys.argv else "updated ") + path)
