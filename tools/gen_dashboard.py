#!/usr/bin/env python3
"""Generate the "Claude Code" Grafana dashboard JSON (metrics, logs and traces).

Usage (from the repo root): python3 tools/gen_dashboard.py > dashboards/claude-code.json

Signals (Claude Code 2.1.2xx-2.1.3xx, last checked on 2.1.314, OTLP -> grafana/otel-lgtm):
  Prometheus  claude_code_{session_count,cost_usage_USD,token_usage_tokens,active_time_seconds,
              lines_of_code_count,commit_count,pull_request_count,code_edit_tool_decision}_total
              traces_spanmetrics_* (span_name = claude_code.*)
  Loki        {service_name="claude-code"} with event_name structured metadata
              (api_request, tool_result, tool_decision, user_prompt, skill_activated, hook_execution_complete, compaction, ...)
  Tempo       claude_code.interaction / llm_request / tool / tool.execution / tool.blocked_on_user

Design notes
  * Counter metrics are per-session series and Prometheus increase() drops a session's first
    sample, so short sessions vanish from rate panels. Totals therefore use max_over_time per series
    over the range, and time-based cost/token/request panels are built from the exact api_request
    log events instead.
  * Reading order: Overview (numbers) -> Cost -> Productivity -> API -> Tools -> Subagents -> Hooks/compaction -> Traces -> Events.
"""
import json, re

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}

_id = [0]
_refs = "ABCDEFGHIJKLMNOP"


def norm(unit, decimals=None):
    """Number-format policy for EU/NL readers (Grafana always prints a decimal point and cannot localize).

    * money always has 4 decimals ($1.8153): a dot followed by exactly 3 digits reads as a thousands
      separator in NL ("$1.815" = 1815), so 3 decimals are never used;
    * big counts use SI scaling without a unit word (5.8 M, 501 k) instead of dotted thousands;
    * every other unit gets explicit decimals so auto-decimals can never produce a 3-digit fraction.
    """
    if unit == "short":
        unit = "si:"
    if unit == "currencyUSD":
        return unit, 4
    if decimals is None:
        decimals = 1 if unit == "percentunit" else (None if unit == "si:" else 2)
    return unit, decimals


# Charts that used to plot "count per interval" (the interval grows with the dashboard range, so the same activity
# looked bigger when zoomed out) are plotted as rates instead: old title -> (new title, unit of time, interval variable).
# The query is multiplied by <unit in ms> / <interval in ms>, so the y axis means the same at every range.
PER = {
    "Cost over time by model": ("Cost per hour by model", "hour", "$__interval_ms"),
    "Tokens over time by type": ("Tokens per minute by type", "min", "$__interval_ms"),
    "Prompts over time": ("Prompts per hour", "hour", "$__interval_ms"),
    "Sessions started": ("Sessions started per hour", "hour", "$__interval_ms"),
    "API requests": ("API requests per minute", "min", "$__interval_ms"),
    "API errors by HTTP status": ("API errors per hour", "hour", "$__interval_ms"),
    "Tool calls by tool": ("Tool calls per minute", "min", "$__interval_ms"),
    "Tool failures by tool and error": ("Tool failures per hour", "hour", "$__interval_ms"),
    "Permission decisions": ("Permission decisions per minute", "min", "$__interval_ms"),
    "Events by type": ("Events per minute by type", "min", "$__interval_ms"),
    "Hook runs": ("Hook runs per minute", "min", "$__interval_ms"),
    "Span errors": ("Span errors per hour", "hour", "1000"),   # already a per-second rate()
}
UNIT_MS = {"min": 60000, "hour": 3600000}


def nid():
    _id[0] += 1
    return _id[0]


def targets(ds, items, instant=False, fmt=None):
    out = []
    for i, it in enumerate(items):
        expr, legend = it if isinstance(it, tuple) else (it, "")
        t = {"datasource": ds, "expr": expr, "legendFormat": legend, "refId": _refs[i]}
        if ds is PROM:
            t["instant"] = instant
            t["range"] = not instant
            if fmt:
                t["format"] = fmt
        else:  # loki
            t["queryType"] = "instant" if instant else "range"
            if fmt == "table":
                t["format"] = "table"
        out.append(t)
    return out


def base(kind, title, x, y, w, h, ds, tg, desc=""):
    return {"id": nid(), "type": kind, "title": title, "description": desc,
            "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": ds, "targets": tg}


def stat(title, expr, unit, x, y, w=4, h=4, ds=PROM, desc="", color="blue", decimals=None, steps=None, bg=False, nv="0", mappings=None):
    """Plain numbers are neutral (colour arg kept for call sites); only `steps` adds a good/bad colour."""
    p = base("stat", title, x, y, w, h, ds, targets(ds, [expr], instant=True), desc)
    unit, decimals = norm(unit, decimals)
    d = {"unit": unit, "color": {"mode": "fixed", "fixedColor": "text"}, "noValue": nv, "mappings": mappings or []}
    if decimals is not None:
        d["decimals"] = decimals
    if steps:
        d["color"] = {"mode": "thresholds"}
        d["thresholds"] = {"mode": "absolute", "steps": steps}
    p["fieldConfig"] = {"defaults": d, "overrides": []}
    p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "background" if bg else "value", "graphMode": "none", "textMode": "value", "justifyMode": "center"}
    return p


def ts(title, items, unit, x, y, w=12, h=8, ds=PROM, desc="", stack=False, bars=False, thr=None, overrides=None, ymax=None):
    if title in PER:
        title, per, den = PER[title]
        items = [(f"({e}) * {UNIT_MS[per]} / {den}", l) for e, l in items]
        is_rate_count = unit in ("short", "none")   # keep compact k / M units; just fix the decimals below
        desc = (desc + " " if desc else "") + f"Shown as a rate per {per}, so the scale is the same at every time range."
    p = base("timeseries", title, x, y, w, h, ds, targets(ds, items), desc)
    p["interval"] = "1m"   # minimum step: 15 s buckets hold 0-1 samples and make quantiles/bars jagged
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 40 if (stack or bars) else 10,
              "showPoints": "auto", "pointSize": 5, "spanNulls": 1800000, "barAlignment": 0,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    unit, dec = norm(unit)
    if locals().get("is_rate_count"):
        dec = 1   # rates: one decimal ("60.0"), never a 3-digit fraction
    dflt = {"unit": unit, "custom": custom, "noValue": "none in this range", "min": 0}
    if dec is not None:
        dflt["decimals"] = dec
    if ymax is not None:
        dflt["max"] = ymax
    if thr:   # dashed good/warn/bad lines so a glance shows where "bad" starts
        dflt["thresholds"] = {"mode": "absolute", "steps": thr}
        custom["thresholdsStyle"] = {"mode": "dashed"}
    p["fieldConfig"] = {"defaults": dflt, "overrides": overrides or []}
    p["options"] = {"legend": {"displayMode": "list", "placement": "bottom", "calcs": [], "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}}
    return p


def bargauge(title, expr, unit, x, y, w=6, h=7, ds=PROM, desc="", legend="", colors="palette-classic"):
    p = base("bargauge", title, x, y, w, h, ds, targets(ds, [(expr, legend)], instant=True), desc)
    unit, dec = norm(unit)
    bd = {"unit": unit, "color": {"mode": "fixed", "fixedColor": "blue"}, "noValue": "none in this range", "min": 0}
    if legend:   # name from label values; a single series otherwise loses its name
        bd["displayName"] = legend.replace("{{", "${__field.labels.").replace("}}", "}")
    if dec is not None:
        bd["decimals"] = dec
    if unit == "percentunit":
        bd["max"] = 1
    p["fieldConfig"] = {"defaults": bd, "overrides": []}
    p["options"] = {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True, "valueMode": "color",
                    "namePlacement": "top", "sizing": "manual", "minVizHeight": 26, "maxVizHeight": 34, "minVizWidth": 8,
                    "text": {"titleSize": 13, "valueSize": 14},
                    "reduceOptions": {"calcs": ["lastNotNull"], "values": False, "fields": ""}}
    return p


def table(title, ds, expr, x, y, w=12, h=8, desc="", rename=None, order=None, hide=None, overrides=None):
    p = base("table", title, x, y, w, h, ds, targets(ds, [expr], instant=True, fmt="table"), desc)
    p["fieldConfig"] = {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}, "noValue": "none in this range"},
                        "overrides": overrides or []}
    p["options"] = {"showHeader": True, "cellHeight": "sm"}
    p["transformations"] = [{"id": "organize", "options": {
        "excludeByName": {k: True for k in (hide or ["Time"])},
        "indexByName": {k: i for i, k in enumerate(order or [])},
        "renameByName": rename or {}}}]
    return p


def count_table(title, expr, cols, x, y, w, h, desc=""):
    """Loki grouped count as a table; cols = [(label, header), ...]. Count renders as an inline bar."""
    order = [c for c, _ in cols] + ["Value #A"]
    rename = {c: n for c, n in cols}
    rename["Value #A"] = "Count"
    t = table(title, LOKI, expr, x, y, w, h, desc=desc, order=order, rename=rename,
              overrides=[{"matcher": {"id": "byName", "options": "Count"},
                          "properties": [{"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic"}},
                                         {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}},
                                         {"id": "min", "value": 0}, {"id": "decimals", "value": 0}]}])
    t["fieldConfig"]["defaults"]["noValue"] = "n/a"   # empty label (e.g. no classifier verdict), not missing data
    t["transformations"].append({"id": "sortBy", "options": {"fields": {}, "sort": [{"field": "Count", "desc": True}]}})
    return t


def tempo_table(title, query, x, y, w, h, table_type, desc="", rename=None, order=None, hide=None, sort=None, unit_overrides=None):
    p = base("table", title, x, y, w, h, TEMPO,
             [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "limit": 30, "tableType": table_type, "query": query}], desc)
    p["fieldConfig"] = {"defaults": {"custom": {"align": "auto"}, "noValue": "-"},
                        "overrides": unit_overrides or []}
    p["options"] = {"showHeader": True, "cellHeight": "sm"}
    alias = {"duration": "Duration", "time": "Start time", "spanID": "Span ID", "name": "Name"}
    tr = []
    if sort:
        tr.append({"id": "sortBy", "options": {"fields": {}, "sort": [{"field": alias.get(sort, sort), "desc": True}]}})
    index = {}
    for i, k in enumerate(order or []):
        index[k] = i
        if k in alias:
            index[alias[k]] = i
    tr.append({"id": "organize", "options": {
        "excludeByName": {**{k: True for k in (hide or [])}, **{alias[k]: True for k in (hide or []) if k in alias}},
        "indexByName": index,
        "renameByName": rename or {}}})
    p["transformations"] = tr
    return p


def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


# ---- shorthand selectors --------------------------------------------------------------------
M = '{model=~"$model"}'
TOK = "claude_code_token_usage_tokens_total"
COST = "claude_code_cost_usage_USD_total"
SESS = "claude_code_session_count_total"
ACT = "claude_code_active_time_seconds_total"
LOC = "claude_code_lines_of_code_count_total"
COM = "claude_code_commit_count_total"
PR = "claude_code_pull_request_count_total"
EDIT = "claude_code_code_edit_tool_decision_total"
RI = "$__rate_interval"
L = '{service_name="claude-code"}'
RNG = "$__range"


MCP_FAILED = ' | status="failed"'
TOOL_OK = ' | success="true"'
TOOL_FAILED = ' | success="false"'


def ev(name, extra=""):
    return f'{L} | event_name="{name}"{extra}'


def lq(fn, name, rng="$__auto", unwrap=None, by=None, q=None, extra=""):
    sel = ev(name, extra) + (' | unwrap %s | __error__=""' % unwrap if unwrap else "")
    inner = f"{sel} [{rng}]"
    if unwrap and q is not None:
        e = f"quantile_over_time({q}, {inner})"
    else:
        e = f"{fn}({inner})"
    return f"{e} by ({by})" if (by and unwrap) else (f"sum by ({by}) ({e})" if by else f"sum({e})")


def cnt(name, by, extra="", rng=RNG):
    """Loki range count grouped by labels, as an instant vector (for bar gauges / tables)."""
    return f"sum by ({by}) (count_over_time({ev(name, extra)} [{rng}]))"


# Health judges everything against the user's OWN history, so it works in any setup:
#   * ratio tiles: value = (now over the dashboard range) / (usual over BASE); 1.0x = as usual
#   * the colour steps below are multiples of that baseline (not absolute numbers)
#   * counts of errors / failed MCP servers have a healthy baseline of exactly zero, so any occurrence is flagged
BASE = "7d"
UP = [{"color": "green", "value": None}, {"color": "orange", "value": 1.5}, {"color": "red", "value": 2.5}]      # higher = worse
DOWN = [{"color": "red", "value": None}, {"color": "orange", "value": 0.75}, {"color": "green", "value": 0.9}]   # lower = worse
ANY = [{"color": "green", "value": None}, {"color": "orange", "value": 1}]
desc_rng = ("Per-session counters: totals use max_over_time per series over the selected range, so a session that "
            "started before the range includes its earlier usage.")

panels = []
y = 0

# ============================================================================== Health
panels.append(row("Health", y)); y += 1


def ratio_note(what, up=True):
    return (f"{what} now (dashboard range) divided by the same figure over your last {BASE}: 1.0x = as usual. "
            + ("Orange from 1.5x, red from 2.5x." if up else "Orange below 0.9x, red below 0.75x of usual."))


def api_q(field, q, rng):
    return f'quantile_over_time({q}, {ev("api_request")} | unwrap {field} | __error__="" [{rng}]) by ()'


def lat_ratio(field):
    return f"{api_q(field, 0.95, RNG)} / {api_q(field, 0.95, BASE)}"


def tool_n(rng, fail=False):
    extra = ' | success="false"' if fail else ""
    return f'(sum(count_over_time({ev("tool_result", extra)} [{rng}])) or vector(0))'


def cache_ratio(rng):
    return (f'sum(max_over_time({TOK}{{type="cacheRead",model=~"$model"}}[{rng}])) / '
            f'sum(max_over_time({TOK}{{type=~"cacheRead|input|cacheCreation",model=~"$model"}}[{rng}]))')


def cost_per_req(rng):
    return (f'(sum(sum_over_time({ev("api_request")} | unwrap cost_usd | __error__="" [{rng}])) / '
            f'sum(count_over_time({ev("api_request")} [{rng}])))')


X = "suffix: x"   # unit for ratio tiles
BASELINE_LINES = [
    {"matcher": {"id": "byName", "options": n},
     "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 8]}},
                    {"id": "color", "value": {"mode": "fixed", "fixedColor": c}},
                    {"id": "custom.lineWidth", "value": 1}, {"id": "custom.showPoints", "value": "never"},
                    {"id": "custom.fillOpacity", "value": 0}]}
    for n, c in (("usual p95 (7 d)", "text"), ("1.5x usual p95", "orange"))]
panels += [
    stat("API errors", f'sum(count_over_time({ev("api_error")} [{RNG}])) or vector(0)', "short", 0, y, 6, 4, ds=LOKI, bg=True, steps=ANY,
         desc="api_error events in the range. A healthy API has none, so any error is flagged."),
    stat("Failed MCP servers", f'sum(count_over_time({ev("mcp_server_connection", MCP_FAILED)} [{RNG}])) or vector(0)', "short",
         6, y, 6, 4, ds=LOKI, bg=True, steps=ANY, desc="MCP servers that failed to connect (per session start). A healthy setup has none."),
    stat("Tool failure rate vs usual",
         f'((({tool_n(RNG, True)} + 1) / ({tool_n(RNG)} + 2)) / (({tool_n(BASE, True)} + 1) / ({tool_n(BASE)} + 2)))',
         X, 12, y, 6, 4, ds=LOKI, bg=True, steps=UP, nv="no data",
         desc=ratio_note("Share of tool calls that failed") + " Lightly smoothed (+1/+2) so a single failure early on does not explode the ratio."),
    stat("Cache hit rate vs usual", f"({cache_ratio(RNG)}) / ({cache_ratio(BASE)})", X, 18, y, 6, 4, bg=True, steps=DOWN, nv="no data",
         desc=ratio_note("Cache hit share", up=False) + " Low means prompts are being re-read at full price."),
]
y += 4
panels += [
    stat("API latency p95 vs usual", lat_ratio("duration_ms"), X, 0, y, 8, 4, ds=LOKI, bg=True, steps=UP, nv="no data",
         desc=ratio_note("p95 request duration")),
    stat("Time to first token p95 vs usual", lat_ratio("ttft_ms"), X, 8, y, 8, 4, ds=LOKI, bg=True, steps=UP, nv="no data",
         desc=ratio_note("p95 time to first token")),
    stat("Cost per request vs usual", f"{cost_per_req(RNG)} / {cost_per_req(BASE)}", X, 16, y, 8, 4, ds=LOKI, bg=True, steps=UP, nv="no data",
         desc=ratio_note("Average cost of an API request") + " Rises when context grows or a pricier model is used."),
]
y += 4

# ============================================================================== Subscription limits
# Data comes from usage-poller/poll_usage.py (the numbers behind /usage), pushed once a minute as claude_usage_* gauges.
panels.append(row("Subscription limits", y)); y += 1
SES, WEEK = 'limit="session"', 'limit="weekly_all"'
PACE = [{"color": "green", "value": None}, {"color": "orange", "value": 0.85}, {"color": "red", "value": 1}]


def lo(selector):
    """Last known value: the poller runs every 4 minutes and may skip a run (rate limit), so look back 15 minutes."""
    return f"last_over_time({selector}[15m])"


def used(sel):
    return lo(f"claude_usage_utilization_ratio{{{sel}}}")


def resets_in(sel):
    return f"clamp_min({lo(f'claude_usage_reset_timestamp_seconds{{{sel}}}')} - time(), 0)"


def pace(sel):
    """Where the window ends if the current pace continues: used / fraction of the window already elapsed."""
    return (f"{used(sel)} / clamp_min(1 - {resets_in(sel)} / {lo(f'claude_usage_window_seconds{{{sel}}}')}, 0.1)")


PACE_DESC = ("Used so far divided by the part of the window already gone, i.e. where this window ends if you keep the current pace. "
             "Orange from 85%; red above 100% (you would hit the limit before it resets). Early in a window it is unreliable.")
panels += [
    stat("5-hour limit used", used(SES), "percentunit", 0, y, nv="no data", desc="Share of the rolling 5-hour limit used."),
    stat("5-hour pace", pace(SES), "percentunit", 4, y, bg=True, steps=PACE, nv="no data", desc=PACE_DESC),
    stat("5-hour resets in", resets_in(SES), "dtdurations", 8, y, nv="no data"),
    stat("Weekly limit used", used(WEEK), "percentunit", 12, y, nv="no data", desc="Share of the 7-day limit used (all models)."),
    stat("Weekly pace", pace(WEEK), "percentunit", 16, y, bg=True, steps=PACE, nv="no data", desc=PACE_DESC),
    stat("Weekly resets in", resets_in(WEEK), "dtdurations", 20, y, nv="no data"),
]
y += 4
panels += [
    ts("Limit usage over time", [(lo("claude_usage_utilization_ratio"), "{{limit}} {{scope}}")], "percentunit", 0, y, 12, 8, ymax=1,
       desc="Utilization of each limit as reported by the usage endpoint; the 5-hour line drops back to 0 when its window resets."),
    bargauge("Weekly usage by product", f"sort_desc({lo('claude_usage_weekly_share_ratio')})", "percentunit", 12, y, 6, 8, legend="{{source}}",
             desc="Share of this week's usage by product (Claude Code, Chats, Cowork, ...)."),
    bargauge("Weekly limits by model", lo('claude_usage_utilization_ratio{limit="weekly_scoped"}'), "percentunit", 18, y, 6, 8, legend="{{scope}}",
             desc="Separate weekly limits for specific models, when your plan has them."),
]
y += 8
ONOFF = [{"type": "value", "options": {"1": {"text": "On", "index": 0}, "0": {"text": "Off", "index": 1}}}]
panels += [
    table("Plan", PROM, f"max by (plan, tier) ({lo('claude_usage_plan_info')})", 0, y, 6, 4, order=["plan", "tier"], hide=["Time", "Value"],
          rename={"plan": "Plan", "tier": "Rate-limit tier"}),
    stat("Credits used", f"sum({lo('claude_usage_credits_used')})", "currencyEUR", 6, y, 4, 4, nv="no data",
         desc="Usage credits spent beyond your plan limits (currency as reported by your account)."),
    stat("Credits", f"max({lo('claude_usage_credits_enabled')})", "none", 10, y, 3, 4, nv="no data", mappings=ONOFF,
         desc="Whether usage credits are switched on. When off, hitting a limit stops requests."),
    stat("Unnamed allowance", f"sum({lo('claude_usage_allowance_used')})", "prefix:$", 13, y, 4, 4, nv="n/a",
         desc="A dollar allowance the usage endpoint returns under an internal name (iguana_necktie); its meaning is not documented. Amount used so far."),
    stat("Cap", f"sum({lo('claude_usage_allowance_limit')})", "prefix:$", 17, y, 3, 4, nv="n/a", desc="Limit of the unnamed allowance to the left."),
    stat("Usage data age", f"time() - {lo('claude_usage_last_success_timestamp_seconds').replace('[15m]','[1h]')}", "s", 20, y, 4, 4, bg=True, decimals=0,
         steps=[{"color": "green", "value": None}, {"color": "orange", "value": 600}, {"color": "red", "value": 1500}], nv="not running",
         desc="Time since the last successful usage poll (it runs every 4 minutes). Orange after 10 minutes, red after 25: usually an "
              "expired login token (it refreshes when Claude Code is used) or rate limiting."),
]
y += 4

# ============================================================================== Overview
panels.append(row("Overview (selected time range)", y)); y += 1
panels += [
    stat("Sessions", f"sum(max_over_time({SESS}[{RNG}]))", "short", 0, y, desc=desc_rng, color="blue"),
    stat("Prompts", f'sum(count_over_time({ev("user_prompt")} [{RNG}]))', "short", 4, y, ds=LOKI, color="blue",
         desc="Prompts you sent (user_prompt events)."),
    stat("Active time", f"sum(max_over_time({ACT}[{RNG}]))", "s", 8, y, desc="Time actively working (you + Claude).", color="orange"),
    stat("Total cost", f"sum(max_over_time({COST}{M}[{RNG}]))", "currencyUSD", 12, y, desc=desc_rng, color="green", decimals=3),
    stat("Total tokens", f"sum(max_over_time({TOK}{M}[{RNG}]))", "short", 16, y, desc=desc_rng, color="purple"),
    stat("Avg cost / session", f"sum(max_over_time({COST}{M}[{RNG}])) / clamp_min(sum(max_over_time({SESS}[{RNG}])), 1)",
         "currencyUSD", 20, y, color="green", decimals=3),
]
y += 4
panels += [
    stat("Cache hit ratio",
         f'sum(max_over_time({TOK}{{type="cacheRead",model=~"$model"}}[{RNG}])) / clamp_min(sum(max_over_time({TOK}{{type=~"cacheRead|input|cacheCreation",model=~"$model"}}[{RNG}])), 1)',
         "percentunit", 0, y, desc="cacheRead / (cacheRead + input + cacheCreation). Higher = cheaper and faster."),
    stat("Requests", f'sum(count_over_time({ev("api_request")} [{RNG}]))', "short", 4, y, w=3, ds=LOKI, color="purple",
         desc="api_request events."),
    stat("API p95 latency", f'quantile_over_time(0.95, {ev("api_request")} | unwrap duration_ms | __error__="" [{RNG}]) by ()',
         "ms", 7, y, ds=LOKI, color="orange", desc="p95 request duration across all models."),
    stat("Tool success rate",
         f'sum(count_over_time({ev("tool_result", TOOL_OK)} [{RNG}])) / sum(count_over_time({ev("tool_result")} [{RNG}]))',
         "percentunit", 11, y, ds=LOKI, desc="Share of tool calls that succeeded."),
    stat("Lines", f"sum(max_over_time({LOC}{M}[{RNG}]))", "short", 15, y, w=3, color="blue", desc="Lines added + removed by Claude's edits."),
    stat("Commits", f"sum(max_over_time({COM}[{RNG}]))", "short", 18, y, w=3, color="blue"),
    stat("PRs", f"sum(max_over_time({PR}[{RNG}]))", "short", 21, y, w=3, color="blue", desc="Pull requests created."),
]
y += 4

# ============================================================================== Cost & tokens
panels.append(row("Cost and tokens", y)); y += 1
panels += [
    ts("Cost over time by model", [(f'sum by (model) (sum_over_time({ev("api_request")} | unwrap cost_usd | __error__="" [$__auto]))', "{{model}}")],
       "currencyUSD", 0, y, 12, 8, ds=LOKI, stack=True, bars=True,
       desc="Exact spend per interval, summed from api_request events (cost_usd)."),
    ts("Tokens over time by type", [
        (f'sum(sum_over_time({ev("api_request")} | unwrap {f} | __error__="" [$__auto]))', n)
        for f, n in [("input_tokens", "input"), ("output_tokens", "output"),
                     ("cache_read_tokens", "cacheRead"), ("cache_creation_tokens", "cacheCreation")]],
       "short", 12, y, 12, 8, ds=LOKI, stack=True, bars=True, desc="Exact tokens per interval from api_request events."),
]
y += 8
panels += [
    bargauge("Cost by model", f"sort_desc(sum by (model) (max_over_time({COST}{M}[{RNG}])))", "currencyUSD", 0, y, legend="{{model}}"),
    bargauge("Cost by query source", f"sort_desc(sum by (query_source) (max_over_time({COST}{M}[{RNG}])))", "currencyUSD", 6, y,
             legend="{{query_source}}", desc="main = your prompts; other values are sub-agents and background queries."),
    bargauge("Cost by effort", f'sort_desc(label_replace(sum by (effort) (max_over_time({COST}{M}[{RNG}])), "effort", "not set", "effort", "^$"))', "currencyUSD", 12, y, legend="{{effort}}"),
    bargauge("Tokens by type", f"sort_desc(sum by (type) (max_over_time({TOK}{M}[{RNG}])))", "short", 18, y, legend="{{type}}"),
]
y += 7
panels += [
    table("Top sessions by cost", PROM,
          f'topk(10, sum by (session, user_email) (label_replace(max_over_time({COST}{M}[{RNG}]), "session", "$1", "session_id", "(.{{8}}).*")))',
          0, y, 12, 8, desc="Session = first 8 characters of the session id.",
          order=["session", "user_email", "Value"],
          rename={"session": "Session", "user_email": "User", "Value": "Cost"},
          overrides=[{"matcher": {"id": "byName", "options": "Cost"},
                      "properties": [{"id": "unit", "value": "currencyUSD"}, {"id": "decimals", "value": 4}]},
                     {"matcher": {"id": "byName", "options": "User"}, "properties": [{"id": "noValue", "value": "unknown"}]}]),
    ts("Cost per API request (avg)", [(lq("avg_over_time", "api_request", unwrap="cost_usd", by="model"), "{{model}}")], "currencyUSD", 12, y, 12, 8,
       ds=LOKI, desc="Average cost_usd per request, by model."),
]
y += 8
panels += [
    bargauge("Cost by subagent", f'sort_desc(sum by (agent_name) (max_over_time({COST}{{agent_name!="",model=~"$model"}}[{RNG}])))', "currencyUSD", 0, y, h=8,
             legend="{{agent_name}}", desc="Cost of subagent runs (Explore, general-purpose, custom agents). The main conversation is left out: it would dwarf them."),
    bargauge("Cost by MCP server", f'sort_desc(sum by (mcp_server_name) (max_over_time({COST}{{mcp_server_name!="",model=~"$model"}}[{RNG}])))', "currencyUSD", 6, y, h=8,
             legend="{{mcp_server_name}}", desc="Cost of the model turns that handled MCP tool calls, per MCP server. Built-in tools are left out."),
    bargauge("Tokens by MCP tool", f'topk(5, sum by (mcp_server_name, mcp_tool_name) (max_over_time({TOK}{{mcp_tool_name!="",model=~"$model"}}[{RNG}])))', "short", 12, y, h=8,
             legend="{{mcp_server_name}} / {{mcp_tool_name}}", desc="Tokens spent on turns that handled each MCP tool's result (top 5). Shows which MCP tools fill the context."),
    bargauge("Cost by version", f"sort_desc(sum by (service_version) (max_over_time({COST}{M}[{RNG}])))", "currencyUSD", 18, y, h=8,
             legend="{{service_version}}", desc="Cost per Claude Code version, so a release that changes your spend stands out."),
]
y += 8
panels += [
    bargauge("Cost by host", f"sort_desc(sum by (host_name) (max_over_time({COST}{M}[{RNG}])))", "currencyUSD", 0, y, w=12, h=7,
             legend="{{host_name}}", desc="Cost per machine. Needs OTEL_RESOURCE_ATTRIBUTES=host.name=<name> on each Claude Code install "
             "(claude/apply-claude-settings.py sets it); data from before that shows as one unnamed host."),
    bargauge("Requests by host", cnt("api_request", "host_name"), "short", 12, y, w=12, h=7, ds=LOKI,
             legend="{{host_name}}", desc="API requests per machine in the selected range."),
]
y += 7

# ============================================================================== Productivity
panels.append(row("Productivity", y)); y += 1
panels += [
    bargauge("Lines added vs removed", f"sum by (type) (max_over_time({LOC}{M}[{RNG}]))", "short", 0, y, h=7, legend="{{type}}",
             desc="Lines changed by Claude's edits in the selected range."),
    bargauge("Edit decisions", f"sum by (tool_name, decision) (max_over_time({EDIT}[{RNG}]))", "short", 6, y, h=7,
             legend="{{tool_name}} / {{decision}}", desc="Permission decisions for Edit, Write and NotebookEdit."),
    bargauge("Edits by language", f"sort_desc(sum by (language) (max_over_time({EDIT}[{RNG}])))", "short", 12, y, h=7, legend="{{language}}"),
    stat("Edit accept rate", f'sum(max_over_time({EDIT}{{decision="accept"}}[{RNG}])) / sum(max_over_time({EDIT}[{RNG}]))',
         "percentunit", 18, y, 6, 7, desc="Share of edit permission prompts accepted."),
]
y += 7
panels += [
    ts("Prompts over time", [(f'sum(count_over_time({ev("user_prompt")} [$__auto]))', "prompts")], "short", 0, y, 8, 8, ds=LOKI, bars=True,
       desc="How often you prompt (user_prompt events)."),
    ts("Activity share by type", [(f"sum by (type) (rate({ACT}[{RI}]))", "{{type}}")], "percentunit", 8, y, 8, 8, stack=True, bars=True,
       desc="Share of time spent active, summed over sessions (above 100% means several sessions at once). user = you typing/reading; cli = Claude working."),
    ts("Sessions started", [(f"sum by (start_type) ({SESS} unless {SESS} offset $__interval)", "{{start_type}}")], "short", 16, y, 8, 8,
       stack=True, bars=True, desc="Sessions that began in each interval (fresh / continue / resume)."),
]
y += 8

# ============================================================================== API performance
panels.append(row("API performance (from api_request logs)", y)); y += 1
panels += [
    ts("API latency p50 / p95", [
        (lq("", "api_request", unwrap="duration_ms", by="model", q=0.5), "p50 {{model}}"),
        (lq("", "api_request", unwrap="duration_ms", by="model", q=0.95), "p95 {{model}}"),
        (api_q("duration_ms", 0.95, BASE), "usual p95 (7 d)"),
        (f"1.5 * {api_q('duration_ms', 0.95, BASE)}", "1.5x usual p95")], "ms", 0, y, 8, 8, ds=LOKI,
       overrides=BASELINE_LINES,
       desc="Dashed lines: your own p95 over the last 7 days, and 1.5x that (where the Health tile turns orange)."),
    ts("Time to first token p50 / p95", [
        (lq("", "api_request", unwrap="ttft_ms", by="model", q=0.5), "p50 {{model}}"),
        (lq("", "api_request", unwrap="ttft_ms", by="model", q=0.95), "p95 {{model}}"),
        (api_q("ttft_ms", 0.95, BASE), "usual p95 (7 d)"),
        (f"1.5 * {api_q('ttft_ms', 0.95, BASE)}", "1.5x usual p95")], "ms", 8, y, 8, 8, ds=LOKI,
       overrides=BASELINE_LINES,
       desc="Dashed lines: your own p95 over the last 7 days, and 1.5x that (where the Health tile turns orange)."),
    ts("API requests", [(lq("count_over_time", "api_request", by="model"), "{{model}}")], "short", 16, y, 8, 8, ds=LOKI, stack=True, bars=True),
]
y += 8
panels += [
    ts("Output tokens per request (avg)", [(lq("avg_over_time", "api_request", unwrap="output_tokens", by="model"), "{{model}}")],
       "short", 0, y, 5, 8, ds=LOKI),
    ts("Cache read / request (avg)", [(lq("avg_over_time", "api_request", unwrap="cache_read_tokens", by="model"), "{{model}}")],
       "short", 5, y, 5, 8, ds=LOKI),
    count_table("Requests by effort and speed", cnt("api_request", "effort, speed"), [("effort", "Effort"), ("speed", "Speed")], 10, y, 8, 8,
                desc="Reasoning effort and speed setting (e.g. fast mode) per request."),
    ts("API errors by HTTP status", [(lq("count_over_time", "api_error", by="status_code, model"), "HTTP {{status_code}} {{model}}"),
                                     (lq("count_over_time", "api_retries_exhausted", by="model"), "retries exhausted {{model}}")],
       "short", 18, y, 6, 8, ds=LOKI, bars=True, stack=True,
       desc="api_error / api_retries_exhausted events. Empty while the API is healthy (verified: a bad model name produces an HTTP 404 here)."),
]
y += 8

# ============================================================================== Tools
panels.append(row("Tools, permissions and skills", y)); y += 1
panels += [
    ts("Tool calls by tool", [(lq("count_over_time", "tool_result", by="tool_name"), "{{tool_name}}")], "short", 0, y, 8, 8, ds=LOKI,
       stack=True, bars=True),
    ts("Tool duration p95 by tool", [(lq("", "tool_result", unwrap="duration_ms", by="tool_name", q=0.95), "{{tool_name}}")], "ms", 8, y, 8, 8, ds=LOKI),
    ts("Tool failures by tool and error",
       [(f'sum by (tool_name, error_type) (count_over_time({ev("tool_result", TOOL_FAILED)} [$__auto]))', "{{tool_name}}: {{error_type}}")],
       "short", 16, y, 8, 8, ds=LOKI, stack=True, bars=True, desc="tool_result events with success=false."),
]
y += 8
panels += [
    ts("Permission decisions", [(f'sum by (decision, source) (count_over_time({ev("tool_decision")} [$__auto]))', "{{decision}} ({{source}})")],
       "short", 0, y, 8, 8, ds=LOKI, stack=True, bars=True, desc="accept/reject and who decided (config, user, hook ...)."),
    count_table("Permission mode and auto-mode verdicts", cnt("tool_decision", "permission_mode, classifier_verdict"),
                [("permission_mode", "Mode"), ("classifier_verdict", "Auto-mode verdict")], 8, y, 8, 8,
                desc="Permission mode in force and the auto-mode classifier's verdict per tool call."),
    ts("Tool result size p95 by tool", [(lq("", "tool_result", unwrap="tool_result_size_bytes", by="tool_name", q=0.95), "{{tool_name}}")],
       "bytes", 16, y, 8, 8, ds=LOKI, desc="Large results fill the context window; watch for tools that return a lot."),
]
y += 8
panels += [
    table("MCP server connections", LOKI, cnt("mcp_server_connection", "status, transport_type, error_code"), 0, y, 10, 8,
          order=["status", "Value #A", "transport_type", "error_code"],
          rename={"status": "Status", "Value #A": "Count", "transport_type": "Transport", "error_code": "Error code"},
          desc="mcp_server_connection events in the range. 'failed' means a configured server did not start; the error code says why (e.g. an HTTP status).",
          overrides=[{"matcher": {"id": "byName", "options": "Status"},
                      "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                     {"id": "mappings", "value": [{"type": "value", "options": {
                                         "connected": {"color": "green", "index": 0}, "failed": {"color": "red", "index": 1},
                                         "disconnected": {"color": "orange", "index": 2}}}]}]},
                     {"matcher": {"id": "byName", "options": "Error code"}, "properties": [{"id": "noValue", "value": "-"}]}]),
    count_table("Skills activated", cnt("skill_activated", "skill_name, invocation_trigger"),
                [("skill_name", "Skill"), ("invocation_trigger", "Triggered by")], 10, y, 8, 8,
                desc="Skills loaded, and whether you or Claude triggered them."),
    count_table("Slash commands used", cnt("user_prompt", "command_name", ' | command_name!=""'),
                [("command_name", "Command")], 18, y, 6, 8,
                desc="Slash commands typed as prompts (/clear, /usage, custom commands ...)."),
]
y += 8

panels.append(row("Subagents", y)); y += 1
panels += [
    count_table("Subagent runs by type", cnt("subagent_completed", "agent_type, model"),
                [("agent_type", "Subagent"), ("model", "Model")], 0, y, 8, 7,
                desc="subagent_completed events: which subagents ran, and on which model."),
    ts("Subagent duration (avg)", [(lq("avg_over_time", "subagent_completed", unwrap="duration_ms", by="agent_type"), "{{agent_type}}")],
       "ms", 8, y, 8, 7, ds=LOKI, desc="Wall-clock time per subagent run."),
    ts("Subagent tokens per run (avg)", [(lq("avg_over_time", "subagent_completed", unwrap="total_tokens", by="agent_type"), "{{agent_type}}")],
       "short", 16, y, 8, 7, ds=LOKI, desc="Total tokens a subagent used (counted separately from the main conversation)."),
]
y += 7

# ============================================================================== Hooks and compaction
panels.append(row("Hooks and context compaction", y)); y += 1
HK = "hook_execution_complete"
panels += [
    stat("Hook runs", f'sum(count_over_time({ev(HK)} [{RNG}])) or vector(0)', "short", 0, y, 4, 4, ds=LOKI,
         desc="Hook executions in the range (one per hook event, covering all hooks that matched it)."),
    stat("Hook problems",
         f'(sum(sum_over_time({ev(HK)} | unwrap num_blocking | __error__="" [{RNG}])) or vector(0)) + '
         f'(sum(sum_over_time({ev(HK)} | unwrap num_non_blocking_error | __error__="" [{RNG}])) or vector(0))',
         "short", 0, y + 4, 4, 4, ds=LOKI, bg=True, steps=ANY,
         desc="Hook runs that blocked an action or errored (num_blocking + num_non_blocking_error). Zero is healthy; a hook that blocks on purpose will show up here too."),
    ts("Hook runs", [(f'sum by (hook_event) (count_over_time({ev(HK)} [$__auto]))', "{{hook_event}}")], "short", 4, y, 8, 8, ds=LOKI,
       stack=True, bars=True, desc="PreToolUse / PostToolUse / SessionStart / UserPromptSubmit ... hook executions."),
    ts("Hook duration p95 by hook", [(lq("", HK, unwrap="total_duration_ms", by="hook_name", q=0.95), "{{hook_name}}")], "ms", 12, y, 6, 8, ds=LOKI,
       desc="Total time a hook event took. Hooks run in the critical path: a slow one delays every tool call or prompt it is attached to."),
    count_table("Hooks by event", cnt(HK, "hook_name"), [("hook_name", "Hook")], 18, y, 6, 8,
                desc="Hook executions per hook event and matcher in the range."),
]
y += 8
CP = "compaction"
panels += [
    stat("Compactions", f'sum(count_over_time({ev(CP)} [{RNG}])) or vector(0)', "short", 0, y, 4, 4, ds=LOKI,
         desc="Times the conversation was compacted to free context (automatically when it filled up, or by /compact)."),
    stat("Context freed", f'1 - sum(sum_over_time({ev(CP)} | unwrap post_tokens | __error__="" [{RNG}])) / '
                          f'sum(sum_over_time({ev(CP)} | unwrap pre_tokens | __error__="" [{RNG}]))',
         "percentunit", 0, y + 4, 4, 4, ds=LOKI, nv="n/a", desc="Share of the context removed by compactions in the range."),
    ts("Context size before and after compaction",
       [(f'avg_over_time({ev(CP)} | unwrap pre_tokens | __error__="" [$__auto]) by (trigger)', "before ({{trigger}})"),
        (f'avg_over_time({ev(CP)} | unwrap post_tokens | __error__="" [$__auto]) by (trigger)', "after ({{trigger}})")],
       "short", 4, y, 7, 8, ds=LOKI, bars=True, desc="Tokens in context when a compaction ran, and what was left afterwards."),
    ts("Compaction duration", [(f'avg_over_time({ev(CP)} | unwrap duration_ms | __error__="" [$__auto]) by (trigger)', "{{trigger}}")], "ms", 11, y, 5, 8,
       ds=LOKI, bars=True, desc="How long a compaction took. Claude is busy summarising during this time."),
    count_table("Compactions by trigger", cnt(CP, "trigger, success"), [("trigger", "Trigger"), ("success", "OK")], 16, y, 8, 8,
                desc="auto = the context window filled up; manual = /compact. OK=false means the compaction failed."),
]
y += 8

# ============================================================================== Traces
panels.append(row("Traces (span metrics + Tempo)", y)); y += 1
SM = 'service="claude-code"'
panels += [
    ts("Span rate by type", [(f"sum by (span_name) (rate(traces_spanmetrics_calls_total{{{SM}}}[{RI}]))", "{{span_name}}")], "ops", 0, y, 8, 8,
       desc="interaction = one prompt; llm_request = one API call; tool / tool.execution / tool.blocked_on_user. "
            "The binary also defines mcp.rpc, hook, subagent.spawn, compaction and bash.subprocess spans, but they were not "
            "exported in testing; they would show up here if emitted."),
    ts("Span p95 latency by type", [(f"histogram_quantile(0.95, sum by (le, span_name) (rate(traces_spanmetrics_latency_bucket{{{SM}}}[{RI}])))", "{{span_name}}")],
       "s", 8, y, 8, 8),
    ts("Span errors", [(f'sum by (span_name) (rate(traces_spanmetrics_calls_total{{{SM}, status_code="STATUS_CODE_ERROR"}}[{RI}]))', "{{span_name}}")],
       "short", 16, y, 8, 8, bars=True, desc="Error spans per interval; empty is good."),
]
y += 8
panels.append(tempo_table(
    "Recent interactions (click a trace ID to open the waterfall)",
    '{ resource.service.name = "claude-code" && name = "claude_code.interaction" }', 0, y, 24, 8, "traces",
    hide=["traceService", "nested", "traceName", "Name"], order=["traceID", "startTime", "traceName", "traceDuration"],
    rename={"traceID": "Trace ID", "startTime": "Start", "traceName": "Root span", "traceDuration": "Duration"}))
y += 8
# traceIdHidden must stay: the Span ID data link needs it to open the trace (it is already hidden from view).
common_hide = ["traceService", "traceName", "service.name"]
panels.append(tempo_table(
    "Slow LLM requests (over 3 s, slowest first)",
    '{ resource.service.name = "claude-code" && name = "claude_code.llm_request" && duration > 3s } | select(span.model, span.ttft_ms, span.output_tokens)',
    0, y, 24, 7, "spans", hide=common_hide + ["name"], sort="duration",
    order=["duration", "model", "ttft_ms", "output_tokens", "time", "spanID"],
    rename={"duration": "Duration", "model": "Model", "ttft_ms": "Time to first token (ms)", "output_tokens": "Output tokens", "time": "Start", "spanID": "Span ID"},
    unit_overrides=[{"matcher": {"id": "byName", "options": "Duration"}, "properties": [{"id": "unit", "value": "ns"}]}]))
y += 7
# The execution span has no tool name; its parent claude_code.tool span does (its time also includes waiting for a permission answer).
panels.append(tempo_table(
    "Slow tool calls (over 1 s, slowest first)",
    '{ resource.service.name = "claude-code" && name = "claude_code.tool" && duration > 1s } | select(span.tool_name, span.bash_command_class)',
    0, y, 24, 7, "spans", hide=common_hide + ["name"], sort="duration",
    desc="Whole tool call, including any time spent waiting for a permission answer.",
    order=["duration", "tool_name", "bash_command_class", "time", "spanID"],
    rename={"duration": "Duration", "tool_name": "Tool", "bash_command_class": "Command type", "time": "Start", "spanID": "Span ID"},
    unit_overrides=[{"matcher": {"id": "byName", "options": "Duration"}, "properties": [{"id": "unit", "value": "ns"}]}]))
y += 8

# ============================================================================== Events / logs
panels.append(row("Events, plugins and logs", y)); y += 1
panels += [
    ts("Events by type", [(f'sum by (event_name) (count_over_time({L} | event_name!="plugin_loaded" [$__auto]))', "{{event_name}}")],
       "short", 0, y, 12, 8, ds=LOKI, stack=True, bars=True, desc="plugin_loaded is excluded; it fires many times at every startup."),
    table("Plugins loaded", LOKI, cnt("plugin_loaded", "plugin_name, plugin_scope, marketplace_name"), 12, y, 12, 8,
          order=["plugin_name", "plugin_scope", "marketplace_name", "Value #A"],
          rename={"plugin_name": "Plugin", "plugin_scope": "Scope", "marketplace_name": "Marketplace", "Value #A": "Loads"},
          desc="Plugins Claude Code loaded in the range (one load per session start).",
          overrides=[{"matcher": {"id": "byName", "options": "Plugin"}, "properties": [{"id": "custom.width", "value": 300}]},
                     {"matcher": {"id": "byName", "options": "Loads"}, "properties": [{"id": "custom.width", "value": 90}]}]),
]
y += 8
# One line per event: the name first, then whichever of these fields the event carries.
STREAM_LINE = ("{{ .event_name }}"
               + "".join("{{ with .%s }}  %s{{ end }}" % (f, fmt) for f, fmt in (
                   ("tool_name", "{{ . }}"), ("hook_name", "{{ . }}"), ("command_name", "/{{ . }}"), ("agent_type", "{{ . }}"),
                   ("model", '{{ regexReplaceAll "-[0-9]{8}$" (regexReplaceAll "^claude-" . "") "" }}'), ("decision", "{{ . }}"), ("status", "{{ . }}"), ("trigger", "{{ . }}"),
                   ("duration_ms", "{{ . }} ms"), ("total_duration_ms", "{{ . }} ms")))
               + '{{ if eq .success "false" }}  FAILED{{ end }}')
logs = base("logs", "Event stream (prompt text is redacted by default)", 0, y, 24, 12, LOKI,
            [{"refId": "A", "datasource": LOKI, "queryType": "range", "expr": f'{L} | event_name!="plugin_loaded" | line_format `{STREAM_LINE}`'}])
logs["options"] = {"showTime": True, "wrapLogMessage": True, "prettifyLogMessage": False, "enableLogDetails": True,
                   "sortOrder": "Descending", "dedupStrategy": "none"}
panels.append(logs)

# ---- display names -------------------------------------------------------------------------------------
# Grafana's "Rename by regex" transformation rewrites series names (legends, bar gauge labels) without touching the query,
# and value mappings do the same for table cells. The regex must be written /.../flags, otherwise Grafana anchors it.
def rn(*pairs):
    return [{"id": "renameByRegex", "options": {"regex": r, "renamePattern": to}} for r, to in pairs]


# claude-haiku-4-5-20251001 -> haiku-4-5, claude-opus-5 -> opus-5 (no vendor prefix, no snapshot date)
MODEL_RE = r"claude-([a-z]+(?:-\d+)*?)(?:-\d{8})?"
MODEL_RN = rn((f"/{MODEL_RE}(?=\\s|$|,|\\))/g", "$1"))
RENAMES = {
    "Tokens per minute by type": rn(("/^input$/", "Input"), ("/^output$/", "Output"), ("/^cacheRead$/", "Cache read"),
                                    ("/^cacheCreation$/", "Cache creation")),
    "Tokens by type": rn(("/^input$/", "Input"), ("/^output$/", "Output"), ("/^cacheRead$/", "Cache read"),
                         ("/^cacheCreation$/", "Cache creation")),
    "Activity share by type": rn(("/^cli$/", "Claude working"), ("/^user$/", "You")),
    "Limit usage over time": rn(("/^session/", "5-hour limit"), ("/^weekly_all/", "Weekly limit (all models)"),
                                ("/^weekly_scoped /", "Weekly limit: ")),
    "Permission decisions per minute": rn(("/\\(config\\)/", "(settings rule)"), ("/\\(user_temporary\\)/", "(you, this time)"),
                                          ("/\\(user_permanent\\)/", "(you, always)")),
    "Span rate by type": rn(("/claude_code\\.(.*)/", "$1"), ("/_/g", " "), ("/\\./g", " > ")),
    "Span p95 latency by type": rn(("/claude_code\\.(.*)/", "$1"), ("/_/g", " "), ("/\\./g", " > ")),
    "Span errors per hour": rn(("/claude_code\\.(.*)/", "$1"), ("/_/g", " "), ("/\\./g", " > ")),
    "Events per minute by type": rn(("/_/g", " ")),
}
MODEL_MAP = {"type": "regex", "options": {"pattern": f"^{MODEL_RE}$", "result": {"text": "$1"}}}
CELL_MAPS = {   # table title -> column header -> value mappings
    "Subagent runs by type": {"Model": [MODEL_MAP]},
    "Slow LLM requests (over 3 s, slowest first)": {"Model": [MODEL_MAP]},
    "Slow tool calls (over 1 s, slowest first)": {"Tool": [
        {"type": "regex", "options": {"pattern": "^mcp__(.+?)__(.+)$", "result": {"text": "$2 ($1)"}}}]},
    "Plugins loaded": {"Plugin": [{"type": "regex", "options": {"pattern": "^cc-plugin-(.*)$", "result": {"text": "$1"}}}]},
    "Plan": {
        "Plan": [{"type": "value", "options": {k: {"text": v, "index": i} for i, (k, v) in enumerate(
            (("max", "Max"), ("pro", "Pro"), ("team", "Team"), ("enterprise", "Enterprise"), ("free", "Free")))}}],
        "Rate-limit tier": [{"type": "value", "options": {k: {"text": v, "index": i} for i, (k, v) in enumerate(
            (("default_claude_max_20x", "Max 20x"), ("default_claude_max_5x", "Max 5x"), ("default_claude_pro", "Pro")))}},
                            {"type": "regex", "options": {"pattern": "^default_claude_(.*)$", "result": {"text": "$1"}}}]},
}
EMPTY_IS_GOOD = {   # a chart that is empty because nothing went wrong should say so
    "API errors per hour": "No API errors in this range",
    "Tool failures per hour": "No tool failures in this range",
    "Span errors per hour": "No span errors in this range",
}

MODEL_COLORS = ((".*opus.*", "purple"), (".*sonnet.*", "light-blue"), (".*sonnet-5-5.*", "blue"),
                (".*haiku.*", "green"), (".*haiku-5-5.*", "light-green"))
GOOD_HIGH = lambda warn, ok: [{"color": "red", "value": None}, {"color": "orange", "value": warn}, {"color": "green", "value": ok}]
GOOD_LOW = lambda warn, bad: [{"color": "green", "value": None}, {"color": "orange", "value": warn}, {"color": "red", "value": bad}]
STAT_STEPS = {
    "Cache hit ratio": GOOD_HIGH(0.75, 0.9),
    "Tool success rate": GOOD_HIGH(0.85, 0.95),
    "Edit accept rate": GOOD_HIGH(0.5, 0.8),
    "5-hour limit used": GOOD_LOW(0.7, 0.9),
    "Weekly limit used": GOOD_LOW(0.7, 0.9),
}
TEXT_GOOD_BAD = lambda mapping: [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                 {"id": "color", "value": {"mode": "fixed", "fixedColor": "text"}},   # unmapped values stay neutral
                                 {"id": "mappings", "value": [{"type": "value", "options": {
                                     k: {"color": c, "index": i} for i, (k, c) in enumerate(mapping)}}]}]
CELL_STYLES = {
    "Permission mode and auto-mode verdicts": {"Auto-mode verdict": TEXT_GOOD_BAD((("allow", "green"), ("block", "red")))},
    "Compactions by trigger": {"OK": TEXT_GOOD_BAD((("true", "green"), ("false", "red")))},
    "Top sessions by cost": {"Cost": [{"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic"}},
                                      {"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}, {"id": "min", "value": 0}]},
}

for pnl in panels:
    title = pnl.get("title", "")
    if pnl["type"] in ("timeseries", "bargauge"):
        pnl["transformations"] = MODEL_RN + RENAMES.get(title, [])
    for col, maps in CELL_MAPS.get(title, {}).items():
        pnl["fieldConfig"]["overrides"].append({"matcher": {"id": "byName", "options": col},
                                                "properties": [{"id": "mappings", "value": maps}]})
    if title in EMPTY_IS_GOOD:
        pnl["fieldConfig"]["defaults"]["noValue"] = EMPTY_IS_GOOD[title]
    if title == "Cost per hour by model":
        pnl["fieldConfig"]["defaults"]["decimals"] = 2   # axis ticks: $300.00, not $300.0000

    # same model, same colour on every chart (later overrides win, so the specific versions come last)
    if pnl["type"] in ("timeseries", "bargauge") and any(
            "{{model}}" in t.get("legendFormat", "") or "by (model" in t.get("expr", "") for t in pnl["targets"]):
        pnl["fieldConfig"]["overrides"] += [
            {"matcher": {"id": "byRegexp", "options": rx}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
            for rx, c in MODEL_COLORS]
        if pnl["type"] == "timeseries" and any("p95" in t.get("legendFormat", "") for t in pnl["targets"]):
            # p50 and p95 of one model share a colour, so p95 is drawn dashed
            pnl["fieldConfig"]["overrides"].append(
                {"matcher": {"id": "byRegexp", "options": "^p95 .*"},
                 "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}}]})

    # tiles that mean good or bad get a colour; the number itself stays neutral until it crosses a line
    if title in STAT_STEPS:
        pnl["fieldConfig"]["defaults"]["color"] = {"mode": "thresholds"}
        pnl["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": STAT_STEPS[title]}
        pnl["options"]["colorMode"] = "value"

    for col, props in CELL_STYLES.get(title, {}).items():
        pnl["fieldConfig"]["overrides"].append({"matcher": {"id": "byName", "options": col}, "properties": props})

def scope_to_host(expr, loki):
    """Add the $host filter to a query. 'All' is ".*", which also matches data that has no host_name."""
    if loki:
        return expr.replace(L, L + ' | host_name=~"$host"')
    expr = re.sub(r'\b(claude_code_\w+)\{', r'\1{host_name=~"$host",', expr)
    return re.sub(r'\b(claude_code_\w+)(?![{\w])', r'\1{host_name=~"$host"}', expr)


for pnl in panels:
    for t in pnl.get("targets", []):
        if "expr" in t and "host_name=~" not in t["expr"]:
            t["expr"] = scope_to_host(t["expr"], t["datasource"] == LOKI)

dashboard = {
    "uid": "claude-code",
    "title": "Claude Code",
    "description": "Cost, tokens, productivity, API/tool performance, traces and events for Claude Code sessions, per host.",
    "tags": ["claude-code", "otel"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "editable": True,
    "graphTooltip": 1,
    "refresh": "30s",
    "time": {"from": "now-3h", "to": "now"},
    "templating": {"list": [{
        "name": "model", "label": "Model", "type": "query", "datasource": PROM,
        "query": {"query": f"label_values({TOK}, model)", "refId": "m"},
        "definition": f"label_values({TOK}, model)",
        "refresh": 2, "includeAll": True,
        "regex": "/^(?<value>claude-(?<text>[a-z]+(?:-\\d+)*?)(?:-\\d{8})?)$/", "multi": True, "allValue": ".*",
        "current": {"selected": True, "text": "All", "value": "$__all"}, "sort": 1},
        {"name": "host", "label": "Host", "type": "query", "datasource": PROM,
         "query": {"query": f"label_values({SESS}, host_name)", "refId": "h"},
         "definition": f"label_values({SESS}, host_name)",
         "refresh": 2, "includeAll": True, "multi": True, "allValue": ".*",
         "current": {"selected": True, "text": "All", "value": "$__all"}, "sort": 1}]},
    "annotations": {"list": [{
        "name": "Prompts", "enable": False, "iconColor": "blue", "datasource": LOKI,
        "expr": f'{L} | event_name="user_prompt"', "titleFormat": "Prompt", "textFormat": "{{prompt_length}} chars",
        "useValueForTime": False}]},
    "panels": panels,
}

if __name__ == "__main__":
    print(json.dumps(dashboard, indent=2))
