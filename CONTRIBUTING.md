# Contributing

Issues and pull requests are welcome.

## Changing the dashboard

`dashboards/claude-code.json` is generated. Edit [`tools/gen_dashboard.py`](tools/gen_dashboard.py), regenerate and
validate against a running stack:

```bash
python3 tools/gen_dashboard.py > dashboards/claude-code.json
python3 tools/validate_dashboard.py --range 24h   # also try 15m, 3h and 7d
```

Grafana reloads the file within about 30 seconds. Edits made in the Grafana UI are overwritten, so do not rely on them.

## Pull requests

- Keep one change per pull request and explain what it fixes or adds.
- Commit the regenerated `dashboards/claude-code.json` together with the generator change.
- If a panel changes visibly, refresh the matching screenshot in `docs/screenshots/` and make sure it shows no account details.
- Do not add credentials, tokens or real account identifiers anywhere.

## Reporting problems

Use the issue templates. For security problems see [SECURITY.md](SECURITY.md).
