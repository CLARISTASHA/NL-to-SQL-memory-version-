# Task Report Agent

A natural language to SQL query agent that converts plain English questions into MySQL queries against the `user_task` table and returns summarized results with optional charts.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Setup Instructions](#setup-instructions)
3. [Running the Application](#running-the-application)
4. [Docker Deployment](#docker-deployment)
5. [Bare Metal Deployment (systemd)](#bare-metal-deployment-systemd)
6. [How to Update Schema When DB Changes](#how-to-update-schema-when-db-changes)
7. [How to Add New Examples to Schema](#how-to-add-new-examples-to-schema)
8. [How to Improve the Prompt](#how-to-improve-the-prompt)
9. [API Reference](#api-reference)
10. [Monitoring & Logs](#monitoring--logs)
11. [Known Limitations](#known-limitations)

---

## Architecture

```
User Question
     │
     ▼
resolve_question_with_history()   ← fills in references from conversation memory
     │
     ▼
generate_sql()                    ← FAISS retrieves schema context, LLM generates SQL
     │
     ▼
extract_sql_and_explanation()     ← parses LLM output with 3-stage fallback
     │
     ▼
force_reporting_table()           ← ensures correct table name (user_task)
     │
     ▼
sanitize_sql()                    ← blocks dangerous statements & unknown tables
     │
     ▼
execute_sql()                     ← runs query against MySQL
     │
     ▼
analyze_and_summarize()           ← returns clean summary string
     │
     ▼
generate_chart()                  ← optional base64 PNG chart
```

**Stack:**
- **LLM:** phi3:mini via Ollama (runs locally, no API key needed)
- **Embeddings:** phi3:mini via OllamaEmbeddings
- **Vector store:** FAISS (schema_index/ folder)
- **API:** FastAPI + Uvicorn
- **Database:** MySQL (remote)

---

## Setup Instructions

### Prerequisites

- Python 3.11+
- MySQL access (credentials in `.env`)
- Ollama installed: `curl -fsSL https://ollama.com/install.sh | sh`

### 1. Clone and enter the project

```bash
cd /opt/task_report_agent
```

### 2. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your real database credentials
nano .env
```

### 4. Pull the LLM model

```bash
ollama pull phi3:mini
```

### 5. Extract schema and build vector store

```bash
python extract_schema.py
python build_schema_vectorstore.py
```

This creates `schema_final.txt` and the `schema_index/` folder.

### 6. Test the setup

```bash
python task_report_agent.py
# Type: how many tasks assigned to hari
```

---

## Running the Application

### Interactive CLI mode

```bash
source venv/bin/activate
python task_report_agent.py
```

### FastAPI server mode

```bash
source venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000
```

API docs available at: `http://localhost:8000/docs`

### Run tests

```bash
python tests/run_tests.py
```

---

## Docker Deployment

### Build and start

```bash
cp .env.example .env
# Fill in .env with real credentials

docker-compose up -d --build
```

### Check status

```bash
docker-compose ps
docker-compose logs -f
```

### Stop

```bash
docker-compose down
```

### Notes

- Ollama model is persisted in a Docker volume (`ollama_models`) so it is not re-downloaded on restart.
- Logs are written to `./logs/` on the host.
- First startup takes ~2 minutes while phi3:mini downloads (~2GB).

---

## Bare Metal Deployment (systemd)

Use this for direct server deployment without Docker.

### 1. Copy files to server

```bash
scp -r . ubuntu@your-server:/opt/task_report_agent
```

### 2. Install systemd services

```bash
sudo cp systemd/ollama.service /etc/systemd/system/
sudo cp systemd/task-report-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 3. Enable and start services

```bash
# Start Ollama first
sudo systemctl enable ollama
sudo systemctl start ollama

# Pull model
ollama pull phi3:mini

# Start the app
sudo systemctl enable task-report-agent
sudo systemctl start task-report-agent
```

### 4. Check service status

```bash
sudo systemctl status ollama
sudo systemctl status task-report-agent

# View live logs
journalctl -u task-report-agent -f
journalctl -u ollama -f
```

### 5. Set up log rotation

```bash
sudo cp logrotate.conf /etc/logrotate.d/task-report-agent
sudo mkdir -p /var/log/task_report_agent
sudo chown ubuntu:ubuntu /var/log/task_report_agent
```

---

## How to Update Schema When DB Changes

Run this whenever a table is added, a column is renamed, or the DB structure changes.

### Automatic (recommended)

```bash
chmod +x scripts/update_schema.sh
./scripts/update_schema.sh
```

This script runs all 3 steps and restarts the service automatically.

### Manual steps

```bash
# Step 1: Re-extract schema from the database
python extract_schema.py
# Output: schema_final.txt, schema_raw.txt

# Step 2: Rebuild FAISS vector store
python build_schema_vectorstore.py
# Output: schema_index/ folder (replaces old one)

# Step 3: Restart the app to load the new index
sudo systemctl restart task-report-agent
# OR for Docker:
docker-compose restart
```

### What these files do

| File | Purpose |
|---|---|
| `extract_schema.py` | Connects to MySQL, reads table/column definitions, writes `schema_final.txt` |
| `build_schema_vectorstore.py` | Reads `schema_final.txt`, creates embeddings, saves FAISS index to `schema_index/` |
| `schema_index/` | FAISS vector store loaded at app startup — must be present |

---

## How to Add New Examples to Schema

Adding examples improves how the LLM understands table relationships and column meanings.

### Step 1: Edit schema_final.txt

Open `schema_final.txt` and add examples under the relevant table section:

```
Table: user_task
Columns: id, company_id, assigned_to, assigned_by, priority, status, due_date, ...

Examples:
- "tasks assigned to John" → assigned_to = 'John'
- "tasks assigned by Suresh" → assigned_by = 'Suresh'
- "high priority tasks" → priority = 'High'
- "overdue tasks" → due_date < NOW() AND status != 'completed'
```

### Step 2: Rebuild the vector store

```bash
python build_schema_vectorstore.py
```

### Step 3: Restart the app

```bash
sudo systemctl restart task-report-agent
```

### Tips for good examples

- Cover both `assigned_to` and `assigned_by` — the model often confuses them without examples
- Add examples for every status value your DB uses (`Open`, `completed`, `In-Progress`, etc.)
- Add examples for common date filters (`this week`, `overdue`, `due today`)

---

## How to Improve the Prompt

The prompt lives in `task_report_agent.py` inside the `sql_prompt` variable.

### Golden rule: examples beat rules

`phi3:mini` follows concrete Q→A examples far more reliably than abstract rules. When adding a constraint, always add an example alongside it.

**Less effective:**
```
NEVER use LIKE for assigned_to
```

**More effective:**
```
Q: show tasks assigned to hari
A: SELECT * FROM user_task WHERE assigned_to = 'hari' LIMIT 100;
```

### Step-by-step: adding a new pattern

1. Identify the question pattern that generates wrong SQL (check test output)
2. Write the correct SQL manually
3. Add a Q→A example pair to the EXAMPLES section of the prompt
4. Run `python tests/run_tests.py` to verify it passes
5. Add the new question to `tests/test_questions.py` so it is tested every time

### Prompt structure

```
[Column reference block]   ← exact column names and their meanings
[Strict rules]             ← numbered constraints
[Examples]                 ← Q→A pairs — most important section
[Format instruction]       ← tells LLM how to structure output
[Schema]                   ← injected at runtime from FAISS retrieval
[Question]                 ← injected at runtime
```

### What to do when SQL is wrong

1. Run `python tests/run_tests.py` and note the generated SQL
2. Check: wrong column? wrong table? missing filter? LIKE instead of exact match?
3. Add a prompt example for that exact question pattern
4. If the model still ignores it, move the example higher in the prompt (earlier = higher weight)

---

## API Reference

### POST /query

Submit a natural language question.

**Request:**
```json
{
  "question": "how many tasks assigned to hari",
  "session_id": "optional-string"
}
```

**Response:**
```json
{
  "session_id": "abc-123",
  "question": "how many tasks assigned to hari",
  "resolved_question": "how many tasks assigned to hari",
  "sql": "SELECT COUNT(*) FROM user_task WHERE assigned_to = 'hari';",
  "explanation": "Counts all tasks assigned to hari.",
  "summary": "Total count: 128.",
  "row_count": 1,
  "chart_uri": null,
  "duration_ms": 1840.5
}
```

### GET /health

Returns service health status.

```json
{
  "status": "ok",
  "timestamp": "2025-06-01T10:00:00",
  "ollama": "ok",
  "version": "1.0.0"
}
```

### POST /reset

Clears conversation history.

```json
{ "status": "ok", "message": "Conversation history cleared." }
```

---

## Monitoring & Logs

### Log files

| File | Contents |
|---|---|
| `/var/log/task_report_agent/app.log` | FastAPI request logs, SQL generated, row counts, errors |
| `/var/log/task_report_agent/ollama.log` | Ollama model server logs |

### Log format

```
2025-06-01 10:00:01 [INFO] GET /health status=200 duration=12.3ms
2025-06-01 10:00:05 [INFO] [abc-123] Question: how many tasks assigned to hari
2025-06-01 10:00:06 [INFO] [abc-123] SQL: SELECT COUNT(*) FROM user_task WHERE assigned_to = 'hari';
2025-06-01 10:00:07 [INFO] [abc-123] rows=1 duration=1840.5ms
```

### Check service health

```bash
curl http://localhost:8000/health
```

### Log rotation

Logs rotate daily, kept for 14 days, compressed after 1 day. Configured in `/etc/logrotate.d/task-report-agent`.

---

## Known Limitations

| Limitation | Detail | Workaround |
|---|---|---|
| Single table only | Agent only queries `user_task`. JOIN to other tables is not supported | Add schema examples if a specific JOIN is always needed |
| No authentication | The `/query` endpoint has no auth | Place behind a reverse proxy (nginx) with IP allowlist or API key header |
| Model accuracy | `phi3:mini` occasionally misinterprets complex multi-condition questions | Add a Q→A example for the failing pattern to the prompt |
| Conversation history is global | All requests in one process share the same history | Pass a `session_id` and handle per-session history in a future version |
| No streaming | Full response is returned at once after LLM completes | Acceptable for internal tools; add SSE streaming if needed for UI |
| Cold start time | First request after startup is slow (~5s) while LLM loads into memory | Send a warm-up request on startup (e.g., `GET /health` triggers LLM ping) |
| Case sensitivity | Names like `hari` vs `Hari` are treated differently by MySQL | Normalize names to title case before inserting into DB, or add `LOWER()` to queries |


Run this after clone:
python build_schema_vectorstore.py