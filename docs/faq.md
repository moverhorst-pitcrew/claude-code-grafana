# FAQ

## Why not just use `/usage` or `/cost` in Claude Code?

Those show the current session or the current limits. This keeps history: cost per model, effort, subagent and MCP
server over days, how fast the API and your tools were, which hooks are slow, and trace waterfalls. It also combines
several machines or containers in one place.

## Does anything leave my machine?

No, except the image download and, only if you enable the poller, one request to `api.anthropic.com` for your usage.
Grafana listens on `127.0.0.1`. Prompt and response text are redacted by Claude Code. See *Privacy and security* in the
README.

## Can I watch several machines or containers in one dashboard?

Yes. Point each Claude Code at the same OTLP endpoint and give each a name with
`OTEL_RESOURCE_ATTRIBUTES=host.name=<name>` (`claude-settings/apply-claude-settings.py` does this, or set it by hand in a
container's `settings.json`). Then use the *Host* variable and the "by host" panels. Sessions that started before the
setting have no host and show only under *All*.

## Can I use my own Grafana?

Yes. Import `dashboards/claude-code.json` (also attached to every release) under *Dashboards > New > Import*. It needs a
Prometheus, a Loki and a Tempo datasource that receive Claude Code's OTLP data, for example through the
OpenTelemetry Collector. The dashboard picks the first datasource of each type; change it under *Dashboard settings >
Variables* (`ds_prometheus`, `ds_loki`, `ds_tempo`).

## A panel is empty or shows "none in this range"

Usually that is true: nothing of that kind happened in the range (no API errors is good). If a panel should have data,
start a **new** Claude Code session (settings are read at startup), widen the time range, and check
[`telemetry-reference.md`](telemetry-reference.md) against what your Prometheus and Loki contain.

## The subscription limit tiles say "stale" or the poller reports `http-429`

The usage endpoint rate-limits and answers 429 with `Retry-After: 0`. The poller backs off 2, 4, 8, then 10 minutes and
recovers by itself. `token-expired` means your Claude login token ran out; use Claude Code once and it is refreshed.

## Does the poller work on macOS or Windows?

It reads Claude Code's `.credentials.json` from your Claude config directory, so it works wherever Claude Code stores
the login in that file (Linux and Windows). On macOS Claude Code keeps the login in the Keychain, which the poller does
not read, so the subscription tiles stay empty there. Everything else works.

## How do I reset or wipe the data?

Stop the stack and delete its data: `~/.local/share/otel-lgtm` for the systemd install, or
`docker compose down -v` for the compose file. Then start it again.

## How do I change the Grafana password?

Log in with `admin` / `admin` and Grafana asks you to change it on first login.

## Which versions are tested?

See *Compatibility* in the README. Claude Code can rename metrics or events between versions; reports from other
versions are welcome.
