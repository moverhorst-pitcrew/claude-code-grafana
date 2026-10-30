# Security policy

## Scope

This project runs a local Grafana stack bound to `127.0.0.1` and, optionally, a poller that reads your Claude Code
login to call `api.anthropic.com`. The things worth reporting are:

- anything that makes the stack reachable from outside the machine
- the poller printing, logging, storing or sending an access token anywhere except `api.anthropic.com`
- the installer or settings script changing files outside what the README documents
- telemetry content (prompts, responses, account identifiers) being exported when it should not be

Grafana, Loki, Tempo, Prometheus and the OpenTelemetry collector come from
[`grafana/otel-lgtm`](https://github.com/grafana/docker-otel-lgtm); report problems in those upstream.

## Reporting

Please do not open a public issue. Use GitHub's private
[security advisory form](https://github.com/moverhorst-pitcrew/claude-code-grafana/security/advisories/new).
You should get a first reply within a week.

## Supported versions

Only the latest release and `main`.
