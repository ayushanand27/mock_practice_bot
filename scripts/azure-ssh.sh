#!/usr/bin/env bash
# Open an SSH shell to the Azure bot VM.
set -euo pipefail
HOST="${AZURE_BOT_HOST:-104.208.98.207}"
USER_NAME="${AZURE_BOT_USER:-azureuser}"
KEY="${AZURE_BOT_KEY:-$HOME/Downloads/mock-practice-bot_key.pem}"
if [[ ! -f "$KEY" && -f "/c/Users/ayush/Downloads/mock-practice-bot_key.pem" ]]; then
  KEY="/c/Users/ayush/Downloads/mock-practice-bot_key.pem"
fi
exec ssh -i "$KEY" -o StrictHostKeyChecking=yes "${USER_NAME}@${HOST}" "$@"
