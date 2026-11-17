#!/usr/bin/env bash
# Install Clip Pipeline on a Debian/Ubuntu VPS. Run as root from inside the unzipped project folder:
#     sudo bash deploy/install.sh                    # GUI on http://SERVER_IP:8080  (password protected)
#     sudo DOMAIN=clips.example.com bash deploy/install.sh   # + free HTTPS via Caddy (domain must point to the VPS)
#     sudo BIND=$(tailscale ip -4) bash deploy/install.sh    # private: reachable only over Tailscale
set -euo pipefail
APP=/opt/clip-pipeline
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update -y
apt-get install -y ffmpeg python3 python3-venv python3-pip rsync
mkdir -p "$APP"
rsync -a --exclude data --exclude .env --exclude config.yaml --exclude tests --exclude __pycache__ "$SRC"/ "$APP"/
cd "$APP"
python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt
[ -f config.yaml ] || cp config.example.yaml config.yaml

if [ ! -f .env ]; then
  touch .env
fi
if ! grep -q '^GUI_PASSWORD=' .env; then
  PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(12))')"
  echo "GUI_PASSWORD=$PW" >> .env
  NEWPW="$PW"
fi
chmod 600 .env

HOST="${BIND:-0.0.0.0}"          # BIND=<tailscale ip> keeps the GUI off the public internet
[ -n "${DOMAIN:-}" ] && HOST=127.0.0.1     # with Caddy, only Caddy talks to the app
cat > /etc/systemd/system/clip-pipeline.service <<UNIT
[Unit]
Description=Clip Pipeline
After=network-online.target
[Service]
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python -m clipper --host $HOST run
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable clip-pipeline
systemctl restart clip-pipeline      # restart, not just start: an update must replace the running code

if [ -n "${DOMAIN:-}" ]; then
  apt-get install -y caddy
  printf '%s {\n  reverse_proxy 127.0.0.1:8080\n}\n' "$DOMAIN" > /etc/caddy/Caddyfile
  systemctl reload caddy || systemctl restart caddy
  URL="https://$DOMAIN"
else
  command -v ufw >/dev/null && ufw allow 8080/tcp || true
  URL="http://$(hostname -I | awk '{print $1}'):8080"
fi

echo
echo "=============================================="
echo " Clip Pipeline is running:  $URL"
echo " Username: anything      Password: ${NEWPW:-<already in $APP/.env>}"
echo " Logs:   journalctl -u clip-pipeline -f"
echo " Update: re-run this script from the new folder"
echo "=============================================="
