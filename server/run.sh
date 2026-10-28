#!/bin/bash
# Запуск: ALERTS_TOKEN=xxx ./run.sh      (дашборд: http://<ip>:8080/)
cd "$(dirname "$0")"
exec python3 server.py
