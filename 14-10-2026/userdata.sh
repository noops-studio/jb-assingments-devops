#!/bin/bash
set -e

apt-get update -y
apt-get install -y python3 python3-pip python3-venv stress-ng

python3 -m venv /opt/app-venv
source /opt/app-venv/bin/activate
pip install flask flask-socketio python-socketio psutil
deactivate

echo "__APP_PY_B64__" | base64 -d | gunzip > /opt/app.py
chmod +x /opt/app.py

cat > /etc/systemd/system/app.service << 'EOF'
[Unit]
Description=Web Application
After=network.target

[Service]
Type=simple
User=root
ExecStart=/opt/app-venv/bin/python /opt/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable app
systemctl start app