#!/usr/bin/env bash
# Gunicorn worker'ları yeni kodu yüklesin diye servisi yeniden başlatır.
# Sunucuda: sudo ./scripts/restart_mrktahsilat.sh
set -euo pipefail
SERVICE="${GUNICORN_SERVICE:-gunicorn_mrktahsilat}"
if ! systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  echo "Starting $SERVICE..."
  sudo systemctl start "$SERVICE"
else
  echo "Restarting $SERVICE..."
  sudo systemctl restart "$SERVICE"
fi
systemctl is-active "$SERVICE" && echo "OK: $SERVICE is active"
