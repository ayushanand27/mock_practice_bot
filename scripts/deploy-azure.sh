#!/usr/bin/env bash
# Redeploy mock_practice_bot to the Azure VM.
# Usage (from repo root): bash scripts/deploy-azure.sh
set -euo pipefail

HOST="${AZURE_BOT_HOST:-104.208.98.207}"
USER_NAME="${AZURE_BOT_USER:-azureuser}"
KEY="${AZURE_BOT_KEY:-$HOME/Downloads/mock-practice-bot_key.pem}"
# Git Bash on Windows:
if [[ ! -f "$KEY" && -f "/c/Users/ayush/Downloads/mock-practice-bot_key.pem" ]]; then
  KEY="/c/Users/ayush/Downloads/mock-practice-bot_key.pem"
fi

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=yes "${USER_NAME}@${HOST}")

echo "==> Pulling latest code and restarting service on ${USER_NAME}@${HOST}"
"${SSH[@]}" 'bash -s' <<'REMOTE'
set -euo pipefail
cd ~/mock_practice_bot
git fetch origin
git reset --hard origin/main
source .venv/bin/activate
pip install -q -r requirements.txt
sudo systemctl restart mock-practice-bot
sleep 2
sudo systemctl --no-pager --full status mock-practice-bot | head -25
REMOTE
echo "==> Deploy done"
