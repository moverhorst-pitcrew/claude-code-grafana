# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/). Upgrade notes are in [docs/upgrading.md](docs/upgrading.md).

## [Unreleased]

### Added
- `tools/send_sample_telemetry.py` and a CI job that start the stack with `docker compose up -d`, send it, and run
  `tools/validate_dashboard.py` against the result, so a query that errors against a live Grafana fails CI.
- `examples/alerting/`: Grafana alert rule examples for weekly usage above 80%, 5-hour usage
  above 90%, a stale usage poller, and daily cost per host above a threshold.
- `tools/validate_dashboard.py --require-data`: a query that succeeds but returns no rows counts as a failure,
  checked per target, so a renamed or dropped metric fails CI instead of leaving a panel empty. The CI smoke job
  uses it; panels that are empty on the sample telemetry are listed in the script's `ALLOW_EMPTY` constant.
- `tools/wait_for_telemetry.py`: the smoke job waits until the sample telemetry can actually be queried
  (a searchable trace in Tempo, two samples of the span metrics and of the counters) instead of sleeping
  for a fixed ten seconds, and fails with what is still missing. `tools/send_sample_telemetry.py` sends two
  samples per cumulative counter, so the dashboard's `rate()` panels have something to show.
- Cost by skill and cost by terminal type (interactive vs headless) bar gauge panels.
- `tests/test_versions.py`: fails CI when the `grafana/otel-lgtm` version drifts between
  `docker-compose.yml`, the Quadlet unit and the README Compatibility line. A `dashboard-smoke`
  CI step also checks that the Quadlet's pinned `@sha256` digest still matches its tag.
- "Internal errors" count tile in the Health row, next to API errors and failed MCP servers,
  from the `internal_error` event (documented but previously unplotted).
- `tools/doctor.py`: one script to run when the dashboard is empty. It checks Claude Code's settings, the
  collector's OTLP ports, Prometheus, Loki, the usage poller and Grafana's datasources, prints the likely fix
  per step and exits non-zero on a real failure. A disabled usage poller warns instead of failing. The
  `dashboard-smoke` CI job runs it against the live stack.

### Fixed
- Requests by host and Cost by host show data without a `host_name` (from before `host.name` was set) as the host
  "unnamed".
- Requests by host shows a bar per host. Grafana returns a Loki instant query as one table, and the bar gauge reduced it
  to its last row, so it showed a single bar named "Value #A" (the last host's count).
- `tools/validate_dashboard.py` now exits non-zero when a query fails; it used to print `FAILED: N` but still exit 0.
- Credits used no longer hardcodes a euro sign: it sums per currency as reported by the account
  and shows the currency as the tile's name (an account that reports no currency renders as
  "unknown").

## [1.1.0] - 2026-10-30

### Added
- `docker-compose.yml` for Docker users (macOS, Windows, Linux), with the usage poller as an opt-in profile.
- Unit tests (standard library only) and CI: tests on Python 3.11 and 3.15, ruff, yamllint, shellcheck, and a check
  that `dashboards/claude-code.json` matches the generator.
- The dashboard JSON is attached to every GitHub release for import into any Grafana.
- Documentation: telemetry reference, FAQ and upgrade notes in `docs/`; `SECURITY.md`, `CONTRIBUTING.md`, code of
  conduct, issue and pull request templates, Dependabot, CODEOWNERS.

### Changed
- The dashboard uses datasource variables (`ds_prometheus`, `ds_loki`, `ds_tempo`) instead of fixed ids, so it imports
  into any Grafana.
- `grafana/otel-lgtm` is pinned to 0.39.0 (Grafana 13.3) instead of `latest`.
- The compose poller runs on `python:3.15-alpine`; CI tests on Python 3.11 and 3.15 and uses `actions/checkout` and `actions/setup-python` v7.

### Fixed
- The usage poller backs off 2, 4, 8, then 10 minutes after a rate limit, as documented (it started at 1 minute), and
  closes its state file handles.
- `tools/gen_dashboard.py` runs on Python before 3.12 (it used backslashes inside f-string expressions).

## [1.0.1] - 2026-10-30

### Changed
- The dashboard is provisioned into a **Claude Code** folder, next to the alert rules, instead of the top level.
- The dashboard description mentions multiple hosts.

## [1.0.0] - 2026-10-30

First tagged release: the Claude Code dashboard (health, cost, tokens, productivity, tools, MCP, subagents, hooks,
compaction, traces), optional subscription-limit tracking, a *Host* variable with "by host" panels, and a Podman +
systemd installer.

[1.1.0]: https://github.com/moverhorst-pitcrew/claude-code-grafana/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/moverhorst-pitcrew/claude-code-grafana/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/moverhorst-pitcrew/claude-code-grafana/releases/tag/v1.0.0
