#!/usr/bin/env python3
"""Wait until the sample telemetry of tools/send_sample_telemetry.py can be queried, so
tools/validate_dashboard.py --require-data judges a loaded stack instead of one that is still ingesting.

Three things lag behind the OTLP push: Tempo makes a trace searchable only after its ingester flushes,
Tempo's metrics-generator writes traces_spanmetrics_* once per collection interval, and rate() needs two
samples of a counter. Exits non-zero naming whatever is still missing, so a stuck stack is not mistaken
for a renamed metric.

  tools/wait_for_telemetry.py [timeout-seconds]   (default 180)
"""
import json, os, sys, time, urllib.parse, urllib.request
G = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000") + "/api/datasources/proxy/uid"
TRACE = '{ resource.service.name = "claude-code" && name = "claude_code.interaction" }'


def get(path, **params):
    try:
        with urllib.request.urlopen(f"{G}{path}?{urllib.parse.urlencode(params)}", timeout=30) as r: return json.load(r)
    except Exception as e:  # a datasource that is still starting answers with an error; keep polling
        print(f"      {type(e).__name__}: {e}")
        return {}


def samples(selector):
    """How many samples Prometheus has for the series the selector matches, 0 if it matches nothing."""
    r = get("/prometheus/api/v1/query", query=f"max(count_over_time({selector}[10m]))")
    result = r.get("data", {}).get("result")
    return float(result[0]["value"][1]) if result else 0


def pending():
    """The checks that are not satisfied yet, as readable strings."""
    now = int(time.time())
    traces = len(get("/tempo/api/search", q=TRACE, start=now - 3600, end=now, limit=5).get("traces", []))
    spans = samples('traces_spanmetrics_calls_total{service="claude-code"}')
    counter = samples("claude_code_cost_usage_USD_total")
    out = []
    if not traces: out.append("no searchable trace in Tempo")
    if spans < 2: out.append(f"{spans:.0f} of 2 traces_spanmetrics_calls_total samples")
    if counter < 2: out.append(f"{counter:.0f} of 2 claude_code_cost_usage_USD_total samples")
    return out


def main():
    timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    deadline = time.time() + timeout
    while True:
        left = pending()
        if not left:
            print("sample telemetry is queryable")
            return
        print("waiting for: " + "; ".join(left), flush=True)
        if time.time() >= deadline:
            sys.exit(f"the stack did not make the sample telemetry queryable within {timeout}s: " + "; ".join(left))
        time.sleep(5)


if __name__ == "__main__":
    main()
