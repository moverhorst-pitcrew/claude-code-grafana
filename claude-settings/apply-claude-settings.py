#!/usr/bin/env python3
"""Merge claude-telemetry-env.json into the `env` block of Claude Code's user settings.

Backs the file up first (settings.json.bak-pre-otel, only if no backup exists yet) and leaves every
other setting untouched. Honors CLAUDE_CONFIG_DIR. Use --remove to take the keys out again.

Also sets OTEL_RESOURCE_ATTRIBUTES=host.name=<name> so the dashboard can tell machines apart (the Host variable and
the "by host" panels). <name> is CLAUDE_HOST_NAME if set, else this machine's hostname. Other attributes already in
OTEL_RESOURCE_ATTRIBUTES are kept.
"""
import json, os, shutil, socket, sys

here = os.path.dirname(os.path.abspath(__file__))
cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
path = os.path.join(cfg, "settings.json")
keys = json.load(open(os.path.join(here, "claude-telemetry-env.json")))

settings = json.load(open(path)) if os.path.exists(path) else {}
if os.path.exists(path) and not os.path.exists(path + ".bak-pre-otel"):
    shutil.copy2(path, path + ".bak-pre-otel")
env = settings.setdefault("env", {})
ATTR = "OTEL_RESOURCE_ATTRIBUTES"
others = [a for a in env.get(ATTR, "").split(",") if a and not a.startswith("host.name=")]
if "--remove" in sys.argv:
    for k in keys:
        env.pop(k, None)
    if others:
        env[ATTR] = ",".join(others)
    else:
        env.pop(ATTR, None)
else:
    env.update(keys)
    env[ATTR] = ",".join(others + ["host.name=" + (os.environ.get("CLAUDE_HOST_NAME") or socket.gethostname())])
os.makedirs(cfg, exist_ok=True)
with open(path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
print(("removed from " if "--remove" in sys.argv else "updated ") + path)
