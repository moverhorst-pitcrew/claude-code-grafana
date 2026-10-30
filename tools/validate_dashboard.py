#!/usr/bin/env python3
"""Run every query of dashboards/claude-code.json against Grafana's datasources and report results.
Uses GRAFANA_PASSWORD from the environment when set (never stored in files); otherwise queries run anonymously.

  tools/validate_dashboard.py [dashboard.json] [--range 15m|3h|24h|7d]   (default 3h; use several ranges to check scaling)
"""
import base64, json, os, sys, time, urllib.parse, urllib.request
G = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")
# Without GRAFANA_PASSWORD the queries run anonymously, which works when anonymous viewing is enabled.
auth = "Basic " + base64.b64encode(f"admin:{os.environ['GRAFANA_PASSWORD']}".encode()).decode() if "GRAFANA_PASSWORD" in os.environ else None
args = [a for a in sys.argv[1:] if not a.startswith("--")]
rng = sys.argv[sys.argv.index("--range") + 1] if "--range" in sys.argv else "3h"
args = [a for a in args if a != rng]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(args[0] if args else os.path.join(ROOT, "dashboards", "claude-code.json")))
secs = {"m": 60, "h": 3600, "d": 86400}[rng[-1]] * int(rng[:-1])
now = int(time.time()); start = now - secs
# Grafana picks roughly range / 600 points, never finer than 15 s; the rate interval is at least 4x the step
step = max(15, secs // 600)
sub = {"$__interval_ms": str(step * 1000), "$__rate_interval_ms": str(max(step * 4, 60) * 1000), "$__rate_interval": f"{max(step * 4, 60)}s", "$__interval": f"{step}s", "$__auto": f"{step}s", "$__range": rng, "$model": ".*", "$host": ".*"}
def fix(s):
    for k, v in sorted(sub.items(), key=lambda kv: -len(kv[0])): s = s.replace(k, v)
    return s
def get(path, params):
    req = urllib.request.Request(G + path + "?" + urllib.parse.urlencode(params), headers={"Authorization": auth} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)
    except urllib.error.HTTPError as e: return {"status": "error", "error": e.read().decode()[:300]}
bad = 0
for p in d["panels"]:
    for t in p.get("targets", []):
        ds = t["datasource"]["type"]; q = fix(t.get("expr") or t.get("query", "")); t0 = time.time()
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
        flag = "ok " if ok else "ERR"
        if not ok: bad += 1
        print(f"{flag} {str(n):>4} {time.time() - t0:5.1f}s  [{ds[:4]}] {p['title'][:48]}" + ("" if ok else f"\n      {n}"))
print("FAILED:", bad)
sys.exit(1 if bad else 0)
