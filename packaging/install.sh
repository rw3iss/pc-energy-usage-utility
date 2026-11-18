#!/usr/bin/env bash
# Install or upgrade the pcenergy collector service. Run from the repo: sudo packaging/install.sh
#
# Touches only: /opt/pcenergy (venv), /etc/pcenergy (config), /etc/systemd/system/pcenergy-*.service
# and /var/lib/pcenergy (database, created by systemd). Safe to run again.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/pcenergy
UNIT=/etc/systemd/system/pcenergy-collector.service
CONF=/etc/pcenergy/collector.toml

step() { printf '\033[1m==> %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

PY="$(command -v python3 || true)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "Python 3.12 or later is required (found: ${PY:-none})" >&2
    exit 1
fi

step "Python virtual environment in $PREFIX/venv"
if [ ! -x "$PREFIX/venv/bin/python" ]; then
    "$PY" -m venv "$PREFIX/venv"
fi
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip

SPEC="$REPO"
if [ -e /proc/driver/nvidia ]; then
    SPEC="$REPO[nvidia]"
    step "NVIDIA driver found: installing with the [nvidia] extra"
fi
step "Installing pcenergy into the venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade --no-cache-dir "$SPEC"

step "Collector settings in $CONF"
if [ -f "$CONF" ]; then
    echo "    kept existing $CONF"
else
    install -d -m 0755 /etc/pcenergy
    "$PREFIX/venv/bin/python" - "$CONF" <<'PY'
import sys
from pathlib import Path
from pcenergy import config as c
c.save(c.COLLECTOR_SCHEMA, c.defaults(c.COLLECTOR_SCHEMA), Path(sys.argv[1]))
PY
    chmod 0644 "$CONF"
fi

step "systemd unit $UNIT"
install -m 0644 "$REPO/packaging/systemd/pcenergy-collector.service" "$UNIT"
systemctl daemon-reload
if systemctl is-active --quiet pcenergy-collector; then
    systemctl restart pcenergy-collector
    echo "    restarted (upgrade)"
else
    systemctl enable --now pcenergy-collector
fi

step "Check"
"$PREFIX/venv/bin/pcenergy" collect --once || true
sleep 2
systemctl --no-pager --lines=5 status pcenergy-collector || true

cat <<MSG

Collector installed. Next, as your normal user:
  uv tool install '$REPO[nvidia]'    # or: pipx install '$REPO[nvidia]'
  pcenergy doctor
  pcenergy
MSG
