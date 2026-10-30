#!/usr/bin/env python3
"""Push a small set of sample OTLP metrics, logs and traces to an otel-lgtm stack (OTLP/HTTP JSON), so
tools/validate_dashboard.py has something to query instead of an empty Grafana.

Covers the metric and event names the dashboard queries (see docs/telemetry-reference.md): two sample
sessions on two models, one subscription-usage snapshot, and one trace covering each span type the
dashboard shows.

  tools/send_sample_telemetry.py [otlp-http-base-url]   (default http://127.0.0.1:4318, or $OTLP_URL)
"""
import json, os, sys, time, urllib.request

OTLP = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("OTLP_URL", "http://127.0.0.1:4318")).rstrip("/")
NOW = time.time()
HOST = "ci-runner"

SESSIONS = [
    {"session_id": "ci00000000000001", "model": "claude-sonnet-5-20261101", "query_source": "main", "effort": "",
     "agent_name": "", "mcp_server_name": "", "mcp_tool_name": "", "skill_name": "", "user_email": "ci@example.com",
     "start_type": "fresh", "tool_name": "Bash", "tool_ok": "true"},
    {"session_id": "ci00000000000002", "model": "claude-haiku-4-5-20251001", "query_source": "subagent", "effort": "high",
     "agent_name": "Explore", "mcp_server_name": "github", "mcp_tool_name": "search_issues", "skill_name": "turn-optimizer",
     "user_email": "ci@example.com", "start_type": "continue", "tool_name": "Edit", "tool_ok": "false"},
]


def ns(t):
    return str(int(t * 1e9))


def attrs(**kv):
    out = []
    for k, v in kv.items():
        val = {"doubleValue": v} if isinstance(v, float) else ({"intValue": v} if isinstance(v, int) else {"stringValue": str(v)})
        out.append({"key": k, "value": val})
    return out


def post(path, body):
    req = urllib.request.Request(OTLP + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def metric(name, points, sum_monotonic=None):
    """points: list of (attrs: dict, value)."""
    data_points = [{"startTimeUnixNano": ns(NOW - 3600), "timeUnixNano": ns(NOW), "asDouble": float(v), "attributes": attrs(**a)}
                   for a, v in points]
    m = {"name": name, "unit": ""}
    if sum_monotonic is None:
        m["gauge"] = {"dataPoints": data_points}
    else:
        m["sum"] = {"dataPoints": data_points, "aggregationTemporality": 2, "isMonotonic": sum_monotonic}
    return m


def session_metrics(s):
    common = {"model": s["model"], "effort": s["effort"], "query_source": s["query_source"], "agent_name": s["agent_name"],
              "mcp_server_name": s["mcp_server_name"], "mcp_tool_name": s["mcp_tool_name"], "skill_name": s["skill_name"],
              "host_name": HOST, "session_id": s["session_id"], "user_email": s["user_email"], "service_version": "2.1.314"}
    out = [metric("claude_code_cost_usage_USD_total", [(common, 0.42)], sum_monotonic=True)]
    for t, v in (("input", 1200), ("output", 340), ("cacheRead", 5000), ("cacheCreation", 800)):
        out.append(metric("claude_code_token_usage_tokens_total", [({**common, "type": t}, v)], sum_monotonic=True))
    out.append(metric("claude_code_session_count_total", [({"start_type": s["start_type"], "host_name": HOST}, 1)], sum_monotonic=True))
    for t, v in (("user", 300), ("cli", 900)):
        out.append(metric("claude_code_active_time_seconds_total", [({"type": t, "host_name": HOST}, v)], sum_monotonic=True))
    for t, v in (("added", 120), ("removed", 30)):
        out.append(metric("claude_code_lines_of_code_count_total", [({"type": t, "model": s["model"], "host_name": HOST}, v)], sum_monotonic=True))
    return out


def all_metrics():
    out = []
    for s in SESSIONS:
        out += session_metrics(s)
    out.append(metric("claude_code_code_edit_tool_decision_total",
                       [({"tool_name": "Edit", "decision": "accept", "source": "config", "language": "python", "host_name": HOST}, 4)],
                       sum_monotonic=True))
    out.append(metric("claude_code_commit_count_total", [({"host_name": HOST}, 1)], sum_monotonic=True))
    out.append(metric("claude_code_pull_request_count_total", [({"host_name": HOST}, 1)], sum_monotonic=True))
    # Subscription usage: normally pushed by usage-poller/poll_usage.py, sent here so those panels get data too.
    out += [
        metric("claude_usage_utilization_ratio", [({"limit": "session", "scope": ""}, 0.3)]),
        metric("claude_usage_utilization_ratio", [({"limit": "weekly_all", "scope": ""}, 0.4)]),
        metric("claude_usage_utilization_ratio", [({"limit": "weekly_scoped", "scope": "Opus"}, 0.2)]),
        metric("claude_usage_reset_timestamp_seconds", [({"limit": "session", "scope": ""}, NOW + 3600)]),
        metric("claude_usage_reset_timestamp_seconds", [({"limit": "weekly_all", "scope": ""}, NOW + 86400)]),
        metric("claude_usage_window_seconds", [({"limit": "session", "scope": ""}, 18000)]),
        metric("claude_usage_window_seconds", [({"limit": "weekly_all", "scope": ""}, 604800)]),
        metric("claude_usage_weekly_share_ratio", [({"source": "Claude Code"}, 0.7)]),
        metric("claude_usage_weekly_share_ratio", [({"source": "Chats"}, 0.3)]),
        metric("claude_usage_plan_info", [({"plan": "max", "tier": "tier_4"}, 1)]),
        metric("claude_usage_credits_used", [({"currency": "USD"}, 2.5)]),
        metric("claude_usage_credits_enabled", [({"currency": "USD"}, 1)]),
        metric("claude_usage_allowance_limit", [({"allowance": "iguana_necktie"}, 50)]),
        metric("claude_usage_allowance_used", [({"allowance": "iguana_necktie"}, 12.5)]),
        metric("claude_usage_last_success_timestamp_seconds", [({}, NOW)]),
    ]
    return out


def resource():
    return {"attributes": attrs(**{"service.name": "claude-code", "service.version": "2.1.314", "host.name": HOST})}


def send_metrics():
    post("/v1/metrics", {"resourceMetrics": [{"resource": resource(),
                                               "scopeMetrics": [{"scope": {"name": "sample-telemetry"}, "metrics": all_metrics()}]}]})


def log_record(event_name, t, **fields):
    return {"timeUnixNano": ns(t), "observedTimeUnixNano": ns(t), "severityNumber": 9, "body": {"stringValue": event_name},
            "attributes": attrs(event_name=event_name, host_name=HOST, **fields)}


def all_logs():
    out = []
    for i, s in enumerate(SESSIONS):
        t = NOW - 60 * i
        out.append(log_record("api_request", t, model=s["model"], duration_ms=1200 + i * 300, ttft_ms=250 + i * 50,
                               cost_usd=0.1 + i * 0.05, input_tokens=900, output_tokens=300, cache_read_tokens=4000,
                               cache_creation_tokens=600, effort=s["effort"] or "not_set", speed="normal",
                               query_source=s["query_source"], session_id=s["session_id"]))
        out.append(log_record("tool_result", t, tool_name=s["tool_name"], success=s["tool_ok"], duration_ms=400,
                               error_type="" if s["tool_ok"] == "true" else "permission_denied", decision_source="config",
                               mcp_server_scope="", tool_result_size_bytes=2048, session_id=s["session_id"]))
        out.append(log_record("tool_decision", t, tool_name=s["tool_name"], decision="accept", source="config",
                               permission_mode="default", classifier_verdict="allow", session_id=s["session_id"]))
        out.append(log_record("user_prompt", t, command_name="" if i == 0 else "usage", prompt_length=42, session_id=s["session_id"]))
        out.append(log_record("subagent_completed", t, agent_type="Explore", model=s["model"], duration_ms=5000,
                               total_tokens=3000, session_id=s["session_id"]))
    out.append(log_record("api_error", NOW, status_code="529", model=SESSIONS[0]["model"]))
    out.append(log_record("api_retries_exhausted", NOW, model=SESSIONS[0]["model"]))
    out.append(log_record("mcp_server_connection", NOW, status="connected", error_code="", duration_ms=120, transport_type="stdio"))
    out.append(log_record("mcp_server_connection", NOW, status="failed", error_code="ENOENT", duration_ms=50, transport_type="stdio"))
    out.append(log_record("hook_execution_complete", NOW, hook_event="PreToolUse", hook_name="format-on-save",
                           total_duration_ms=80, num_blocking=0, num_non_blocking_error=0))
    out.append(log_record("compaction", NOW, trigger="auto", pre_tokens=180000, post_tokens=40000, duration_ms=3000, success="true"))
    out.append(log_record("skill_activated", NOW, skill_name="turn-optimizer", invocation_trigger="manual"))
    out.append(log_record("plugin_loaded", NOW, plugin_name="ponytail", plugin_scope="user", marketplace_name="anthropics"))
    return out


def send_logs():
    post("/v1/logs", {"resourceLogs": [{"resource": resource(),
                                         "scopeLogs": [{"scope": {"name": "sample-telemetry"}, "logRecords": all_logs()}]}]})


def rand_id(nbytes):
    """traceId/spanId take hex in OTLP/JSON (the collector's ID type has its own JSON mapping, unlike other bytes fields)."""
    return os.urandom(nbytes).hex()


def span(trace_id, span_id, parent_id, name, t0, t1, **fields):
    s = {"traceId": trace_id, "spanId": span_id, "name": name, "kind": 1,
         "startTimeUnixNano": ns(t0), "endTimeUnixNano": ns(t1), "attributes": attrs(**fields), "status": {"code": 1}}
    if parent_id:
        s["parentSpanId"] = parent_id
    return s


def all_spans():
    trace_id, root, llm, tool = rand_id(16), rand_id(8), rand_id(8), rand_id(8)
    t = NOW
    return [
        span(trace_id, root, "", "claude_code.interaction", t - 5, t, model=SESSIONS[0]["model"]),
        span(trace_id, llm, root, "claude_code.llm_request", t - 5, t - 3, model=SESSIONS[0]["model"], ttft_ms=300, output_tokens=300),
        span(trace_id, tool, root, "claude_code.tool", t - 3, t - 1, tool_name="Bash", bash_command_class="read"),
    ]


def send_traces():
    post("/v1/traces", {"resourceSpans": [{"resource": resource(),
                                            "scopeSpans": [{"scope": {"name": "sample-telemetry"}, "spans": all_spans()}]}]})


def main():
    send_metrics()
    send_logs()
    send_traces()
    print(f"sample telemetry sent to {OTLP}")


if __name__ == "__main__":
    main()
