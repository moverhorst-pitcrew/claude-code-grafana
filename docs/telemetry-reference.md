# Telemetry reference

What Claude Code exports, and what the dashboard does with it. Names were taken from Claude Code 2.1.x and can change
between versions; if a panel goes empty after an upgrade, compare this list with what your Prometheus and Loki contain.

Claude Code sends OpenTelemetry **metrics** (to Prometheus) and **events** (to Loki, as log lines with structured
metadata). The two are linked by `session_id`.

## Metrics (Prometheus)

All are counters. They carry `service_version` and `terminal_type` (empty for interactive sessions, `non-interactive`
for headless runs such as agents) plus the labels below. With `OTEL_RESOURCE_ATTRIBUTES=host.name=<name>` they also
carry `host_name`, which drives the dashboard's *Host* variable.

| Metric | Extra labels | Used for |
|---|---|---|
| `claude_code_cost_usage_USD_total` | `model`, `effort`, `query_source`, `agent_name`, `mcp_server_name`, `mcp_tool_name`, `skill_name` | Cost tiles and every "cost by ..." panel |
| `claude_code_token_usage_tokens_total` | same as cost, plus `type` (`input`, `output`, `cacheRead`, `cacheCreation`) | Token panels, cache hit rate |
| `claude_code_session_count_total` | `start_type` | Sessions, sessions started per hour |
| `claude_code_active_time_seconds_total` | `type` (`user`, `cli`) | Active time, activity share |
| `claude_code_lines_of_code_count_total` | `model`, `type` (`added`, `removed`) | Productivity |
| `claude_code_code_edit_tool_decision_total` | `tool_name`, `decision`, `source`, `language` | Edit decisions, accept rate, edits by language |
| `claude_code_commit_count_total` | | Commits |
| `claude_code_pull_request_count_total` | | Pull requests |

Counters are per session (`session_id` is a label), so "total over a range" panels take `max_over_time` per series and
sum, which also counts usage a session had before the range started. See the note in the README.

## Events (Loki)

Selector: `{service_name="claude-code"} | event_name="<name>"`. Fields are structured metadata.

| Event | Notable fields | Used for |
|---|---|---|
| `api_request` | `model`, `duration_ms`, `ttft_ms`, `cost_usd`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `effort`, `query_source` | Latency, time to first token, cost per request, tokens per minute |
| `api_error`, `api_retries_exhausted` | `status_code` | API errors (no events means no errors) |
| `tool_result` | `tool_name`, `success`, `duration_ms`, `error_type`, `decision_source`, `mcp_server_scope` | Tool calls, failures, durations, permission wait |
| `tool_decision` | `tool_name`, `decision`, `source`, `permission_mode` | Approved and blocked tools |
| `mcp_server_connection` | `status`, `error_code`, `duration_ms`, `transport_type` | MCP server status and errors |
| `hook_execution_start` / `hook_execution_complete` | `hook_event`, `hook_name`, `total_duration_ms`, `num_blocking` | Hooks (slow or blocking) |
| `compaction` | `trigger`, `pre_tokens`, `post_tokens`, `duration_ms`, `success` | Context compaction |
| `skill_activated` | `skill_name`, `invocation_trigger` | Skills |
| `plugin_loaded` | `plugin_name`, `plugin_scope` | Plugins |
| `user_prompt` | `command_name`, `prompt_length`; the text fields are `<REDACTED>` | Prompts per hour, slash commands |
| `assistant_response` | `response_length`; `response` is `<REDACTED>` | Event stream |
| `internal_error`, `feedback_survey`, `at_mention`, `managed_settings_resolved`, `retention_sweep`, `hook_registered` | | Event stream only |

Prompt and response text are redacted by Claude Code unless `OTEL_LOG_USER_PROMPTS` is set, which this project never sets.

## Traces (Tempo)

With `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` Claude Code exports spans for each interaction, LLM request and tool call.
The trace panels search them with TraceQL, and a log line's `trace_id` links to its trace.

## Subscription usage (`claude_usage_*`, optional)

Produced by [`usage-poller/poll_usage.py`](../usage-poller/poll_usage.py), not by Claude Code. They are account-wide,
so the *Host* variable does not filter them.

| Metric | Labels | Meaning |
|---|---|---|
| `claude_usage_utilization_ratio` | `limit` (`session`, `weekly_all`, `weekly_scoped`), `scope` | Fraction of each limit used |
| `claude_usage_reset_timestamp_seconds`, `claude_usage_window_seconds` | `limit`, `scope` | When it resets, and the window length |
| `claude_usage_limit_active` | `limit`, `scope` | 1 while that limit is throttling |
| `claude_usage_weekly_share_ratio` | `source` | Share of the weekly limit by product |
| `claude_usage_credits_used`, `claude_usage_credits_enabled` | `currency` | Extra-usage credits |
| `claude_usage_allowance_*` | `allowance` | Dollar allowances the API reports under internal names |
| `claude_usage_plan_info` | `plan`, `tier` | Plan |
| `claude_usage_poll_ok`, `claude_usage_poll_failure_info`, `claude_usage_last_success_timestamp_seconds` | `reason` | Poller health |
