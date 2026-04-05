# llamail

Your private email agent, running locally. Control your inbox from Telegram — RAG search, drafts, campaigns, and a persistent synthetic persona. Runs on consumer hardware. Zero cloud, zero monthly bills.

## Architecture

- **n8n** — forwards Telegram messages to/from webservice, triggers on new Gmail
- **Webservice** — all smart logic: command parsing, LLM calls, search, drafts
- **llama.cpp** — two servers: Llama 3.1 8B (chat) + Nomic v2 MoE (embeddings)
- **SQLite** — emails, import jobs, chat history, drafts, campaigns, scheduled sends, FTS5 full-text index
- **ChromaDB** — vector embeddings for semantic search
- **Cloudflare Tunnel** — exposes n8n for Telegram webhook (free, no request limits)

## Prerequisites

- Docker + Docker Compose
- llama.cpp built with GPU support (CUDA, Vulkan, or Metal)
- Python 3.11+
- Google Cloud project with Gmail API enabled
- Telegram bot (via @BotFather)

## Setup

### 1. Environment

```bash
# Copy and fill in your secrets
cp .env.example .env
```

Key variables in `.env` (root, for Docker):
- `WEBHOOK_URL` — Cloudflare Tunnel URL (auto-updated by `start.sh`)
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `N8N_EDITOR_BASE_URL=http://localhost:5678`

Key variables in `webservice/.env` (prefix `EMAIL_`):
- `EMAIL_TELEGRAM_BOT_TOKEN` — same token, used by the webservice to send "Analyzing..." notifications
- `EMAIL_TELEGRAM_CHAT_ID` — your Telegram chat ID (for notifications)
- `EMAIL_DEFAULT_ACCOUNT` — default Gmail address for commands that need an account

### 2. Start infrastructure

```bash
# Start n8n + Cloudflare Tunnel
./start.sh

# Start llama.cpp servers (LLM on :11434, embeddings on :11435)
./start_llama_servers.sh
```

### 3. Start webservice

```bash
cd webservice
pip install -e .
python -m uvicorn email_service.main:app --host 0.0.0.0 --port 8000
```

### 4. Gmail OAuth (one-time)

Place your Google OAuth credentials:
- `webservice/credentials/client_secret.json`
- First import triggers browser OAuth flow, saves `token.json` automatically

Google Cloud Console must have:
- Gmail API enabled
- OAuth consent screen configured
- Redirect URI: `http://localhost:9090/`

### 5. n8n workflows

Set up two workflows manually in n8n:

**Telegram command workflow:**
```
Telegram Trigger --> HTTP POST http://host.docker.internal:8000/telegram/command
                     body: {text, chat_id}
                 --> Telegram Send Message: {{ $json.reply }}
```

**Gmail live pipeline:**
```
Gmail Trigger (Simplify OFF) --> Code node (flatten fields)
    --> HTTP POST http://host.docker.internal:8000/process-email
    --> Telegram notification with summary
```

## Telegram Commands

All interaction happens through the Telegram bot. Three-tier command routing:

1. **`/slash` commands** — instant dispatch, no LLM needed (~100ms)
2. **Bare compound commands** (`import start`, `draft reply`, `campaign create`, `schedule at`) — instant dispatch, no LLM needed (~100ms). These always have subcommands so they can't be confused with natural language.
3. **Natural language** ("find emails about Q2 budget") — shows "Analyzing your message..." notification instantly, then LLM classifies intent (~4-5s)

**Tip:** Type `/` in Telegram to see the autocomplete menu with all available commands (configured via BotFather `/setcommands`).

### Email Search & Q&A

| Command | Description | Example |
|---------|-------------|---------|
| `search (query)` | Hybrid search (semantic + keyword) across all emails | `search Q2 budget report` |
| `ask (question)` | RAG Q&A — finds relevant emails, answers with context | `ask What did John say about the deadline?` |
| `recent [count]` | Show latest processed emails (default 5, max 20) | `recent 10` |
| `show (number)` | Display full email body (uses number from search/recent) | `show 1` |
| `delete (number)` | Trash email in Gmail + remove from local DB and search index | `delete 3` |
| `block (number)` | Block sender — creates Gmail filter to auto-trash future emails | `block 3` |
| `unsubscribe (number)` | Unsubscribe from mailing list (auto-sends or gives link) | `unsubscribe 2` |

**Follow-up questions work** — the bot remembers recent conversation (sliding window, last 10 exchanges).

### Drafting & Sending

| Command | Description | Example |
|---------|-------------|---------|
| `draft reply (number) (instructions)` | Reply to an email from last search/recent | `draft reply 1 agree but suggest Thursday` |
| `draft new (recipient) (instructions)` | Compose a new email from scratch | `draft new john@example.com ask about project deadline` |
| `send [draft_id]` | Send last draft, or a specific draft by ID | `send` or `send 3` |
| `grammar (text)` | Proofread and fix grammar/spelling/style | `grammar I wants to meeting on tuesday` |

**Typical flow:**
1. `search meeting` or `recent` — results show as `[1]`, `[2]`, `[3]`...
2. `show 1` — read the full email
3. `draft reply 1 agree but suggest Thursday` — generates reply with draft ID `(#1)`
4. `send` — sends the draft via Gmail

- You provide **what** to say, the LLM writes **how** to say it
- Number aliases from the last `search` or `recent` — no need to copy long email IDs
- Full email IDs also accepted if you have one
- Drafts are saved to the database with a unique ID — they persist across sessions
- Reply drafts include original email context (sender, subject, body)
- Reply drafts preserve Gmail thread ID — replies appear in the same conversation
- New email drafts auto-detect recipient name from past emails
- Double-send protection — `send` on an already-sent draft returns "already sent"

### Import Management

| Command | Description | Example |
|---------|-------------|---------|
| `import start (account) [count\|all]` | Start bulk Gmail import | `import start user@gmail.com 100` |
| `import pause (account)` | Pause running import | `import pause user@gmail.com` |
| `import resume (account)` | Resume paused/completed import + reset failed emails | `import resume user@gmail.com` |
| `import status` | Show progress for all accounts | `import status` |
| `import history (account)` | Show all past import jobs | `import history user@gmail.com` |

**Worker health check** — if llama.cpp servers go down during import, the worker auto-pauses the job instead of burning retries. Use `import resume` once servers are back.

### Campaigns

| Command | Description | Example |
|---------|-------------|---------|
| `campaign create (name) (template) [subject]` | Create a new campaign from a template | `campaign create demo cover_letter.txt Role at {company_name}` |
| `campaign load (name) (csv_file)` | Load recipients from a CSV file | `campaign load demo recipients.csv` |
| `campaign personalize (name)` | LLM personalizes each email using recipient context | `campaign personalize demo` |
| `campaign preview (name) [count]` | Preview personalized emails | `campaign preview demo 2` |
| `campaign start (name)` | Start sending (throttled to avoid Gmail rate limits) | `campaign start demo` |
| `campaign pause (name)` | Pause a running campaign | `campaign pause demo` |
| `campaign resume (name)` | Resume a paused campaign | `campaign resume demo` |
| `campaign status` | Show all campaigns with progress and reply stats | `campaign status` |
| `campaign results (name)` | Show detailed results with reply classifications | `campaign results demo` |

**How it works:**
- Template + CSV → LLM rewrites each email uniquely (not mail-merge — actual content rewriting based on company context)
- Sending is throttled (`campaign_send_rate` emails/hour) to avoid Gmail rate limits
- Reply tracking is automatic — when a reply arrives via Gmail trigger, the system matches it by thread ID, LLM classifies it as `interview`, `rejection`, `follow_up`, `automated`, or `ghosted`, and sends a Telegram push notification for interviews and follow-ups

### Scheduled Sends

| Command | Description | Example |
|---------|-------------|---------|
| `send [draft_id] at (time)` | Schedule a draft to send at a specific time | `send at 14:30` or `send 9 at 14:30` |
| `send [draft_id] in (duration)` | Schedule a draft to send after a delay | `send in 2h` or `send in 30m` or `send in 1h30m` |
| `schedule list` | Show all pending scheduled sends | `schedule list` |
| `schedule cancel (draft_id)` | Cancel a scheduled send, restore to draft | `schedule cancel 9` |

A background scheduler thread checks for due sends every 30 seconds. You get a Telegram notification when the scheduled email is sent.

### Other

| Command | Description |
|---------|-------------|
| `accounts` | Show connected Gmail accounts with email counts |
| `help` | List all available commands |

### Natural Language

You don't have to use exact commands. Free-form text goes through LLM intent classification:

- "find emails about Q2 budget" --> `search`
- "what did John say about Monday?" --> `ask`
- "show me last 3 emails" --> `recent`
- "fix my grammar: I wants to meeting" --> `grammar`
- "write a reply to 1 saying we agree" --> `draft reply`
- "send it" / "send the draft" --> `send`
- "delete that spam" / "trash email 2" --> `delete`
- "block this sender" / "stop emails from them" --> `block`
- "unsubscribe from this newsletter" --> `unsubscribe`
- "how's my campaign doing?" --> `campaign status`
- "hey" / "thanks" --> chitchat (Sable synthetic persona)

**Note:** Compound commands like `import start`, `draft reply`, `campaign create`, and `schedule at` bypass the LLM entirely — they're matched instantly by the first word. You'll see "Analyzing your message..." only for true natural language that needs LLM classification.

## Email Processing Pipeline

When a new email arrives (via Gmail trigger or bulk import):

1. **Token check** — if body > 3500 tokens, split into chunks
2. **LLM summarization** — extracts summary, category, priority, sentiment, action items, key people
3. **Embedding** — generates vector embedding via Nomic v2 MoE
4. **Storage** — saves to SQLite (structured data + FTS5 index) and ChromaDB (vectors)
5. **Gmail link** — generates `rfc822msgid:` search link for opening the email in Gmail

Single emails process in ~4s. Chunked emails: ~4s per chunk + master summary pass.

### Categories
`work | personal | newsletter | notification | finance | social | spam | other`

### Priority
`high | medium | low`

### Sentiment
`positive | negative | neutral | urgent`

## Search Architecture

Hybrid search combines two strategies:

- **Semantic search** (ChromaDB) — understands meaning, weight 0.6
- **Keyword search** (SQLite FTS5) — exact term matching, weight 0.4

Results are merged, deduplicated, scored, and enriched with full email metadata.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health (LLM, database, ChromaDB) |
| `POST` | `/process-email` | Process a single email through the pipeline |
| `POST` | `/telegram/command` | Handle Telegram bot command |
| `POST` | `/import` | Start bulk import |
| `GET` | `/import/{job_id}` | Check import job status |
| `POST` | `/import/{job_id}/pause` | Pause import job |

## Configuration

All settings in `webservice/src/email_service/config.py` via environment variables (prefix `EMAIL_`):

| Setting | Default | Description |
|---------|---------|-------------|
| `llm_url` | `http://localhost:11434` | llama.cpp chat server |
| `embed_url` | `http://localhost:11435` | llama.cpp embedding server |
| `db_path` | `data/emails.db` | SQLite database path |
| `chroma_path` | `data/chroma` | ChromaDB storage path |
| `max_context_tokens` | 8192 | LLM context window |
| `chunk_threshold` | 3500 | Tokens before chunking kicks in |
| `chat_history_limit` | 10 | Conversation memory window |
| `chat_history_token_budget` | 2000 | Max tokens for conversation context |
| `import_batch_size` | 50 | Emails per import batch |
| `import_max_retries` | 3 | Retries per failed import task |
| `scheduler_check_interval` | 30 | Seconds between scheduled-send polls |
| `campaigns_dir` | `campaigns` | Directory for campaign templates and CSVs |
| `campaign_send_rate` | 50 | Campaign emails per hour |
| `campaign_check_interval` | 30 | Seconds between campaign send-loop polls |
| `telegram_bot_token` | *(required)* | Telegram bot token (for "Analyzing..." notifications) |
| `telegram_chat_id` | *(required)* | Your Telegram chat ID |
| `default_account` | *(required)* | Default Gmail address for commands |

## Project Structure

```
webservice/src/email_service/
  config.py                  # Pydantic Settings
  main.py                    # FastAPI app + lifespan + stale job recovery
  dependencies.py            # get_db() provider
  models/
    database.py              # SQLAlchemy ORM (Email, EmailChunk, ImportJob, ImportTask, ChatMessage, Draft, Campaign, CampaignRecipient)
    schemas.py               # Pydantic request/response models
  routes/
    health.py                # GET /health
    process.py               # POST /process-email
    imports.py               # Import job endpoints
    telegram.py              # POST /telegram/command
  services/
    llm.py                   # llama.cpp client (generate + embed + health check)
    embeddings.py            # ChromaDB store/search
    email_processor.py       # Summarization pipeline
    chunker.py               # Text splitting with overlap
    token_budget.py          # tiktoken count/truncate
    search.py                # Hybrid search (semantic + FTS5)
    chat_memory.py           # Conversation history (SQLite)
    gmail_client.py          # Gmail API auth + fetch + send
    import_coordinator.py    # Import job management
    import_worker.py         # Background import thread + health check auto-pause
    telegram_handler.py      # Command dispatcher + three-tier routing
    telegram_notifier.py     # Sends "Analyzing..." feedback via Telegram Bot API
    handler_state.py         # Shared state (_last_results, _last_draft_id)
    utils.py                 # Shared helpers (parse_json)
    cmd_email.py             # Email commands: search, ask, recent, show, delete, block, unsubscribe
    cmd_draft.py             # Draft commands: draft reply/new, send, grammar, schedule
    cmd_import.py            # Import commands: start, pause, resume, status, history
    cmd_campaign.py          # Campaign commands: create, load, personalize, preview, start, pause, resume, status, replies
    campaign_engine.py       # Campaign business logic (personalize, load CSV, reply classification)
    campaign_sender.py       # Background campaign send thread (throttled)
    send_scheduler.py        # Background scheduled-send thread
  templates/
    summarize.j2             # Single email summarization
    summarize_chunk.j2       # Per-chunk summarization
    summarize_master.j2      # Combine chunk summaries
    classify_intent.j2       # NL intent classification
    ask.j2                   # RAG Q&A prompt
    chitchat.j2              # Sable persona prompt
    draft_reply.j2           # Reply draft generation
    draft_new.j2             # New email composition
    grammar.j2               # Grammar/spelling correction
    personalize.j2           # Campaign email personalization
    classify_reply.j2        # Campaign reply classification
```

## Maintenance

```bash
# Check database stats
sqlite3 webservice/data/emails.db "SELECT category, count(*) FROM emails GROUP BY category;"

# Rebuild FTS5 index (if out of sync)
python -c "from email_service.models.database import init_db, rebuild_fts; init_db(); rebuild_fts()"

# Reset everything
sqlite3 webservice/data/emails.db "DELETE FROM emails; DELETE FROM email_chunks; DELETE FROM import_jobs; DELETE FROM import_tasks; DELETE FROM chat_messages;"
```

## Ports

| Service | Port |
|---------|------|
| n8n | 5678 |
| FastAPI webservice | 8000 |
| llama.cpp LLM | 11434 |
| llama.cpp embeddings | 11435 |
| OAuth redirect (one-time) | 9090 |

---

The default persona is Sable: a cold, synthetic voice built specifically for this project.

*Code handcrafted, not vibecoded.*
