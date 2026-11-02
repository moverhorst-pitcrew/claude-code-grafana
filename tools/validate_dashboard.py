#!/usr/bin/env python3
"""Run every query of dashboards/claude-code.json against Grafana's datasources and report results.
Uses GRAFANA_PASSWORD from the environment when set (never stored in files); otherwise queries run anonymously.
With --require-data a query that succeeds but returns no rows is a failure too, unless its panel is in ALLOW_EMPTY.
A query ending in `or vector(0)` always returns a row, so --require-data does not catch a rename inside those.

  tools/validate_dashboard.py [dashboard.json] [--range 15m|3h|24h|7d] [--require-data]   (default 3h; use several ranges to check scaling)
"""
import base64, json, os, sys, time, urllib.parse, urllib.request
G = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
# Without GRAFANA_PASSWORD the queries run anonymously, which works when anonymous viewing is enabled.
auth = "Basic " + base64.b64encode(f"admin:{os.environ['GRAFANA_PASSWORD']}".encode()).decode() if "GRAFANA_PASSWORD" in os.environ else None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIONS = ("--range", "--require-data")  # anything else is rejected: a typo in CI must not quietly skip a check
# Panels that are empty by construction on the sample telemetry of tools/send_sample_telemetry.py, so
# --require-data accepts no rows for them. A single target of a multi-target panel goes in as
# ("panel title", "refId") rather than widening the entry to the whole panel.
ALLOW_EMPTY = {
    "Slow LLM requests (over 3 s, slowest first)",  # the sample claude_code.llm_request span takes 2 s
    "Span errors per hour",  # the sample spans all carry status OK, so status_code="STATUS_CODE_ERROR" matches nothing
}


def fix(s, sub):
    for k, v in sorted(sub.items(), key=lambda kv: -len(kv[0])): s = s.replace(k, v)
    return s


def get(path, params):
    req = urllib.request.Request(G + path + "?" + urllib.parse.urlencode(params), headers={"Authorization": auth} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)
    except urllib.error.HTTPError as e: return {"status": "error", "error": e.read().decode()[:300]}


def main():
    unknown = [a for a in sys.argv[1:] if a.startswith("--") and a not in OPTIONS]
    if unknown: sys.exit(f"unknown option {unknown[0]}, this script takes {' and '.join(OPTIONS)}")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rng = sys.argv[sys.argv.index("--range") + 1] if "--range" in sys.argv else "3h"
    args = [a for a in args if a != rng]
    require_data = "--require-data" in sys.argv
    with open(args[0] if args else os.path.join(ROOT, "dashboards", "claude-code.json")) as f: d = json.load(f)
    secs = {"m": 60, "h": 3600, "d": 86400}[rng[-1]] * int(rng[:-1])
    now = int(time.time()); start = now - secs
    # Grafana picks roughly range / 600 points, never finer than 15 s; the rate interval is at least 4x the step
    step = max(15, secs // 600)
    sub = {"$__interval_ms": str(step * 1000), "$__rate_interval_ms": str(max(step * 4, 60) * 1000), "$__rate_interval": f"{max(step * 4, 60)}s", "$__interval": f"{step}s", "$__auto": f"{step}s", "$__range": rng, "$model": ".*", "$host": ".*"}
    bad = 0; empty = []
    for p in d["panels"]:
        for t in p.get("targets", []):
            ds = t["datasource"]["type"]; q = fix(t.get("expr") or t.get("query", ""), sub); t0 = time.time()
            if ds == "prometheus":
                r = get("/api/datasources/proxy/uid/prometheus/api/v1/query_range", {"query": q, "start": start, "end": now, "step": step})
                ok = r.get("status") == "success"; n = len(r["data"]["result"]) if ok else r.get("error")
            elif ds == "loki":
                r = get("/api/datasources/proxy/uid/loki/loki/api/v1/query_range",
                        {"query": q, "start": start * 10**9, "end": now * 10**9, "step": step, "limit": 5})
                ok = r.get("status") == "success"; n = len(r["data"]["result"]) if ok else r.get("error")
            else:  # tempo
                r = get("/api/datasources/proxy/uid/tempo/api/search", {"q": q, "start": start, "end": now, "limit": 5})
                ok = "error" not in r and "status" not in r; n = len(r.get("traces", [])) if ok else r.get("error")
            # Several panels query different metrics or fields per target, so count rows per target: rows in one
            # target must not cover for a rename in another.
            label = p["title"] if len(p["targets"]) == 1 else f"{p['title']} [{t['refId']}]"
            allowed = p["title"] in ALLOW_EMPTY or (p["title"], t.get("refId")) in ALLOW_EMPTY
            flag = "ok" if ok else "ERR"
            if not ok: bad += 1
            elif require_data and n == 0:
                flag = "none" if allowed else "EMPTY"  # "none" is an allowlisted panel with nothing to show
                if not allowed: empty.append(label)
            print(f"{flag:<5} {str(n):>4} {time.time() - t0:5.1f}s  [{ds[:4]}] {p['title'][:48]}" + ("" if ok else f"\n      {n}"))
    print("FAILED:", bad)
    if require_data:
        print("EMPTY:", len(empty))
        for label in empty: print("     ", label)
    sys.exit(1 if bad or empty else 0)


if __name__ == "__main__":
    main()
