# Project: BatchTradingAgents

## Remote Server (Windows)

The continuous evaluation service runs on a Windows machine at `10.0.0.14`.

**SSH access:**
```
ssh -i ~/.ssh/key_butschpc danie@10.0.0.14
```

**REST API (direct from this machine):**
```
http://10.0.0.14:8000
```

**Key endpoints:**
- `GET /api/health` — service status, queue depths, uptime
- `GET /api/logs?limit=50` — last N log lines from the service
- `GET /api/news?limit=100&status=queued` — news articles (filterable)
- `GET /api/tasks?limit=50&status=completed` — GPU tasks (filterable)
- `GET /api/tasks/stats` — queue depths, worker state, model info
- `GET /api/watchlist?account_id=paper_main` — watchlist for account
- `GET /api/proposals?status=pending` — pending trade proposals
- `GET /api/accounts` — all configured accounts
- `GET /api/accounts/{id}/holdings` — positions with P/L
- `POST /api/news` — inject test article `{"headline": "...", "symbols": ["AAPL"]}`
- `POST /api/watchlist` — add ticker `{"account_id": "...", "symbol": "..."}`
- `DELETE /api/watchlist/{symbol}?account_id=...` — remove ticker
- `POST /api/proposals/{id}/approve` — execute proposed trades
- `POST /api/proposals/{id}/reject` — reject proposal
- `GET /api/watchlist/search?q=...` — ticker autocomplete search
- Full Swagger docs at `http://10.0.0.14:8000/docs`

**Run remote commands via SSH:**
```
ssh -i ~/.ssh/key_butschpc danie@10.0.0.14 "command here"
```

**Notes:**
- Windows machine — use `cmd /c "..."` or PowerShell syntax for Windows-specific commands
- Python is at the default PATH location
- The repo is at `C:\Users\danie\repos\BatchTradingAgents`
- The service runs via `python -m service.main` from that directory
- Redis runs via Docker on the same machine
- Ollama runs natively on the same machine (GPU attached)
- SQLite DB at `C:\Users\danie\repos\BatchTradingAgents\data\service.db`

**Common operations:**
```bash
# Check service logs
curl -s "http://10.0.0.14:8000/api/logs?limit=20" | python3 -m json.tool

# Check task stats
curl -s "http://10.0.0.14:8000/api/tasks/stats" | python3 -m json.tool

# Pull latest code on server
ssh -i ~/.ssh/key_butschpc danie@10.0.0.14 "cd C:\Users\danie\repos\BatchTradingAgents && git pull"

# Restart service (stop + start)
ssh -i ~/.ssh/key_butschpc danie@10.0.0.14 "taskkill /F /IM python.exe & cd C:\Users\danie\repos\BatchTradingAgents && start /B python -m service.main"

# Backfill news from yfinance (pulls latest news for all watchlist tickers into DB)
ssh -i ~/.ssh/key_butschpc danie@10.0.0.14 "cd C:\Users\danie\repos\BatchTradingAgents && python -m service.tools backfill"

# Backfill with date filter (only news after a specific date)
ssh -i ~/.ssh/key_butschpc danie@10.0.0.14 "cd C:\Users\danie\repos\BatchTradingAgents && python -m service.tools backfill --since 2026-05-07"

# Replay articles through GPU pipeline (resubmits queued articles to GPU worker)
# Requires service to be running
ssh -i ~/.ssh/key_butschpc danie@10.0.0.14 "cd C:\Users\danie\repos\BatchTradingAgents && python -m service.tools replay --since 2026-05-08T00:00:00"

# Delete DB and start fresh (stop service first)
ssh -i ~/.ssh/key_butschpc danie@10.0.0.14 "cd C:\Users\danie\repos\BatchTradingAgents && del data\service.db"
```
