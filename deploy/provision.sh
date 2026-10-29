#!/bin/bash
# Розгортання реалтайм-сервера CYD Alert на чистому Ubuntu (Lightsail). Запуск від root:
#   sudo bash provision.sh <hostname>        напр. 3.120.10.20.sslip.io
# Ідемпотентний: можна запускати повторно. Секрети — /etc/cyd-alert/env (не в git).
set -euo pipefail
HOST="${1:?hostname}"
REPO="${REPO:-https://github.com/Bodya86/cyd-alert.git}"
APP=/opt/cyd-alert; DATA=/var/lib/cyd-alert; ETC=/etc/cyd-alert
export DEBIAN_FRONTEND=noninteractive

# swap 1 ГБ (план 512 МБ RAM)
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
timedatectl set-timezone Europe/Kyiv || true

apt-get update -q
apt-get install -y -q python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https gnupg rsync
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

id cyd >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin cyd
mkdir -p "$DATA/public" "$ETC"
git config --system --add safe.directory "$APP" 2>/dev/null || true
if [ ! -d "$APP/.git" ]; then
  if [ -f /tmp/cyd.bundle ]; then git clone -q -b main /tmp/cyd.bundle "$APP" && git -C "$APP" remote set-url origin "$REPO"
  else git clone -q --depth 50 "$REPO" "$APP"; fi
elif [ -f /tmp/cyd.bundle ]; then   # оновлення з локального bundle (ще не запушено на GitHub)
  git -C "$APP" fetch -q /tmp/cyd.bundle main && git -C "$APP" merge -q --ff-only FETCH_HEAD
fi
rm -f /tmp/cyd.bundle
chown -R cyd:cyd "$DATA"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q aiohttp pillow

if [ ! -f "$ETC/env" ]; then
  cat > "$ETC/env" <<'ENV'
# Секрети CYD Alert (chmod 600). Після зміни: systemctl restart cyd-alert
TG_BOT_TOKEN=
TG_CHAT_ID=6554910913
ALERTS_TOKEN=
UKRAINEALARM_KEY=
ENV
fi
chmod 600 "$ETC/env"; chown root:root "$ETC/env"

cat > /etc/systemd/system/cyd-alert.service <<UNIT
[Unit]
Description=CYD Alert realtime server
After=network-online.target
Wants=network-online.target

[Service]
User=cyd
Group=cyd
WorkingDirectory=$APP
EnvironmentFile=$ETC/env
Environment=TZ=Europe/Kyiv OUT_DIR=$DATA/public HOST=127.0.0.1 PORT=8080 PYTHONUNBUFFERED=1
ExecStart=$APP/.venv/bin/python $APP/server/rt_server.py
Restart=always
RestartSec=3
MemoryMax=400M
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

# автооновлення: git pull з GitHub раз на 5 хв; зміни в server/ або data/ → рестарт
cat > /usr/local/bin/cyd-update <<'UPD'
#!/bin/bash
set -e
cd /opt/cyd-alert
old=$(git rev-parse HEAD)
git fetch -q origin main
# лише fast-forward: якщо GitHub позаду (зміни ще не запушено) — нічого не робимо
git merge -q --ff-only origin/main 2>/dev/null || exit 0
new=$(git rev-parse HEAD)
if [ "$old" != "$new" ] && git diff --name-only "$old" "$new" | grep -qE '^(server|data)/'; then
  systemctl restart cyd-alert; echo "updated $old -> $new, restarted"
fi
UPD
chmod 755 /usr/local/bin/cyd-update
cat > /etc/systemd/system/cyd-update.service <<'UNIT'
[Unit]
Description=CYD Alert: git pull + restart on change
[Service]
Type=oneshot
ExecStart=/usr/local/bin/cyd-update
UNIT
cat > /etc/systemd/system/cyd-update.timer <<'UNIT'
[Unit]
Description=CYD Alert auto-update every 5 min
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
UNIT

cat > /etc/caddy/Caddyfile <<CADDY
# HTTPS (Let's Encrypt, автоматично) + HTTP без редиректу (ESP32 може ходити без TLS — швидше)
$HOST, http://$HOST, http:// {
	encode gzip
	reverse_proxy 127.0.0.1:8080
}
CADDY

systemctl daemon-reload
systemctl enable --now cyd-alert cyd-update.timer
systemctl restart cyd-alert
systemctl enable caddy && systemctl reload-or-restart caddy
echo "OK: https://$HOST/api/health"
