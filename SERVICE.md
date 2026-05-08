# Continuous Evaluation Service

Real-time news evaluation and automated trading service with web UI.

## Setup (target machine)

### Prerequisites

- Python 3.10+
- Node.js 18+ (for frontend)
- Docker/Vessel/Podman (for Redis)
- Ollama installed locally with models pulled

### Install

```bash
git clone <repo-url>
cd BatchTradingAgents

# Python dependencies
pip install -e .

# Frontend
cd frontend
npm install
npm run build
cd ..
```

### Configure

Edit `config/application.conf`:

```hocon
gpu {
  quick_model = "qwen3:8b"    # adjust to your pulled models
  deep_model = "qwen3:32b"
}

accounts {
  paper_main {
    api_key = ${ALPACA_API_KEY}
    api_secret = ${ALPACA_API_SECRET}
    is_paper = true
    strategy = "balanced"
    watchlist = "aggressive"
  }
}
```

Set environment variables:
```bash
export ALPACA_API_KEY=your_key
export ALPACA_API_SECRET=your_secret
```

### Run

```bash
# Start Redis
docker compose up -d

# Verify Ollama is running
ollama list

# Start service (API + GPU worker)
python -m service.main
```

Open `http://localhost:8000` for the web UI, or `http://localhost:8000/docs` for the API explorer.

### Development (frontend hot-reload)

```bash
# Terminal 1: backend
python -m service.main

# Terminal 2: frontend dev server (proxies /api to backend)
cd frontend
npm run dev
```

Then open `http://localhost:5173`.

## Architecture

```
compose.yaml: Redis only (Ollama runs natively)

python -m service.main:
  ├── FastAPI (port 8000) — API + WebSocket + serves frontend build
  └── GPU Worker (subprocess) — processes task queues via Ollama

Data flow:
  Alpaca News WS → quick screen (GPU) → escalate? → deep investigation (GPU)
    → sell signal? → emergency sell + full re-analysis (GPU)
    → merge debounce (5min) → merge + allocate (GPU)
```
