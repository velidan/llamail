# n8n + Telegram Webhook SSL Guide

**Problem:** Telegram requires HTTPS webhooks, but local n8n runs on HTTP  
**Solution:** Use ngrok with a free static domain for permanent HTTPS tunneling

---

## Table of Contents

1. [The Problem](#the-problem)
2. [Solution Overview](#solution-overview)
3. [Step-by-Step Setup](#step-by-step-setup)
4. [Complete Docker Configuration](#complete-docker-configuration)
5. [Verifying the Setup](#verifying-the-setup)
6. [Troubleshooting](#troubleshooting)
7. [Alternative Solutions](#alternative-solutions)

---

## The Problem

When setting up a Telegram Trigger node in n8n running locally via Docker, you'll encounter this error:

```
Telegram Trigger: Bad Request: bad webhook: An HTTPS URL must be provided for webhook
```

### Why This Happens

1. **Telegram's Security Requirement:** Telegram Bot API only accepts webhook URLs with valid HTTPS/SSL certificates
2. **Local n8n runs on HTTP:** By default, n8n runs on `http://localhost:5678`
3. **localhost isn't accessible:** Even if you had SSL, Telegram's servers can't reach your local machine

### The Error Chain

```
Your PC (http://localhost:5678)
        ↓
Telegram API tries to send webhook
        ↓
❌ Fails: "An HTTPS URL must be provided"
```

---

## Solution Overview

Use **ngrok** to create a secure tunnel from the internet to your local n8n instance.

```
Telegram Servers
        ↓
https://your-domain.ngrok-free.dev (HTTPS ✓)
        ↓
ngrok tunnel (encrypted)
        ↓
Your PC → n8n (http://localhost:5678)
```

### Why ngrok?

| Feature | ngrok (Free) | Cloudflare Tunnel | Self-hosted SSL |
|---------|--------------|-------------------|-----------------|
| Free static domain | ✅ Yes | ❌ Requires card | ❌ Need domain |
| Easy setup | ✅ 5 minutes | ⚠️ 10+ minutes | ❌ Complex |
| Works with Docker | ✅ Yes | ✅ Yes | ⚠️ Varies |
| URL persistence | ✅ Static domain | ⚠️ Changes on restart* | ✅ Your domain |

*Cloudflare quick tunnels change URL on every restart unless you set up a named tunnel (requires payment method on file)

---

## Step-by-Step Setup

### Step 1: Create ngrok Account

1. Go to [https://ngrok.com/signup](https://ngrok.com/signup)
2. Create a free account
3. Verify your email

### Step 2: Get Your Auth Token

1. Log into ngrok dashboard
2. Go to: [https://dashboard.ngrok.com/get-started/your-authtoken](https://dashboard.ngrok.com/get-started/your-authtoken)
3. Copy your authtoken (looks like: `1Y6vOGcucbBzDUv4XkfFl2ojAyv_...`)

### Step 3: Get Your Free Static Domain

1. Go to: [https://dashboard.ngrok.com/domains](https://dashboard.ngrok.com/domains)
2. Click **"Create Domain"** (or you may already have one assigned)
3. Copy your domain (looks like: `your-name-random.ngrok-free.app`)

### Step 4: Create Project Structure

```bash
mkdir n8n-telegram
cd n8n-telegram
```

### Step 5: Create docker-compose.yml

Create the file with the configuration shown in the next section.

### Step 6: Start Services

```bash
docker compose up -d
```

### Step 7: Configure n8n

1. Open `http://localhost:5678`
2. Create your account
3. Add Telegram credentials (Bot Token from @BotFather)
4. Create workflow with Telegram Trigger
5. **Publish/Activate** the workflow

---

## Complete Docker Configuration

### docker-compose.yml

```yaml
services:
  n8n:
    image: n8nio/n8n:latest
    container_name: n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - N8N_HOST=localhost
      - N8N_PORT=5678
      - N8N_PROTOCOL=https
      - WEBHOOK_URL=https://YOUR-STATIC-DOMAIN.ngrok-free.app/
      - GENERIC_TIMEZONE=America/Chicago
      - TZ=America/Chicago
    volumes:
      - n8n_data:/home/node/.n8n

  tunnel:
    image: ngrok/ngrok:latest
    container_name: ngrok
    restart: unless-stopped
    command: http n8n:5678 --authtoken YOUR_AUTH_TOKEN --domain YOUR-STATIC-DOMAIN.ngrok-free.app
    depends_on:
      - n8n

volumes:
  n8n_data:
```

### Required Replacements

Replace these placeholders with your actual values:

| Placeholder | Where to Find It | Example |
|-------------|------------------|---------|
| `YOUR_AUTH_TOKEN` | ngrok dashboard → Your Authtoken | `1Y6vOGcucbBzDUv4XkfFl2ojAyv_6jd8...` |
| `YOUR-STATIC-DOMAIN.ngrok-free.app` | ngrok dashboard → Domains | `azalee-noncorrelating-nonpolemically.ngrok-free.dev` |

### Example with Real Values

```yaml
services:
  n8n:
    image: n8nio/n8n:latest
    container_name: n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - N8N_HOST=localhost
      - N8N_PORT=5678
      - N8N_PROTOCOL=https
      - WEBHOOK_URL=https://azalee-noncorrelating-nonpolemically.ngrok-free.dev/
      - GENERIC_TIMEZONE=America/Chicago
      - TZ=America/Chicago
    volumes:
      - n8n_data:/home/node/.n8n

  tunnel:
    image: ngrok/ngrok:latest
    container_name: ngrok
    restart: unless-stopped
    command: http n8n:5678 --authtoken 1Y6vOGcucbBzDUv4XkfFl2ojAyv_6jd8oVJv7RhFvD1wGBv4g --domain azalee-noncorrelating-nonpolemically.ngrok-free.dev
    depends_on:
      - n8n

volumes:
  n8n_data:
```

---

## Verifying the Setup

### 1. Check Containers Are Running

```bash
docker compose ps
```

Expected output:
```
NAME      IMAGE                COMMAND     SERVICE   STATUS         PORTS
n8n       n8nio/n8n:latest     ...         n8n       Up             0.0.0.0:5678->5678/tcp
ngrok     ngrok/ngrok:latest   ...         tunnel    Up             4040/tcp
```

### 2. Check n8n Logs

```bash
docker compose logs n8n
```

Look for:
```
Editor is now accessible via:
https://your-domain.ngrok-free.dev
```

### 3. Verify Telegram Webhook

Open in browser (replace `BOT_TOKEN` with your bot token):
```
https://api.telegram.org/botBOT_TOKEN/getWebhookInfo
```

**Good response:**
```json
{
  "ok": true,
  "result": {
    "url": "https://your-domain.ngrok-free.dev/webhook/...",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "max_connections": 40,
    "ip_address": "18.192.31.165",
    "allowed_updates": ["message"]
  }
}
```

**Bad indicators:**
- `"url": ""` → Webhook not registered (re-activate workflow)
- `"last_error_message"` → Check the error and troubleshoot

### 4. Test End-to-End

1. Open your Telegram bot
2. Send a message: `/start` or `Hello`
3. Check n8n → Executions tab
4. You should see the execution appear!

---

## Troubleshooting

### Error: "Bad webhook: An HTTPS URL must be provided"

**Cause:** `WEBHOOK_URL` environment variable not set or n8n not restarted

**Fix:**
```bash
# Verify .env or docker-compose.yml has WEBHOOK_URL
# Then restart:
docker compose down
docker compose up -d
```

### Error: "Wrong response from the webhook: 530"

**Cause:** Cloudflare tunnel URL changed (if using Cloudflare quick tunnels)

**Fix:** Switch to ngrok with static domain (this guide)

### Webhook URL is Empty

**Cause:** Workflow not activated

**Fix:**
1. Open n8n
2. Go to your Telegram workflow
3. Toggle OFF then ON (or Unpublish → Publish)

### ngrok Container Exits Immediately

**Cause:** Invalid authtoken or domain

**Fix:**
```bash
# Check logs
docker compose logs tunnel

# Common issues:
# - Typo in authtoken
# - Domain doesn't match your ngrok account
# - Using someone else's domain
```

### Messages Not Appearing in n8n

**Checklist:**
1. Is the workflow **active/published**?
2. Is ngrok container running? (`docker compose ps`)
3. Is webhook registered? (check `getWebhookInfo`)
4. Are there pending updates? (`pending_update_count` in webhook info)

**Clear pending updates:**
```
https://api.telegram.org/botBOT_TOKEN/setWebhook?url=https://your-domain.ngrok-free.dev/webhook/YOUR_WEBHOOK_PATH&drop_pending_updates=true
```

### Port 5678 Already in Use

```bash
# Find what's using it
# Windows:
netstat -ano | findstr :5678

# Linux/Mac:
lsof -i :5678

# Or change port in docker-compose.yml:
ports:
  - "5679:5678"  # Use 5679 externally
```

---

## Alternative Solutions

### Option 1: Cloudflare Quick Tunnel (URL Changes on Restart)

Good for testing, not production.

```yaml
services:
  n8n:
    image: n8nio/n8n:latest
    container_name: n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - N8N_HOST=localhost
      - N8N_PORT=5678
      - N8N_PROTOCOL=https
      - WEBHOOK_URL=${WEBHOOK_URL}
      - GENERIC_TIMEZONE=America/Chicago
    volumes:
      - n8n_data:/home/node/.n8n

  tunnel:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared
    restart: unless-stopped
    command: tunnel --no-autoupdate --url http://n8n:5678
    depends_on:
      - n8n

volumes:
  n8n_data:
```

After starting, get URL from logs:
```bash
docker compose logs tunnel | grep trycloudflare
```

**Downside:** URL changes every restart, requiring webhook re-registration.

### Option 2: Cloudflare Named Tunnel (Permanent, Requires Card on File)

1. Create Cloudflare account
2. Add payment method (won't be charged for free tier)
3. Go to Zero Trust → Networks → Tunnels
4. Create named tunnel, get token
5. Use token in docker-compose

```yaml
tunnel:
  image: cloudflare/cloudflared:latest
  command: tunnel --no-autoupdate run --token YOUR_TUNNEL_TOKEN
```

### Option 3: Real Domain + Let's Encrypt

For production deployments with your own domain.

Requires:
- Domain name
- Reverse proxy (nginx/traefik)
- Let's Encrypt certificate

More complex but gives you full control.

### Option 4: Telegram Polling (No Webhook)

Instead of webhooks, use polling mode. n8n doesn't natively support this, but you could:
- Use a scheduled trigger to call `getUpdates` API
- Process messages in batches

Not recommended due to delays and complexity.

---

## Quick Reference

### Commands

```bash
# Start services
docker compose up -d

# Stop services
docker compose down

# View all logs
docker compose logs

# View specific service logs
docker compose logs n8n
docker compose logs tunnel

# Check running containers
docker compose ps

# Restart everything
docker compose down && docker compose up -d
```

### Telegram API Endpoints

```bash
# Get webhook info
https://api.telegram.org/botBOT_TOKEN/getWebhookInfo

# Set webhook manually
https://api.telegram.org/botBOT_TOKEN/setWebhook?url=WEBHOOK_URL

# Delete webhook
https://api.telegram.org/botBOT_TOKEN/deleteWebhook

# Get pending updates
https://api.telegram.org/botBOT_TOKEN/getUpdates
```

### Important URLs

- ngrok Dashboard: https://dashboard.ngrok.com
- ngrok Authtoken: https://dashboard.ngrok.com/get-started/your-authtoken
- ngrok Domains: https://dashboard.ngrok.com/domains
- n8n Local: http://localhost:5678
- n8n Docs: https://docs.n8n.io

---

## Summary

| Component | Purpose |
|-----------|---------|
| **n8n** | Workflow automation, processes Telegram messages |
| **ngrok** | Creates HTTPS tunnel to your local n8n |
| **Static Domain** | Permanent URL that never changes |
| **WEBHOOK_URL** | Tells n8n what public URL to use for webhooks |

**The flow:**
```
Telegram → ngrok (HTTPS) → n8n (HTTP) → Your Workflow
```

With this setup, your Telegram bot will work reliably with n8n, even running locally!

---

*Guide created: January 2026*
