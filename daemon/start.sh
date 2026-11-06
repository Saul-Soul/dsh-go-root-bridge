#!/bin/sh
# Root 桥监督器：守护进程崩溃自动重启
DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
while true; do
  python3 "$DIR/daemon/daemon.py" >> "$DIR/daemon.log" 2>&1
  echo "[supervisor] restart $(date)" >> "$DIR/daemon.log"
  sleep 2
done
