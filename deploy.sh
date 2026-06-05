#!/bin/bash
# deploy.sh - Deploy Bot Jual Gmail ke VPS
# Jalankan: bash deploy.sh

set -e

VPS_USER="root"
VPS_IP="43.163.107.250"
VPS_DIR="/opt/botjualgmail"
LOCAL_DIR="$(dirname "$0")"

echo "🚀 Deploy Bot Jual Gmail ke VPS..."

# Sync file ke VPS (exclude .env dan database)
rsync -avz --progress \
  --exclude='.env' \
  --exclude='database/bot.db' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='logs/*.log' \
  --exclude='.git' \
  "$LOCAL_DIR/" "$VPS_USER@$VPS_IP:$VPS_DIR/"

echo "📦 Install dependencies di VPS..."
ssh "$VPS_USER@$VPS_IP" "
  cd $VPS_DIR
  python3 -m pip install -r requirements.txt --quiet
  mkdir -p logs database
  
  # Restart service jika ada
  if systemctl is-active --quiet botjualgmail; then
    systemctl restart botjualgmail
    echo '✅ Service di-restart'
  else
    echo '⚠️ Service belum ada. Buat dulu: /etc/systemd/system/botjualgmail.service'
  fi
"

echo "✅ Deploy selesai!"
