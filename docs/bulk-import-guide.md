# Bulk Import — Setup & Code Guide

## What It Does

Bulk import pulls **existing historical emails** from a Gmail account via the Gmail API, runs each one through the LLM processing pipeline (summarize, categorize, embed), and stores them in SQLite + ChromaDB.

This is different from the real-time pipeline where n8n catches new incoming emails. Bulk import handles the backlog.

## Architecture Flow

```
curl POST /import
       │
       ▼
routes/imports.py ─── start_import()
       │
       ├── import_coordinator.create_job()
       │       │
       │       ├── gmail_client.get_gmail_service()  ── OAuth2 auth (first time: browser, after: token.json)
       │       ├── gmail_client.list_message_ids()   ── get Gmail IDs (paginated)
       │       ├── Create ImportJob row in SQLite
       │       ├── Check for duplicates (skip already-imported IDs)
       │       └── Create ImportTask rows (one per email)
       │
       └── Spawn background thread ── import_worker.run_job()
                                           │
                                           ▼ (loop per task)
                                      gmail_client.fetch_email()      ── full email content via Gmail API
                                           │
                                           ▼
                                      email_processor.process_email() ── same pipeline as real-time
                                           │
                                           ├── token_budget.needs_chunking()
                                           ├── LLM summarize (llama.cpp)
                                           ├── embeddings.store() (ChromaDB)
                                           └── _save_email() (SQLite)
```

## File Reference

| File | Purpose |
|------|---------|
| `services/gmail_client.py` | OAuth2 authentication + Gmail API calls (list, fetch, parse) |
| `services/import_coordinator.py` | Job creation, email discovery, deduplication, job status |
| `services/import_worker.py` | Background thread that processes tasks one by one with retries |
| `routes/imports.py` | API endpoints: start, status, pause |
| `models/database.py` | `ImportJob` + `ImportTask` SQLAlchemy models |
| `config.py` | Gmail credentials path, batch size, max retries |
| `credentials/client_secret.json` | Google OAuth client ID + secret (you create this) |
| `credentials/token.json` | Auto-generated after first OAuth flow (reused forever) |

All paths relative to `webservice/src/email_service/` unless noted.

---

## Setup Steps

### 1. Dependencies

From `webservice/`:

```bash
source ../.venv/Scripts/activate    # Windows (Git Bash / MINGW)
# source ../.venv/bin/activate      # Linux / Mac
pip install -e .
```

This installs `google-api-python-client` and `google-auth-oauthlib` (listed in `pyproject.toml`).

### 2. Create credentials directory

```bash
mkdir -p webservice/credentials
```

### 3. Create client_secret.json

You need a Google Cloud OAuth 2.0 Client ID. If you already have one (e.g., for n8n), reuse it.

Create `webservice/credentials/client_secret.json`:

```json
{
  "web": {
    "client_id": "YOUR_CLIENT_ID",
    "client_secret": "YOUR_CLIENT_SECRET",
    "project_id": "YOUR_PROJECT_ID",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "redirect_uris": ["http://localhost:9090/"]
  }
}
```

Get the values from Google Cloud Console > APIs & Services > Credentials > your OAuth Client.

### 4. Add redirect URI in Google Cloud Console

Go to your OAuth Client in Google Cloud Console and add this to **Authorized redirect URIs**:

```
http://localhost:9090/
```

**Important:** trailing slash is required — Google does exact string matching.

Note: it can take 5 minutes to a few hours for Google to propagate the change.

### 5. Port conflicts (Windows)

Docker Desktop often grabs common ports. The OAuth flow uses port 9090 for a one-time browser callback. If 9090 is taken:

```
netstat -ano | findstr :9090
```

If occupied, change the port in `gmail_client.py` line 30 (`flow.run_local_server(port=XXXX)`) and update the redirect URI in both `client_secret.json` and Google Cloud Console.

The webservice itself runs on port 8000 (set via CLI flag or `config.py`). Port 8080 is typically taken by Docker Desktop on Windows.

---

## Running the Import

### Start the webservice

```bash
cd webservice
python -m uvicorn email_service.main:app --host 0.0.0.0 --port 8000
```

Make sure llama.cpp servers are running:
```bash
# LLM server on :11434, embedding server on :11435
./start_llama_servers.sh
```

### First run (OAuth)

```bash
curl -X POST "http://127.0.0.1:8000/import?account_id=YOUR_EMAIL@gmail.com&max_emails=5"
```

The uvicorn terminal will print a Google auth URL. Open it in your browser, sign in, authorize. A `credentials/token.json` file gets saved — **this is a one-time thing**.

### Subsequent runs (no browser)

```bash
curl -X POST "http://127.0.0.1:8000/import?account_id=YOUR_EMAIL@gmail.com&max_emails=100"
```

Token is reused automatically. No browser needed unless the token expires and can't refresh.

### Monitor progress

```bash
curl http://127.0.0.1:8000/import/{JOB_ID}
```

Response example:
```json
{
  "job_id": 2,
  "account_id": "you@gmail.com",
  "status": "completed",
  "total_emails": 5,
  "processed_count": 1,
  "failed_count": 0,
  "skipped_count": 4,
  "created_at": "2026-02-11T07:20:02",
  "started_at": "2026-02-11T07:20:02",
  "finished_at": "2026-02-11T07:20:12"
}
```

### Pause a running import

```bash
curl -X POST http://127.0.0.1:8000/import/{JOB_ID}/pause
```

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/import?account_id=X&max_emails=N` | Start import job. Default max_emails=500 |
| `GET` | `/import/{job_id}` | Get job status + counts |
| `POST` | `/import/{job_id}/pause` | Pause a running job |

---

## How the Code Works

### OAuth Flow (`gmail_client.py`)

```
get_gmail_service()
    │
    ├── token.json exists? ── load credentials
    │       │
    │       ├── valid? ── return Gmail service
    │       └── expired? ── refresh using refresh_token ── save updated token.json
    │
    └── no token.json? ── start OAuth flow
            │
            ├── Read client_secret.json
            ├── Start local HTTP server on port 9090
            ├── Print auth URL (user opens in browser)
            ├── Browser redirects to localhost:9090 with auth code
            ├── Exchange code for access + refresh tokens
            ├── Save to token.json
            └── Return Gmail service
```

### Email Discovery (`import_coordinator.py`)

1. Calls `gmail_client.list_message_ids()` — paginates through Gmail API, returns list of Gmail message IDs
2. Creates an `ImportJob` row (status: pending)
3. Checks which IDs are already in `ImportTask` table (deduplication)
4. Creates `ImportTask` rows only for new IDs
5. Returns the job

### Worker Loop (`import_worker.py`)

Runs in a **background thread** (not async — llama.cpp is sync, no benefit):

```
loop:
    1. Check if job is paused → stop
    2. Pick next pending task
    3. No tasks left? → mark job completed, stop
    4. Fetch full email from Gmail API
    5. Build ProcessEmailRequest
    6. Call email_processor.process_email() (same as real-time pipeline)
    7. Update task status to "done"
    8. Increment job.processed_count
    9. On error: retry up to 3 times, then mark as "failed"
```

### Deduplication

- `ImportTask.gmail_id` is checked against existing tasks
- `Email.id` uses `session.merge()` (upsert) — if the email was already processed via the real-time pipeline, it gets updated, not duplicated
- ChromaDB uses `collection.upsert()` — same dedup for embeddings

### Error Handling

- Each task retries up to `settings.import_max_retries` (default: 3)
- After max retries: task marked as `failed`, `failed_count` incremented
- One failed email doesn't stop the whole job

---

## Timing Estimates

- Each email takes ~4-5 seconds (LLM processing via llama.cpp)
- 50 emails ≈ 4 minutes
- 500 emails ≈ 40 minutes
- Processing is sequential (one email at a time) to avoid overwhelming the GPU

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `redirect_uri_mismatch` | Exact URI match required in Google Cloud Console. Include trailing slash: `http://localhost:9090/` |
| `OSError: [WinError 10048]` port in use | Kill leftover Python processes: `taskkill /PID <pid> /F`. Check with `netstat -ano \| findstr :<port>` |
| `DetachedInstanceError` | SQLAlchemy session closed before accessing model attributes. Save values to local variables before `session.close()` |
| `404 page not found` (plain text) | Something else on that port (likely Docker). Use `127.0.0.1` instead of `localhost`. Change port if needed |
| `file_cache is only supported with oauth2client<4.0.0` | Harmless warning from Google API client. Can be ignored |
| Token expired | Delete `credentials/token.json` and re-run. Browser auth will trigger again |

---

## Config Reference (`config.py`)

```python
gmail_credentials_path: Path = Path("credentials/client_secret.json")
gmail_token_path: Path = Path("credentials/token.json")
gmail_scopes: list[str] = ["https://mail.google.com/", "https://www.googleapis.com/auth/gmail.settings.basic"]
import_batch_size: int = 50      # Gmail API pagination size
import_max_retries: int = 3      # retries per failed email
```

All paths are relative to where uvicorn is started (should be `webservice/`).
