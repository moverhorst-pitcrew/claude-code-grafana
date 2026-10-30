#!/usr/bin/env python3
"""Poll Claude subscription usage (the data behind /usage) and push it to the local OTLP endpoint as gauges.

* Reads the Claude Code login from <CLAUDE_CONFIG_DIR or ~/.claude>/.credentials.json (Linux/file storage only).
* The access token is used only for the request to api.anthropic.com. It is never printed, logged or stored.
* It never refreshes the token (that would rotate it under Claude Code); if the token has expired it reports
  claude_usage_poll_ok=0 and tries again next run. Claude Code refreshes the token whenever it is used.
* Endpoint /api/oauth/usage is undocumented and may change; unknown fields are ignored.

  poll_usage.py            fetch and push once
  poll_usage.py --dry-run  fetch and print the metrics instead of pushing them
"""
import json, os, sys, time, urllib.error, urllib.request
from datetime import datetime

OTLP = os.environ.get("CLAUDE_USAGE_OTLP_URL", "http://127.0.0.1:4318/v1/metrics")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
WINDOW = {"session": 18000, "weekly": 604800}       # 5 hours / 7 days, from the Claude Code client
now = time.time()


def epoch(iso):
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return None


STATE = os.path.join(os.environ.get("STATE_DIRECTORY") or os.path.expanduser("~/.local/state/claude-usage-poller"), "retry_after_until")


def backoff_active():
    """The usage endpoint rate-limits. After a 429 we stay quiet until its Retry-After has passed."""
    try:
        return float(open(STATE).read()) > now
    except Exception:
        return False


def set_backoff(seconds):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        open(STATE, "w").write(str(now + seconds))
    except OSError:
        pass


def creds():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    for attempt in range(3):
        try:
            with open(os.path.join(base, ".credentials.json")) as f:
                return json.load(f).get("claudeAiOauth") or {}
        except json.JSONDecodeError:   # Claude Code may be rewriting the file right now
            if attempt == 2:
                raise
            time.sleep(0.5)


class Out:
    """Collects OTLP gauges."""
    def __init__(self):
        self.m = {}

    def g(self, name, unit, value, **attrs):
        if value is None:
            return
        self.m.setdefault((name, unit), []).append((attrs, float(value)))

    def payload(self):
        t = str(int(now * 1e9))
        metrics = []
        for (name, unit), pts in self.m.items():
            metrics.append({"name": name, "unit": unit, "gauge": {"dataPoints": [
                {"timeUnixNano": t, "asDouble": v,
                 "attributes": [{"key": k, "value": {"stringValue": str(a)}} for k, a in attrs.items()]}
                for attrs, v in pts]}})
        return {"resourceMetrics": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "claude-usage"}}]},
            "scopeMetrics": [{"scope": {"name": "claude-usage-poller"}, "metrics": metrics}]}]}


def failure(out, reason):
    out.g("claude_usage_poll_ok", "", 0)
    out.g("claude_usage_poll_failure_info", "", 1, reason=reason)
    print("usage poll failed:", reason, file=sys.stderr)


def collect(out, usage, meta):
    out.g("claude_usage_poll_ok", "", 1)
    out.g("claude_usage_last_poll_timestamp", "s", now)
    out.g("claude_usage_last_success_timestamp", "s", now)
    out.g("claude_usage_plan_info", "", 1, plan=meta.get("subscriptionType") or "", tier=meta.get("rateLimitTier") or "")

    limits = usage.get("limits")
    if not isinstance(limits, list):   # older shape
        limits = [{"kind": k, "percent": (usage.get(k) or {}).get("utilization"), "resets_at": (usage.get(k) or {}).get("resets_at"),
                   "scope": None, "is_active": True}
                  for k in ("five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet") if isinstance(usage.get(k), dict)]
    for l in limits:
        kind = {"five_hour": "session", "seven_day": "weekly_all"}.get(l.get("kind"), l.get("kind") or "unknown")
        scope = ((l.get("scope") or {}).get("model") or {}).get("display_name") or ((l.get("scope") or {}).get("surface")) or ""
        a = {"limit": kind, "scope": scope}
        if l.get("percent") is not None:
            out.g("claude_usage_utilization", "1", l["percent"] / 100, **a)
        r = epoch(l.get("resets_at")) if l.get("resets_at") else None
        out.g("claude_usage_reset_timestamp", "s", r, **a)
        out.g("claude_usage_window", "s", WINDOW["session" if kind == "session" else "weekly"], **a)
        out.g("claude_usage_limit_active", "", 1 if l.get("is_active") else 0, **a)

    bd = (usage.get("seven_day_breakdown") or {}).get("rows") or []
    for row in bd:
        if row.get("percent") is not None:
            out.g("claude_usage_weekly_share", "1", row["percent"] / 100, source=row.get("display_name") or row.get("key") or "")

    spend = usage.get("spend") or {}
    used = (spend.get("used") or {})
    if used.get("amount_minor") is not None:
        cur = used.get("currency") or ""
        out.g("claude_usage_credits_used", "", used["amount_minor"] / (10 ** (used.get("exponent") or 0)), currency=cur)
        out.g("claude_usage_credits_enabled", "", 1 if spend.get("enabled") else 0, currency=cur, reason=spend.get("disabled_reason") or "")

    # Dollar allowances the API reports under internal names (their meaning is not documented); exported by raw name.
    for k, v in usage.items():
        if k in ("five_hour", "seven_day") or not isinstance(v, dict) or v.get("limit_dollars") is None:
            continue
        out.g("claude_usage_allowance_limit", "", v["limit_dollars"], allowance=k)
        out.g("claude_usage_allowance_used", "", v.get("used_dollars"), allowance=k)
        if v.get("utilization") is not None:
            out.g("claude_usage_allowance_utilization", "1", v["utilization"] / 100, allowance=k)
        out.g("claude_usage_allowance_reset_timestamp", "s", epoch(v["resets_at"]) if v.get("resets_at") else None, allowance=k)


def main():
    out = Out()
    if "--dry-run" not in sys.argv and backoff_active():
        print("backing off after a rate limit; skipping this run", file=sys.stderr)
        return
    try:
        meta = creds()
        tok = meta["accessToken"]
    except Exception as e:
        failure(out, "no-credentials")
        tok = None
    if tok:
        exp = meta.get("expiresAt")
        if exp and exp / 1000 < now + 20:
            failure(out, "token-expired")
        else:
            req = urllib.request.Request(USAGE_URL, headers={
                "Authorization": "Bearer " + tok, "anthropic-beta": "oauth-2025-04-20",
                "Content-Type": "application/json", "User-Agent": "claude-cli/usage-poller"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    usage = json.load(r)
            except urllib.error.HTTPError as e:
                failure(out, f"http-{e.code}")
                if e.code == 429:
                    try:
                        wait = int(e.headers.get("Retry-After", ""))
                    except ValueError:
                        wait = 900
                    set_backoff(min(max(wait, 60), 3600))
                    print(f"rate limited; backing off {min(max(wait, 60), 3600)} s (Retry-After: {e.headers.get('Retry-After')})", file=sys.stderr)
                usage = None
            except Exception as e:
                failure(out, type(e).__name__)
                usage = None
            if usage is not None:
                try:
                    collect(out, usage, meta)
                except Exception as e:   # response shape changed: report it instead of pushing partial data
                    out.m.clear()
                    failure(out, "unexpected-response-" + type(e).__name__)
    body = json.dumps(out.payload())
    if "--dry-run" in sys.argv:
        for (name, unit), pts in sorted(out.m.items()):
            for a, v in pts:
                print(f"{name}[{unit}] {a} = {v}")
        return
    req = urllib.request.Request(OTLP, data=body.encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as e:
        print(f"push to {OTLP} failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
