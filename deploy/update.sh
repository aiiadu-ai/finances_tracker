#!/usr/bin/env bash
# Update the site on the server: get the new code, install what it needs, restart.
# Run on the server:  ~/finance/deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "1/3 Getting the new code..."
git pull --ff-only

echo "2/3 Installing requirements..."
.venv/bin/pip install --quiet -r requirements.txt

echo "3/3 Restarting the site..."
sudo systemctl restart finance
sleep 2
systemctl is-active finance
curl -fsS -o /dev/null http://127.0.0.1:8000/ && echo "Site answers."

# The Telegram bot, once it has been set up (deploy/finance-bot.service installed).
if systemctl cat finance-bot >/dev/null 2>&1; then
  sudo systemctl restart finance-bot
  sleep 2
  echo "Bot: $(systemctl is-active finance-bot)"
fi
echo "Done."
