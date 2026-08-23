#!/usr/bin/env bash
# Start/stop/status the collector with a pidfile. The container suspends when
# the session is idle, so this is written to be safe to call repeatedly.
set -uo pipefail
cd "$(dirname "$0")/.."
PIDFILE=data/collector.pid
LOG=logs/collector.log
mkdir -p logs data

alive() { [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; }

case "${1:-status}" in
  start)
    if alive; then echo "already running pid=$(cat $PIDFILE)"; exit 0; fi
    PYTHONPATH=src setsid python3 -m degen.collect.collector >> "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    sleep 3
    if alive; then echo "started pid=$(cat $PIDFILE)"; else echo "FAILED to start"; tail -5 "$LOG"; exit 1; fi
    ;;
  stop)
    if alive; then kill "$(cat $PIDFILE)"; sleep 2; echo "stopped"; else echo "not running"; fi
    rm -f "$PIDFILE"
    ;;
  status)
    if alive; then echo "running pid=$(cat $PIDFILE)"; else echo "not running"; fi
    ;;
esac
