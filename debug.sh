#!/bin/bash
ssh root@37.27.200.52 <<'EOF'
set -e

echo "🚫 Killing stray Gunicorn..."
pkill -9 -f gunicorn || true
fuser -k 8001/tcp || true

cd /var/www/digitallawyer/app

echo "📦 Rebuilding virtualenv..."
rm -rf venv
python3 -m venv venv
source venv/bin/activate

echo "⬇️ Installing dependencies from app/requirements.txt..."
pip install --upgrade pip wheel setuptools
pip install -r app/requirements.txt

echo "🔧 Installing Gunicorn & Flask (if not in requirements)..."
pip install gunicorn flask

echo "🔑 Fixing permissions..."
chown -R www-data:www-data /var/www/digitallawyer
chmod -R 755 /var/www/digitallawyer

echo "📝 Rewriting systemd service..."
cat >/etc/systemd/system/dl-gunicorn.service <<SERVICE
[Unit]
Description=Digital Lawyer Gunicorn Service
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/digitallawyer/app
ExecStart=/var/www/digitallawyer/app/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8001 --timeout 120 --access-logfile /var/log/digitallawyer/access.log --error-logfile /var/log/digitallawyer/error.log --log-level debug app:app
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

echo "🔄 Restarting services..."
systemctl daemon-reload
systemctl restart dl-gunicorn
systemctl restart nginx

sleep 5

echo "🩺 Checking local /health..."
curl -s http://127.0.0.1:8001/health || echo "⚠️ Local health failed"

echo "🌍 Checking public /health..."
curl -s https://digitallawyer.ai/health || echo "⚠️ Public health failed"
EOF
