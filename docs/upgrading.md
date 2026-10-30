# Upgrading

The dashboard is a generated file and Grafana reloads it within about 30 seconds, so most upgrades are just
`git pull`. Restart the Grafana container when the notes below say so, because provisioning settings and the image tag
are only read at startup.

```bash
git pull
systemctl --user restart otel-lgtm      # Podman + systemd install
docker compose pull && docker compose up -d   # compose install
```

Edits you made to the dashboard in the Grafana UI are overwritten by the file. Copy them into
`tools/gen_dashboard.py` (see *Customising the dashboard*) or save the dashboard under another name first.

## Notes by version

### 1.1.0
- The Grafana image is pinned (`grafana/otel-lgtm` 0.39.0) instead of `latest`. Re-run `./install.sh` once so the
  Quadlet unit picks up the pinned tag.
- The dashboard uses datasource variables (`ds_prometheus`, `ds_loki`, `ds_tempo`) instead of fixed datasource ids.
  Nothing to do on the bundled stack.
- New `docker-compose.yml` for Docker users, unit tests, and CI.
- The usage poller backs off 2, 4, 8, then 10 minutes after a rate limit (it used 1, 2, 4, 8 before).

### 1.0.1
- The dashboard is provisioned into a **Claude Code** folder. Restart the Grafana container once. If the dashboard stays
  at the top level, change the dashboard file (any edit) so Grafana re-saves it.

### 1.0.0
- First tagged release. Adds the *Host* variable and the "by host" panels. Run `claude/apply-claude-settings.py` on each
  machine (or set `OTEL_RESOURCE_ATTRIBUTES=host.name=<name>`) to name it.
