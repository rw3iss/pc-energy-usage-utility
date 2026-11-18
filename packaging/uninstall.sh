#!/usr/bin/env bash
# Remove the pcenergy collector service and its venv. Keeps the database and settings unless --purge.
#   sudo packaging/uninstall.sh [--purge]
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0 [--purge]" >&2
    exit 1
fi
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

echo "==> Stopping and removing the service"
systemctl disable --now pcenergy-collector 2>/dev/null || true
rm -f /etc/systemd/system/pcenergy-collector.service
systemctl daemon-reload

echo "==> Removing /opt/pcenergy"
rm -rf /opt/pcenergy

if [ "$PURGE" -eq 1 ]; then
    echo "==> --purge: removing /var/lib/pcenergy and /etc/pcenergy"
    rm -rf /var/lib/pcenergy /etc/pcenergy
else
    echo "    kept /var/lib/pcenergy (history) and /etc/pcenergy (settings); use --purge to delete them"
fi
echo "The user command stays installed; remove it with: uv tool uninstall pcenergy"
