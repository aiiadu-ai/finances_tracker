#!/usr/bin/env bash
# Update the site on the server: get the new code, install what it needs, restart.
# Run on the server:  ~/finance/deploy/update.sh            (the released version, branch main)
#                     ~/finance/deploy/update.sh <branch>   (look at a change before it is released)
# Running it without a branch always brings the server back to main.
set -euo pipefail
cd "$(dirname "$0")/.."
branch="${1:-main}"

echo "1/3 Getting the new code (branch: $branch)..."
git fetch --quiet origin "$branch"
git checkout --quiet -B "$branch" "origin/$branch"

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
