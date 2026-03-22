#!/bin/bash
set -e

ssh root@37.27.200.52 <<'EOF'
echo "🚫 Stopping stray Gunicorn processes..."
pkill -9 -f gunicorn || true
fuser -k 8001/tcp || true

echo "🔧 Rewriting dl-gunicorn.service with correct app path..."
cat >/etc/systemd/system/dl-gunicorn.service <<SERVICE
[Unit]
Description=Digital Lawyer Gunicorn Service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/digitallawyer/app/app
ExecStart=/var/www/digitallawyer/app/venv/bin/gunicorn \
  --workers 2 \
  --bind 127.0.0.1:8001 \
  --timeout 120 \
  --access-logfile /var/log/digitallawyer/access.log \
  --error-logfile /var/log/digitallawyer/error.log \
  --log-level debug \
  app:app

Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE

echo "🔄 Reloading systemd and restarting Gunicorn..."
systemctl daemon-reload
systemctl restart dl-gunicorn
systemctl enable dl-gunicorn

echo "🔁 Restarting Nginx..."
systemctl reload nginx

sleep 5
echo "🩺 Checking local health endpoint..."
curl -s http://127.0.0.1:8001/health || echo "⚠️ Local health check failed"

echo "🌍 Checking public site..."
curl -s https://digitallawyer.ai/health || echo "⚠️ Public health check failed"
EOF
