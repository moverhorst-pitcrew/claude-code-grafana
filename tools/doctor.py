#!/usr/bin/env python3
"""Diagnose an empty Claude Code dashboard: walks the whole chain from Claude Code's settings through the
collector's OTLP ports to Prometheus, Loki and Grafana, and names the likely fix for every step that is not ok.

All six checks always run and each one catches its own errors, so one invocation shows the whole picture
instead of stopping at the first problem. Exits 1 when a check FAILs. Uses GRAFANA_PASSWORD from the
environment when set; otherwise the Grafana queries run anonymously.

  tools/doctor.py
"""
import base64, json, os, socket, sys, time, urllib.error, urllib.parse, urllib.request

G = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
# Without GRAFANA_PASSWORD the queries run anonymously, which works when anonymous viewing is enabled.
auth = "Basic " + base64.b64encode(f"admin:{os.environ['GRAFANA_PASSWORD']}".encode()).decode() if "GRAFANA_PASSWORD" in os.environ else None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Fallback uids, used when /api/datasources cannot be read; otel-lgtm provisions these.
UIDS = {"prometheus": "prometheus", "loki": "loki", "tempo": "tempo"}
# The log panels select on this and nothing else, so the check and the dashboard agree on what "has events" means.
LOKI_SELECTOR = '{service_name="claude-code"}'
# The poller tiles read the last sample within 15 minutes, so anything older than that leaves them empty.
POLLER_MAX_AGE = 15 * 60

FIX_SETTINGS = "run claude-settings/apply-claude-settings.py, then start a *new* Claude Code session (settings are read at startup)"
FIX_SETTINGS_VALUES = ("deliberate overrides (another endpoint, a different export interval) are fine; otherwise run "
                       "claude-settings/apply-claude-settings.py, which overwrites them, and start a *new* Claude Code session")
FIX_STACK = "the stack is not running: ./install.sh, or docker compose up -d"
FIX_METRICS = ("start a new Claude Code session (settings are read at startup); if logs and traces do arrive but metrics do not, "
               "Claude Code is sending delta metrics and Prometheus rejects them: set OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative")
FIX_LOGS = ("start a new Claude Code session (settings are read at startup) and check that OTEL_LOGS_EXPORTER=otlp is in its env block")
FIX_POLLER_STALE = ("the poller is rate limited (http-429) or your login token ran out (token-expired); 429 recovers by itself after a few minutes, "
                    "token-expired is fixed by using Claude Code once, which refreshes the token")
FIX_POLLER_ABSENT = "only needed if you want the subscription limit tiles: ./install.sh --usage-poller, or docker compose --profile usage-poller up -d"
FIX_DATASOURCES = "recreate the stack so Grafana provisions its datasources again (docker compose down && docker compose up -d), and check its logs"
FIX_AUTH = "set GRAFANA_PASSWORD to the Grafana admin password, or enable anonymous viewing"


def brief(e):
    return (e if isinstance(e, str) else f"{type(e).__name__}: {e}").replace("\n", " ")[:160]


def api(path, params=None):
    """GET a Grafana API path (including the datasource proxy) and parse the JSON answer."""
    url = G + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": auth} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # The answer says why a query was rejected; the exception on its own only says "400 Bad Request".
        e.body = e.read().decode(errors="replace")[:300].replace("\n", " ")
        raise


def query_failed(e, fix):
    """A Grafana query that raised: a 401 means we could not check at all, and an unreachable Grafana is the
    stack being down rather than anything to do with the check itself."""
    if isinstance(e, urllib.error.HTTPError):
        # 403 as well as 401: listing datasources needs the admin role, which a Viewer login does not have.
        if e.code in (401, 403):
            return "warn", f"could not check: Grafana answered {e.code}", FIX_AUTH
        body = getattr(e, "body", "")
        return "FAIL", f"Grafana answered {e.code} {e.reason}" + (f": {body}" if body else ""), fix
    elif isinstance(e, urllib.error.URLError):
        return "FAIL", f"could not reach Grafana at {G}: {brief(e.reason)}", FIX_STACK
    return "FAIL", f"could not query Grafana: {brief(e)}", fix


def promql(uid, expr):
    r = api(f"/api/datasources/proxy/uid/{uid}/api/v1/query", {"query": expr, "time": int(time.time())})
    return r["data"]["result"]


def datasources():
    """Resolve the uid per datasource type, so the checks also work against a Grafana whose uids differ."""
    try:
        return {d["type"]: d["uid"] for d in api("/api/datasources")}, None
    except Exception as e:
        return None, e


def check_settings():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    path = os.path.join(cfg, "settings.json")
    with open(os.path.join(ROOT, "claude-settings", "claude-telemetry-env.json")) as f:
        want = json.load(f)
    if not os.path.exists(path):
        return "FAIL", f"{path} does not exist", FIX_SETTINGS
    with open(path) as f:
        env = json.load(f).get("env", {})
    missing = [k for k in want if k not in env]
    if missing:
        return "FAIL", f"{len(missing)} of {len(want)} telemetry variables missing from {path}: {', '.join(missing)}", FIX_SETTINGS
    # A value that differs is not necessarily wrong (a remote endpoint, a longer interval), but console exporters
    # or delta temporality do leave the dashboard empty, so name what was found next to what was expected.
    differing = [f"{k}={env[k]} (expected {v})" for k, v in want.items() if str(env[k]) != v]
    if differing:
        return "warn", f"{len(differing)} of {len(want)} telemetry variables differ in {path}: {', '.join(differing)}", FIX_SETTINGS_VALUES
    return "ok", f"all {len(want)} telemetry variables present with the expected values in {path}", None


def check_ports():
    closed = []
    for port in (4317, 4318):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
        except OSError:
            closed.append(port)
    if closed:
        return "FAIL", "nothing listening on " + ", ".join(f"127.0.0.1:{p}" for p in closed), FIX_STACK
    return "ok", "127.0.0.1:4317 and :4318 accept connections", None


def check_metrics(uid):
    # count() so a Prometheus with thousands of claude_code_* series answers with one number instead of all of them.
    try:
        r = promql(uid, 'count(count_over_time({__name__=~"claude_code_.*"}[1h]))')
        n = int(float(r[0]["value"][1])) if r else 0
    except Exception as e:
        return query_failed(e, FIX_METRICS)
    if not n:
        return "FAIL", "no claude_code_* series in the last hour", FIX_METRICS
    return "ok", f"{n} claude_code_* series in the last hour", None


def check_logs(uid):
    try:
        r = api(f"/api/datasources/proxy/uid/{uid}/loki/api/v1/query",
                {"query": f"count_over_time({LOKI_SELECTOR} [1h])", "time": int(time.time() * 10**9)})
        n = int(sum(float(s["value"][1]) for s in r["data"]["result"]))
    except Exception as e:
        return query_failed(e, FIX_LOGS)
    if not n:
        return "FAIL", f"no {LOKI_SELECTOR} events in the last hour", FIX_LOGS
    return "ok", f"{n} {LOKI_SELECTOR} events in the last hour", None


def check_poller(uid):
    # A wider lookback than the tiles use, so a poller that stopped hours ago reads as stale instead of absent.
    try:
        r = promql(uid, "time() - last_over_time(claude_usage_last_success_timestamp_seconds[24h])")
    except Exception as e:
        return query_failed(e, FIX_POLLER_STALE)
    if not r:
        # The poller is opt-in: a stack without it is healthy, so this is not a failure.
        return "warn", "no claude_usage_last_success_timestamp_seconds in the last 24 hours, the poller is not running", FIX_POLLER_ABSENT
    age = max(0, int(float(r[0]["value"][1])))  # clamped: a clock a second ahead of Prometheus would read negative
    if age > POLLER_MAX_AGE:
        return "FAIL", f"last poll succeeded {age // 60}m{age % 60:02d}s ago, older than the {POLLER_MAX_AGE // 60}m the tiles read", FIX_POLLER_STALE
    return "ok", f"last poll succeeded {age // 60}m{age % 60:02d}s ago", None


def check_datasources(found, err):
    if found is None:
        return query_failed(err, FIX_DATASOURCES)
    missing = [t for t in UIDS if t not in found]
    if missing:
        return "FAIL", f"no {' or '.join(missing)} datasource in Grafana (found: {', '.join(sorted(found)) or 'none'})", FIX_DATASOURCES
    return "ok", "prometheus, loki and tempo datasources exist (" + ", ".join(f"{t}={found[t]}" for t in UIDS) + ")", None


def run(name, fix, check):
    try:
        return (name,) + check()
    except Exception as e:
        return name, "FAIL", f"check failed: {brief(e)}", fix


def main():
    found, err = datasources()
    uids = {**UIDS, **(found or {})}
    checks = [
        ("Claude Code settings", FIX_SETTINGS, check_settings),
        ("OTLP ports", FIX_STACK, check_ports),
        ("Prometheus has recent Claude Code metrics", FIX_METRICS, lambda: check_metrics(uids["prometheus"])),
        ("Loki has Claude Code events", FIX_LOGS, lambda: check_logs(uids["loki"])),
        ("Usage poller freshness", FIX_POLLER_STALE, lambda: check_poller(uids["prometheus"])),
        ("Grafana datasources", FIX_DATASOURCES, lambda: check_datasources(found, err)),
    ]
    results = [run(name, fix, check) for name, fix, check in checks]
    for name, status, detail, fix in results:
        print(f"{status:<4} {name}: {detail}")
        if fix:
            print(f"       fix: {fix}")
    tally = {s: sum(1 for r in results if r[1] == s) for s in ("ok", "warn", "FAIL")}
    print("\n" + ", ".join(f"{n} {s}" for s, n in tally.items() if n) + f" out of {len(results)} checks")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
