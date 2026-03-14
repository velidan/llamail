# n8n + Cloudflare Tunnel + Telegram + Gmail Setup Guide

## Overview

This setup runs n8n locally via Docker with a Cloudflare Tunnel for HTTPS access,
enabling Telegram bot webhooks and Gmail OAuth integration.

```
Telegram Servers / Gmail OAuth
      |
      v
https://random-words.trycloudflare.com  (HTTPS)
      |
      v
Cloudflare Tunnel (cloudflared container)
      |
      v
n8n (localhost:5678)
```

---

## Prerequisites

- **Docker Desktop** installed and running
- **Git Bash** (or any terminal on Windows)
- **Telegram account** with a bot created via @BotFather
- **Google account** (Gmail) for email integration
- **llama.cpp** built locally (for LLM processing — dual server: chat on :11434, embeddings on :11435)

---

## Project Structure

```
llamail/
├── .env                    # Environment variables (WEBHOOK_URL, etc.)
├── docker-compose.yml      # Docker services: n8n + cloudflared
├── start.sh                # Startup script (handles URL sync)
├── start_llama_servers.sh  # llama.cpp server launcher
├── webservice/             # FastAPI app (the brain)
└── docs/                   # Setup guides
```

---

## How It Works

### The Problem

- Telegram requires **HTTPS** webhook URLs to deliver bot messages
- Gmail OAuth redirect requires a reachable callback URL
- Local n8n runs on **HTTP** (`http://localhost:5678`)
- Telegram servers can't reach localhost

### The Solution

**Cloudflare Tunnel** creates a free, temporary HTTPS URL that forwards traffic
to your local n8n. No Cloudflare account or signup needed.

**Gmail OAuth** uses `localhost` redirect URI since the OAuth flow happens in
your browser (not from Google servers), so it always works regardless of tunnel URL.

### Important: Tunnel URL Changes on Restart

Cloudflare's free "quick tunnel" generates a **random URL every time** the
tunnel container restarts (e.g., `https://random-words.trycloudflare.com`).

This means after every restart you must:
1. Get the new tunnel URL
2. Update the `WEBHOOK_URL` in `.env`
3. Recreate the n8n container so it reads the new URL
4. Re-publish the Telegram workflow in n8n

The `start.sh` script automates steps 1-3.

---

## Initial Setup (One Time)

### 1. Create a Telegram Bot

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`
3. Choose a display name (e.g., "Email Assistant")
4. Choose a username (must end in `bot`, e.g., `my_email_assist_bot`)
5. Copy the **bot token** (e.g., `1111111111:AAFK2bGXEoyMdHWgH0q...`)
6. Save the token — you'll need it for n8n credentials

### 2. Set Up Google Cloud for Gmail API

#### 2a. Create/Select a Google Cloud Project

1. Go to **https://console.cloud.google.com**
2. Sign in with the Gmail account you want to connect
3. Select an existing project or create a new one
   - Click **Select a project** (top bar) > **New Project**
   - Name it `email-assistant`, click **Create**

#### 2b. Enable Gmail API

1. Go to **APIs & Services > Library** (left sidebar)
2. Search for **Gmail API**
3. Click it > click **Enable**

#### 2c. Configure OAuth Consent Screen

Skip this if you already have one configured. Otherwise:

1. Go to **APIs & Services > OAuth consent screen**
2. Select **External**, click **Create**
3. Fill in:
   - App name: `Email Assistant`
   - User support email: your email
   - Developer contact email: your email
4. Click **Save and Continue**
5. **Scopes** page — click **Add or Remove Scopes**, add:
   - `https://mail.google.com/`
6. Click **Save and Continue**
7. **Test users** — click **Add Users**, add your Gmail address
8. Click **Save and Continue**

#### 2d. Create OAuth Credentials

1. Go to **APIs & Services > Credentials**
2. Click **+ Create Credentials > OAuth client ID**
3. Application type: **Web application**
4. Name: `n8n`
5. Under **Authorized redirect URIs**, add:
   ```
   http://localhost:5678/rest/oauth2-credential/callback
   ```
   **IMPORTANT:** Use `localhost`, NOT the tunnel URL. This is stable and never
   changes, because the OAuth redirect happens in your browser.
6. Click **Create**
7. Copy the **Client ID** and **Client Secret** — save these!

### 3. Start the Stack

```bash
cd llamail
./start.sh
```

The script will output:
```
=== Ready! ===
n8n:        http://localhost:5678
Tunnel:     https://random-words.trycloudflare.com
```

### 4. Configure n8n (First Time)

1. Open **http://localhost:5678** in your browser
2. Create your admin account

### 5. Add Telegram Credentials in n8n

1. Go to **Credentials** (key icon in left sidebar)
2. Click **Add Credential**
3. Search for **Telegram**
4. Paste your bot token from step 1
5. Save

### 6. Add Gmail Credentials in n8n

1. Go to **Credentials** (key icon in left sidebar)
2. Click **Add Credential**
3. Search for **Gmail OAuth2 API**
4. Paste your **Client ID** and **Client Secret** from step 2d
5. The **OAuth Redirect URL** should show:
   ```
   http://localhost:5678/rest/oauth2-credential/callback
   ```
   This is controlled by the `N8N_EDITOR_BASE_URL` environment variable
   in docker-compose.yml. It must match what you set in Google Cloud.
6. In the **Scope** field, add:
   ```
   https://mail.google.com/
   ```
   Without this, you'll get "Missing required parameter: scope" error.
7. Click **Sign in with Google**
8. Google shows "Google hasn't verified this app" warning — this is normal:
   - Click **Advanced**
   - Click **Go to [App Name] (unsafe)**
   - This is safe — it's your own app running on your machine
9. Authorize access
10. Save the credential
11. Name it clearly (e.g., "Gmail - Personal")

**If the OAuth Redirect URL shows a tunnel URL instead of localhost:**
The `N8N_EDITOR_BASE_URL` env variable is missing. Ensure `docker-compose.yml` has:
```yaml
- N8N_EDITOR_BASE_URL=http://localhost:5678
```
Then recreate n8n: `docker compose up -d --force-recreate n8n`

### 7. Create a Telegram Workflow

1. Create a **New Workflow**
2. Add a **Telegram Trigger** node
3. Select your Telegram credential
4. Set "Trigger On" to **Message**
5. Add any nodes after it (e.g., Code node, HTTP Request, etc.)
6. **Publish** the workflow (toggle in top-right corner)

### 8. Verify Telegram Works

1. Send a message to your bot in Telegram
2. Check the **Executions** tab in n8n (left sidebar)
3. You should see the execution with the message data

**Note:** The workflow editor will show "Problem running workflow: Because of
limitations in Telegram Trigger, n8n can't listen for test executions at the
same time as listening for production ones." This is **normal** when the
workflow is published. Check the Executions tab, not the editor.

---

## Daily Usage

### Starting (After PC Reboot)

```bash
cd llamail
./start.sh
```

Then open n8n at http://localhost:5678 and **unpublish then publish** the
Telegram workflow so it re-registers the webhook with the new tunnel URL.

Gmail credentials are stored in the n8n Docker volume and persist across
restarts — no need to re-authenticate.

### Stopping

```bash
cd llamail
docker compose down
```

### Checking Status

```bash
# Are containers running?
docker compose ps

# n8n logs
docker logs n8n

# Tunnel logs
docker logs cloudflared

# Current tunnel URL
docker logs cloudflared 2>&1 | grep trycloudflare
```

---

## Docker Configuration

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
      - WEBHOOK_URL=${WEBHOOK_URL}                          # Set by start.sh
      - N8N_EDITOR_BASE_URL=http://localhost:5678           # For OAuth redirects
      - GENERIC_TIMEZONE=America/Chicago
      - TZ=America/Chicago
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

### Key Environment Variables

| Variable | Purpose | Value |
|----------|---------|-------|
| `WEBHOOK_URL` | Public HTTPS URL for Telegram webhooks | `https://xxx.trycloudflare.com/` (changes on restart) |
| `N8N_EDITOR_BASE_URL` | Base URL for OAuth redirects | `http://localhost:5678` (stable) |
| `N8N_PROTOCOL` | Protocol for webhook registration | `https` |
| `GENERIC_TIMEZONE` | Timezone for scheduled workflows | `America/Chicago` |

### .env File

```
N8N_BASIC_AUTH_USER=admin
N8N_BASIC_AUTH_PASSWORD=changeme
GENERIC_TIMEZONE=America/Chicago
TZ=America/Chicago
WEBHOOK_URL=https://xxx.trycloudflare.com/    # Updated by start.sh
```

### Docker Volumes

| Volume | Contains | Persists |
|--------|----------|----------|
| `n8n_data` | n8n credentials, workflows, settings, OAuth tokens | Yes |

Your n8n account, Gmail OAuth tokens, Telegram credentials, and workflows
are stored in `n8n_data`. They survive restarts and container recreations.

```bash
# List volumes
docker volume ls | grep n8n

# WARNING: This deletes all n8n data (credentials, workflows, etc.)
# docker volume rm llamail_n8n_data
```

---

## start.sh Script

The startup script automates the tunnel URL sync process:

```bash
#!/bin/bash
# 1. Stops old containers
# 2. Starts n8n + cloudflared
# 3. Waits for Cloudflare to assign a tunnel URL
# 4. Updates .env with the new URL
# 5. Recreates n8n to pick up the new URL
# 6. Deletes old Telegram webhook (n8n re-registers on publish)
```

After running `start.sh`, you only need to **unpublish then publish** the
Telegram workflow in n8n to complete the webhook registration.

---

## Telegram Webhook Details

### How Webhooks Work

1. When you **publish** a workflow with a Telegram Trigger, n8n calls the
   Telegram API to register a webhook URL
2. Telegram sends all bot messages to that URL via HTTPS POST
3. Cloudflare Tunnel forwards the request to your local n8n
4. n8n processes the message and runs the workflow

### Verifying Webhook Status

Open in browser (replace `BOT_TOKEN` with your token):
```
https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo
```

**Healthy response:**
```json
{
  "ok": true,
  "result": {
    "url": "https://xxx.trycloudflare.com/webhook/.../webhook",
    "pending_update_count": 0,
    "max_connections": 40,
    "allowed_updates": ["message"]
  }
}
```

**Signs of problems:**
- `"url": ""` — Webhook not registered. Unpublish and publish the workflow.
- `"pending_update_count"` high — Messages queued but not delivered.
- `"last_error_message"` present — Check the error message.

### Common Webhook Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `403 Forbidden` | ngrok free tier limit / interstitial | Switch to Cloudflare Tunnel |
| `530` | Cloudflare tunnel URL expired / stale | Run `start.sh` and republish workflow |
| `HTTPS URL must be provided` | `WEBHOOK_URL` not set or uses http | Check `.env` has https URL |
| `"url": ""` | Workflow not published | Publish the workflow in n8n |

### Manually Resetting Webhook

If the webhook gets stuck:

1. Delete: `https://api.telegram.org/bot<BOT_TOKEN>/deleteWebhook`
2. Unpublish the workflow in n8n
3. Publish it again
4. Verify with `getWebhookInfo`

---

## Gmail OAuth Details

### Why localhost for OAuth Redirect?

The OAuth redirect happens in **your browser**, not from Google's servers.
When you click "Sign in with Google" in n8n, this happens:

```
1. Browser opens Google login page
2. You authorize access
3. Google redirects YOUR BROWSER to the callback URL
4. Since your browser can reach localhost, it works!
```

The tunnel URL is NOT needed for OAuth — only for incoming webhooks
(Telegram sending messages to your bot).

### OAuth Redirect URI Must Match

The redirect URI in **Google Cloud Console** must exactly match what
n8n shows in the credential screen:

- Google Cloud: `http://localhost:5678/rest/oauth2-credential/callback`
- n8n shows: `http://localhost:5678/rest/oauth2-credential/callback`

If n8n shows a tunnel URL instead, add `N8N_EDITOR_BASE_URL=http://localhost:5678`
to docker-compose.yml and recreate: `docker compose up -d --force-recreate n8n`

### Important: Add Gmail Scopes

The **Scope** field in the n8n credential must not be empty. Add:
```
https://mail.google.com/
```
Without this, Google returns "Missing required parameter: scope" error.

### "Google hasn't verified this app" Warning

When signing in, Google shows a warning because your app hasn't been verified.
This is **normal and safe** for personal use — you own the app and it runs locally.

To proceed:
1. Click **Advanced** at the bottom of the warning page
2. Click **Go to [App Name] (unsafe)**

This is completely safe because:
- You are both the developer and the user
- The app runs locally on your machine
- OAuth tokens are stored in a Docker volume on your PC
- No third party has access

### One Google Cloud Project for All Accounts

You only need **one** Google Cloud project with one Client ID / Client Secret.
Use it for all your Gmail accounts. No need to create separate projects,
API keys, or OAuth credentials per account.

If your OAuth consent screen is in **Testing** mode, you must add each
Gmail address as a Test user. If it's in **Production** mode (100 user cap),
any Google account can authorize without being added as a test user.

### Adding Multiple Gmail Accounts

For each additional Gmail account:
1. In n8n: **Credentials > Add Credential > Gmail OAuth2 API**
2. Use the **same** Client ID / Client Secret
3. Add the scope: `https://mail.google.com/`
4. Click **Sign in with Google** and log in with that account
5. Click **Advanced > Go to app (unsafe)** on the warning page
6. Authorize access
7. Name the credential clearly (e.g., "Gmail - Work", "Gmail - Personal")

---

## Troubleshooting

### n8n won't start (port conflict)

```bash
# Find what's using port 5678
netstat -ano | findstr :5678

# Or change the port in docker-compose.yml:
ports:
  - "5679:5678"   # Use 5679 externally
```

### Container name conflict

```
Error: container name "/n8n" is already in use
```

```bash
docker rm -f n8n
docker rm -f cloudflared
docker compose up -d
```

### Tunnel URL not appearing in logs

```bash
# Wait a few seconds, then:
docker logs cloudflared 2>&1 | grep trycloudflare

# If nothing, check full logs:
docker logs cloudflared
```

### Messages not appearing in n8n Executions

1. Is the workflow **published**? (toggle top-right in workflow editor)
2. Is the tunnel running? `docker compose ps`
3. Is the webhook URL current? Check `getWebhookInfo`
4. Does the URL in `getWebhookInfo` match the current tunnel URL?
5. Send a new message after republishing

### "Cannot GET /" when opening localhost:5678

n8n is still starting. Wait 10-15 seconds and refresh.

### n8n shows old webhook URL after restart

`docker compose restart` does NOT re-read `.env`. Use:
```bash
docker compose up -d --force-recreate n8n
```

### Gmail OAuth shows tunnel URL instead of localhost

Add to docker-compose.yml environment:
```yaml
- N8N_EDITOR_BASE_URL=http://localhost:5678
```
Then: `docker compose up -d --force-recreate n8n`

### Gmail OAuth "redirect_uri_mismatch" error

The redirect URI in n8n doesn't match Google Cloud. Ensure both have:
```
http://localhost:5678/rest/oauth2-credential/callback
```

### Gmail OAuth "access_denied" error

Your Gmail address must be added as a **Test user** in Google Cloud:
1. Google Cloud Console > APIs & Services > OAuth consent screen
2. Under "Test users", add your Gmail address

---

## Tunnel Alternatives

### Option 1: ngrok (Static URL, Has Request Limits)

Free tier has monthly request limits and an interstitial page that can
block webhook delivery. Previously used but hit rate limits.

### Option 2: Cloudflare Named Tunnel (Permanent URL, Needs Account)

For a stable URL that doesn't change on restart:
1. Create a free Cloudflare account
2. Add a payment method (won't be charged)
3. Go to Zero Trust > Networks > Tunnels
4. Create a named tunnel, get token
5. Replace tunnel service in docker-compose:

```yaml
tunnel:
  image: cloudflare/cloudflared:latest
  command: tunnel --no-autoupdate run --token YOUR_TUNNEL_TOKEN
```

### Option 3: Own Domain + Let's Encrypt

For full control with your own domain. Requires a domain name,
reverse proxy (nginx/traefik), and SSL certificate setup.

---

## Lessons Learned

- **ngrok free tier** has request limits that can block Telegram webhooks (403 error)
- **Cloudflare quick tunnel** works but URL changes on every container restart
- **`docker compose restart`** does NOT re-read `.env` — use `--force-recreate`
- **OAuth redirects** use localhost (browser-based), not the tunnel URL
- **`N8N_EDITOR_BASE_URL`** controls the OAuth redirect URL shown in n8n
- **Telegram webhook test** won't work in editor when workflow is published — check Executions tab instead

---

## Quick Reference

```bash
# ============================================
# START (after reboot)
# ============================================
cd llamail
./start.sh
# Then: unpublish + publish Telegram workflow in n8n

# ============================================
# STOP
# ============================================
docker compose down

# ============================================
# CHECK STATUS
# ============================================
docker compose ps
docker logs n8n
docker logs cloudflared
docker logs cloudflared 2>&1 | grep trycloudflare

# ============================================
# RESTART n8n ONLY (keeps tunnel alive)
# ============================================
docker compose up -d --force-recreate n8n

# ============================================
# TELEGRAM WEBHOOK
# ============================================
# Check:  https://api.telegram.org/bot<TOKEN>/getWebhookInfo
# Delete: https://api.telegram.org/bot<TOKEN>/deleteWebhook

# ============================================
# NUCLEAR RESET (if everything is broken)
# ============================================
docker compose down
docker rm -f n8n cloudflared 2>/dev/null
./start.sh
# Then: republish Telegram workflow
```
