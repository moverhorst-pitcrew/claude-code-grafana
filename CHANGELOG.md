# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/). Upgrade notes are in [docs/upgrading.md](docs/upgrading.md).

## [Unreleased]

### Added
- `tools/send_sample_telemetry.py` and a CI job that start the stack with `docker compose up -d`, send it, and run
  `tools/validate_dashboard.py` against the result, so a query that errors against a live Grafana fails CI.
- `examples/alerting/`: Grafana alert rule examples for weekly usage above 80%, 5-hour usage
  above 90%, a stale usage poller, and daily cost per host above a threshold.

### Fixed
- `tools/validate_dashboard.py` now exits non-zero when a query fails; it used to print `FAILED: N` but still exit 0.

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
