#!/bin/bash
# Ідемпотентне встановлення службових речей на сервері (викликає deploy/safe_deploy.py після merge):
#  * сторож cyd-watchdog (копія в /usr/local/bin — незалежна від /opt/cyd-alert і venv) + systemd-таймер 1/хв;
#  * залежності server/requirements.txt у venv, якщо файл змінився (telethon, segno для MTProto).
set -e
APP=/opt/cyd-alert
install -m 755 "$APP/deploy/cyd_watchdog.py" /usr/local/bin/cyd-watchdog.new
mv -f /usr/local/bin/cyd-watchdog.new /usr/local/bin/cyd-watchdog
cat > /etc/systemd/system/cyd-watchdog.service <<'UNIT'
[Unit]
Description=CYD Alert watchdog (Telegram alert if service down / no posts ingested)
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/bin/cyd-watchdog
TimeoutStartSec=120
UNIT
cat > /etc/systemd/system/cyd-watchdog.timer <<'UNIT'
[Unit]
Description=CYD Alert watchdog every minute
[Timer]
OnBootSec=3min
OnUnitActiveSec=60s
AccuracySec=5s
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable -q --now cyd-watchdog.timer
REQ="$APP/server/requirements.txt"
STAMP=/var/lib/cyd-watchdog/req.sha
mkdir -p /var/lib/cyd-watchdog
SHA=$(sha256sum "$REQ" | cut -d' ' -f1)
if [ "$(cat $STAMP 2>/dev/null)" != "$SHA" ]; then
  "$APP/.venv/bin/python" -m pip install -q -r "$REQ" && echo "$SHA" > $STAMP && echo "requirements installed"
fi
echo "ops OK"
