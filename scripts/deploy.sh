#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${APP_ROOT:-$HOME/apps/reposter_t}"
RELEASE_ID="${GITHUB_SHA:-$(date -u +%Y%m%d%H%M%S)}"
RELEASE_DIR="$APP_ROOT/releases/$RELEASE_ID"
SHARED_DIR="$APP_ROOT/shared"

mkdir -p "$RELEASE_DIR" "$SHARED_DIR/data"
rsync -a \
  --exclude='.git/' \
  --exclude='.env' \
  --exclude='.venv/' \
  --exclude='.test-venv/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='__pycache__/' \
  --exclude='data/' \
  "$GITHUB_WORKSPACE/" "$RELEASE_DIR/"

if [[ ! -f "$SHARED_DIR/.env" ]]; then
  echo "Missing $SHARED_DIR/.env; deployment stopped." >&2
  exit 1
fi

python3 -m venv "$RELEASE_DIR/.venv"
"$RELEASE_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade pip
"$RELEASE_DIR/.venv/bin/python" -m pip install --disable-pip-version-check --quiet "$RELEASE_DIR"
"$RELEASE_DIR/.venv/bin/python" -m compileall -q "$RELEASE_DIR/reposter_bot"

ln -sfn "$SHARED_DIR/data" "$RELEASE_DIR/data"
ln -sfn "$RELEASE_DIR" "$APP_ROOT/current.next"
mv -Tf "$APP_ROOT/current.next" "$APP_ROOT/current"

sudo install -m 0644 "$RELEASE_DIR/deploy/reposter-bot.service" /etc/systemd/system/reposter-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now reposter-bot.service
sudo systemctl restart reposter-bot.service
sudo systemctl --no-pager --full status reposter-bot.service
