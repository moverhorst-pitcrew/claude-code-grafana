# Alert rule examples

Grafana alert rules in [file provisioning format](https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/file-provisioning/) for four common cases:

| File | Fires when |
|---|---|
| [`weekly-usage-high.yaml`](weekly-usage-high.yaml) | Weekly subscription usage is above 80% |
| [`session-usage-high.yaml`](session-usage-high.yaml) | The current 5-hour usage window is above 90% |
| [`poller-stale.yaml`](poller-stale.yaml) | The usage poller has not reported in 10 minutes |
| [`daily-cost-per-host.yaml`](daily-cost-per-host.yaml) | A host's cost in the trailing 24 hours is above a threshold |

The two usage rules need the usage poller (`--usage-poller`); see "Subscription limits" in the
main README.

## Using them

Each file is self-contained and provisions one alert rule into a `Claude Code` folder, next to
the dashboard. Copy the ones you want into Grafana's alerting provisioning path (by default
`/etc/grafana/provisioning/alerting/`, or the matching path under `~/.local/share/otel-lgtm` for
this project's install) and restart or let Grafana reload it.

Before copying a file, open it and adjust:

- `datasourceUid`: the uid of your Prometheus datasource. Find it under *Connections > Data
  sources*, or in `dashboards/claude-code.json`'s *Dashboard settings > JSON Model* once imported.
- The threshold in the rule's `C` query.

These files deliberately have no contact point or notification policy, only a `severity` label:
wire that up yourself under *Alerting > Contact points* and *Notification policies*, or add a
matching label here and route on it.
