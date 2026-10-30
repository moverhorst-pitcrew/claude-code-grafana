#!/usr/bin/env bash
# Local Grafana dashboard for Claude Code telemetry, running in rootless Podman under systemd.
# Run ./install.sh --help for the options.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
quadlet_dir="$HOME/.config/containers/systemd"
unit_dir="$HOME/.config/systemd/user"
data_dir="$HOME/.local/share/otel-lgtm"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Installs a Grafana stack (Grafana, Tempo, Loki, Prometheus) as a rootless Podman container managed by systemd,
with the "Claude Code" dashboard. Everything listens on localhost only.

Options:
  --claude-settings   Add the OpenTelemetry variables to Claude Code's settings.json (a backup is made first)
  --usage-poller      Also install the subscription usage poller. It reads your Claude login token and calls an
                      undocumented endpoint, so it is off unless you ask for it (see README, "Subscription limits")
  --no-boot           Do not enable lingering (the stack then starts when you log in instead of at boot)
  --uninstall         Stop and remove the services (stored data is kept in ~/.local/share/otel-lgtm)
  -h, --help          Show this help

Options can be combined, and the script can be re-run safely.
EOF
}

claude_settings=0; usage_poller=0; boot=1; uninstall=0
for arg in "$@"; do
  case "$arg" in
    --claude-settings) claude_settings=1 ;;
    --usage-poller) usage_poller=1 ;;
    --no-boot) boot=0 ;;
    --uninstall) uninstall=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

if (( uninstall )); then
  systemctl --user disable --now claude-usage-poller.timer 2>/dev/null || true
  rm -f "$unit_dir/claude-usage-poller.service" "$unit_dir/claude-usage-poller.timer"
  systemctl --user stop otel-lgtm.service 2>/dev/null || true
  rm -f "$quadlet_dir/otel-lgtm.container"
  systemctl --user daemon-reload
  echo "Removed the services. Data is kept in $data_dir (delete it to wipe all stored telemetry)."
  echo "Claude Code settings are untouched; run  python3 claude/apply-claude-settings.py --remove  to undo them."
  exit 0
fi

# ---- preflight: fail early with a clear message --------------------------------------------------------------
missing=()
for cmd in podman systemctl curl python3; do command -v "$cmd" >/dev/null || missing+=("$cmd"); done
if (( ${#missing[@]} )); then
  echo "Missing required tools: ${missing[*]}" >&2
  echo "On Debian/Ubuntu: sudo apt-get install -y podman curl python3   (systemd is required too)" >&2
  exit 1
fi
if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "systemd user services are not available in this session (no user bus)." >&2
  echo "Log in through a normal session, or see 'Without systemd' in the README for a plain container command." >&2
  exit 1
fi
if (( $(podman --version | sed -E 's/[^0-9]*([0-9]+).*/\1/') < 4 )); then
  echo "Podman 4.4 or newer is required (Quadlet support); found: $(podman --version)" >&2
  exit 1
fi
for port in 3000 4317 4318; do
  if ss -ltn 2>/dev/null | grep -q "127.0.0.1:$port " && ! podman ps --format '{{.Names}}' | grep -qx otel-lgtm; then
    echo "Port $port on localhost is already in use by something else; free it and re-run." >&2
    exit 1
  fi
done

[[ -f "$here/dashboards/claude-code.json" ]] || python3 "$here/tools/gen_dashboard.py" > "$here/dashboards/claude-code.json"

# ---- Grafana stack as a systemd user service (Podman Quadlet) -------------------------------------------------
mkdir -p "$quadlet_dir" "$unit_dir" "$data_dir"
sed "s|@SETUP_DIR@|$here|g" "$here/quadlet/otel-lgtm.container.in" > "$quadlet_dir/otel-lgtm.container"
if ! systemctl --user is-active --quiet otel-lgtm.service; then
  podman rm -f otel-lgtm >/dev/null 2>&1 || true    # a container started by hand would clash with the service's name
fi
systemctl --user daemon-reload
systemctl --user restart otel-lgtm.service

# A rootless service starts at boot (without a login) only if lingering is enabled for the user.
if (( boot )) && [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
  loginctl enable-linger "$USER" 2>/dev/null || echo "NOTE: run 'sudo loginctl enable-linger $USER' so the stack starts at boot."
fi

echo -n "Waiting for Grafana (the first start pulls the image, which can take a few minutes)"
ready=0
for _ in $(seq 1 150); do
  if curl -sf -o /dev/null http://127.0.0.1:3000/api/health; then ready=1; break; fi
  echo -n "."; sleep 2
done
echo
if (( ! ready )); then
  echo "Grafana did not come up in time. Check: systemctl --user status otel-lgtm.service   and   podman logs otel-lgtm" >&2
  exit 1
fi

# ---- optional parts ---------------------------------------------------------------------------------------------
if (( claude_settings )); then python3 "$here/claude/apply-claude-settings.py"; fi

if (( usage_poller )); then
  sed "s|@SETUP_DIR@|$here|g" "$here/usage-poller/claude-usage-poller.service.in" > "$unit_dir/claude-usage-poller.service"
  cp "$here/usage-poller/claude-usage-poller.timer" "$unit_dir/claude-usage-poller.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now claude-usage-poller.timer
  echo "Usage poller enabled (every 4 minutes). It reads ~/.claude/.credentials.json; remove it with --uninstall."
fi

cat <<MSG

Done.
  Grafana   http://127.0.0.1:3000   (first login: admin / admin, change the password right away)
  OTLP      grpc 127.0.0.1:4317    http 127.0.0.1:4318
  Status    systemctl --user status otel-lgtm.service
MSG
if (( ! claude_settings )); then
  echo "  Next      ./install.sh --claude-settings   (tells Claude Code to send telemetry here)"
fi
echo "  Then      restart any Claude Code session that should report; settings are read at startup."
