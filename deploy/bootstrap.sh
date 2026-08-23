#!/usr/bin/env bash
#
# One-shot setup for a fresh Ubuntu 22.04/24.04 server.
#
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/BRANCH/deploy/bootstrap.sh | sudo bash
#
# or, having cloned already:
#
#   sudo bash deploy/bootstrap.sh
#
# Installs Python, clones the repo, creates an unprivileged service account,
# and registers the collector as a systemd unit that starts on boot and
# restarts on failure. Safe to re-run: every step checks before acting.

set -euo pipefail

REPO_URL="${DEGEN_REPO:-https://github.com/xin10ylop/memecoin2.git}"
BRANCH="${DEGEN_BRANCH:-claude/memecoin-trading-bot-y7skua}"
INSTALL_DIR="${DEGEN_DIR:-/opt/degen}"
SERVICE_USER="${DEGEN_USER:-degen}"

say() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl ca-certificates >/dev/null

say "Creating service account '$SERVICE_USER'"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
  echo "already exists"
fi

say "Fetching the code into $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard --quiet "origin/$BRANCH"
else
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

say "Creating the virtualenv and installing dependencies"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet -e "$INSTALL_DIR"

mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
  say "Wrote $INSTALL_DIR/.env from the example - nothing in it is required for collecting"
fi

say "Installing the systemd unit"
sed -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" -e "s|@USER@|$SERVICE_USER|g" \
  "$INSTALL_DIR/deploy/degen-collector.service" > /etc/systemd/system/degen-collector.service
cp "$INSTALL_DIR/deploy/degen-logrotate" /etc/logrotate.d/degen
systemctl daemon-reload
systemctl enable --now degen-collector

sleep 4
say "Status"
systemctl --no-pager --lines=8 status degen-collector || true

cat <<EOF

  Collector installed and running. It starts on boot and restarts if it dies.

    watch it          journalctl -u degen-collector -f
    how much data     sudo -u $SERVICE_USER $INSTALL_DIR/.venv/bin/degen status
    stop / start      systemctl stop degen-collector
    update the code   sudo bash $INSTALL_DIR/deploy/bootstrap.sh

  Leave it alone for two to four weeks, then run:

    sudo -u $SERVICE_USER $INSTALL_DIR/.venv/bin/degen validate

  That is the number that decides whether any of this is worth trading.

EOF
