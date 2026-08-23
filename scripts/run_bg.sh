#!/usr/bin/env bash
# Launch a module detached with a pidfile, surviving the tool call that starts it.
set -uo pipefail
cd "$(dirname "$0")/.."
NAME="$1"; shift
mkdir -p logs data
PYTHONPATH=src setsid python3 -m "$@" >> "logs/${NAME}.log" 2>&1 < /dev/null &
echo $! > "data/${NAME}.pid"
sleep 2
kill -0 "$(cat data/${NAME}.pid)" 2>/dev/null && echo "${NAME} started pid=$(cat data/${NAME}.pid)" || { echo "${NAME} FAILED"; tail -5 "logs/${NAME}.log"; }
