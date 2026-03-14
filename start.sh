#!/bin/bash

# ============================================
# Email Assistant - Start Script
# Starts n8n + Cloudflare Tunnel, syncs URLs
# ============================================
COMPOSE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$COMPOSE_DIR"

source .env

ENV_FILE=".env"

echo "=== Starting Email Assistant ==="

# 1. Stop any running containers
echo "[1/6] Stopping old containers..."
docker compose down 2>/dev/null

# 2. Start all containers
echo "[2/6] Starting containers..."
docker compose up -d

# 3. Wait for Cloudflare tunnel to get a URL
echo "[3/6] Waiting for Cloudflare tunnel URL..."
TUNNEL_URL=""
for i in $(seq 1 30); do
    TUNNEL_URL=$(docker logs cloudflared 2>&1 | grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' | head -1)
    if [ -n "$TUNNEL_URL" ]; then
        break
    fi
    sleep 1
done

if [ -z "$TUNNEL_URL" ]; then
    echo "ERROR: Could not get Cloudflare tunnel URL after 30 seconds"
    echo "Check logs: docker logs cloudflared"
    exit 1
fi

echo "       Tunnel URL: $TUNNEL_URL"

# 4. Update .env with new URL
echo "[4/6] Updating .env..."
if grep -q "WEBHOOK_URL=" "$ENV_FILE"; then
    sed -i "s|WEBHOOK_URL=.*|WEBHOOK_URL=${TUNNEL_URL}/|" "$ENV_FILE"
else
    echo "WEBHOOK_URL=${TUNNEL_URL}/" >> "$ENV_FILE"
fi

# 5. Recreate n8n with new env (tunnel keeps running)
echo "[5/6] Restarting n8n with new URL..."
docker compose up -d --force-recreate n8n
sleep 5

# 6. Delete old Telegram webhook so n8n re-registers on publish
echo "[6/6] Resetting Telegram webhook..."
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook" > /dev/null 2>&1

echo ""
echo "=== Ready! ==="
echo "n8n:        http://localhost:5678"
echo "Tunnel:     $TUNNEL_URL"
echo ""
echo "Next: Open n8n, unpublish then publish your Telegram workflow."
