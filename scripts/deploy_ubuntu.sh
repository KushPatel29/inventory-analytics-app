#!/usr/bin/env bash
set -euo pipefail

# Idempotent Ubuntu deployment script for Inventory App (Flask + Gunicorn + systemd)
# Usage: sudo bash scripts/deploy_ubuntu.sh [PORT]

APP_NAME="inventory-app"
APP_DIR="/opt/inventory-app"
APP_USER="inventory"
REPO_URL="https://github.com/KushPatel29/inventory-analytics-app.git"
PORT="${1:-8012}"

echo "[*] Deploying $APP_NAME to $APP_DIR on port $PORT"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This script expects an Ubuntu/Debian system with apt-get available." >&2
  exit 1
fi

echo "[*] Installing prerequisites (python venv, git)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip git

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  echo "[*] Creating system user $APP_USER"
  adduser --system --group --home "$APP_DIR" "$APP_USER"
fi

mkdir -p "$APP_DIR"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" bash -lc "\
  set -euo pipefail; \
  if [ ! -d '$APP_DIR/.git' ]; then \
    echo '[*] Cloning repository'; \
    git clone '$REPO_URL' '$APP_DIR'; \
  else \
    echo '[*] Updating repository'; \
    cd '$APP_DIR' && git pull --ff-only; \
  fi; \
  cd '$APP_DIR'; \
  python3 -m venv .venv; \
  source .venv/bin/activate; \
  pip install --upgrade pip; \
  pip install -r requirements.txt gunicorn; \
  mkdir -p data; \
"

UNIT_FILE="/etc/systemd/system/${APP_NAME}.service"
echo "[*] Writing systemd unit: $UNIT_FILE"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Inventory App (Gunicorn)
After=network.target

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=${APP_DIR}/.venv/bin/gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers 3 --threads 4 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${APP_NAME}"
systemctl restart "${APP_NAME}"

echo "[*] Checking service status (tail logs with: journalctl -u ${APP_NAME} -f)"
systemctl --no-pager --full status "${APP_NAME}" || true

if command -v ufw >/dev/null 2>&1; then
  if ufw status | grep -q "Status: active"; then
    echo "[*] Allowing port ${PORT}/tcp via UFW"
    ufw allow ${PORT}/tcp || true
  fi
fi

echo "[*] Deployment complete. Test URLs:"
echo "- App:    http://$(hostname -I | awk '{print $1}'):${PORT}"
echo "- Health: http://$(hostname -I | awk '{print $1}'):${PORT}/healthz"

