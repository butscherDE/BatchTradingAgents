# Batch & Paper Trading CLI Reference

All commands are run via `uv run python -m cli.main` (or `tradingagents` if installed).

---

## `batch` — Multi-Ticker Analysis

Analyze multiple tickers sequentially, generate per-ticker reports and a cross-ticker comparison.

### Basic Usage

```bash
# Analyze two tickers with defaults (ollama, medium depth)
tradingagents batch AAPL NVDA

# Custom provider and models
tradingagents batch AAPL NVDA MSFT -p openai --quick-model gpt-4o-mini --deep-model gpt-4o

# Shallow analysis, German output, custom output directory
tradingagents batch AAPL NVDA --depth shallow -l German -o ./my_reports

# Set a specific analysis date
tradingagents batch AAPL NVDA -d 2026-04-28
```

### Portfolio Import

Pass an existing brokerage portfolio to give the merge report holdings context for rebalancing advice.

```bash
# E*Trade CSV export (auto-detected)
tradingagents batch --portfolio ~/Downloads/PortfolioDownload.csv

# Generic CSV (two columns: ticker,quantity — optional CASH row)
tradingagents batch --portfolio positions.csv

# Force format detection
tradingagents batch --portfolio data.csv --portfolio-format etrade

# Inline positions (repeatable)
tradingagents batch --position AAPL:100 --position NVDA:50 --cash 25000

# Combine CSV + inline (inline positions are merged into the CSV holdings)
tradingagents batch --portfolio ~/Downloads/PortfolioDownload.csv --position AAPL:765
```

When `--portfolio` or `--position` is provided, tickers are optional — they are inferred from holdings. Explicit tickers are added on top of portfolio holdings.

#### Generic CSV Format

```csv
ticker,quantity
AAPL,100
NVDA,50
CASH,20000
```

### Merge-Only Mode

Skip analysis entirely and generate a cross-ticker comparison from existing reports on disk.

```bash
# Merge latest reports for these tickers
tradingagents batch AAPL NVDA --merge-only

# Merge with portfolio context
tradingagents batch --portfolio ~/Downloads/PortfolioDownload.csv --merge-only

# Skip the merge report (analysis only, no comparison)
tradingagents batch AAPL NVDA --no-merge
```

### Investment Strategy

Control risk appetite for the merge report and capital allocation.

```bash
# Conservative — favor blue-chips, limit speculative positions to ≤5%
tradingagents batch AAPL NVDA NUVL -s conservative

# Balanced (default) — mix stability and growth, 10-20% in higher-risk names
tradingagents batch AAPL NVDA NUVL

# Aggressive — overweight high-risk/high-reward, speculative names can be 30%+
tradingagents batch AAPL NVDA NUVL -s aggressive
```

### All Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `TICKERS` | | *(from portfolio)* | Ticker symbols to analyze |
| `--output-dir` | `-o` | `./reports` | Report output directory |
| `--date` | `-d` | today | Analysis date (YYYY-MM-DD) |
| `--depth` | | `medium` | Research depth: `shallow`, `medium`, `deep` |
| `--provider` | `-p` | `ollama` | LLM provider |
| `--quick-model` | | `qwen3:8b` | Model for quick thinking |
| `--deep-model` | | `qwen3:32b` | Model for deep thinking |
| `--language` | `-l` | `English` | Output language |
| `--no-merge` | | `false` | Skip the cross-ticker merge report |
| `--merge-only` | | `false` | Skip analysis; merge existing reports |
| `--portfolio` | | | CSV file with holdings (E\*Trade or generic) |
| `--position` | | | Inline position as `TICKER:QUANTITY` (repeatable) |
| `--cash` | | `0.0` | Cash available for allocation |
| `--portfolio-format` | | *(auto)* | Force format: `etrade` or `generic` |
| `--strategy` | `-s` | `balanced` | Risk strategy: `conservative`, `balanced`, `aggressive` |

---

## `paper` — Alpaca Auto-Trading

Connect to an Alpaca brokerage account, analyze all portfolio holdings (plus optional extra tickers), generate a comparison report, and automatically execute the recommended trades.

### Setup

Set credentials in `.env`:

```bash
ALPACA_API_KEY=your_key
ALPACA_API_SECRET=your_secret
```

Or pass them as flags: `--key YOUR_KEY --secret YOUR_SECRET`

### Basic Usage

```bash
# Analyze current portfolio holdings and execute trades (paper account)
tradingagents paper

# Also consider buying into new tickers
tradingagents paper MSFT GOOGL NVDA

# Use a specific LLM provider
tradingagents paper -p openai --deep-model gpt-4o NVDA
```

### Dry Run

Show proposed trades without submitting orders.

```bash
# Full analysis, show trade plan, don't execute
tradingagents paper --dry-run NVDA

# Skip analysis too — fastest way to see what it would do
tradingagents paper --dry-run --skip-analysis
```

### Skip Analysis

Use existing reports from the output directory instead of running the full analysis pipeline. Useful for fast re-runs after a previous `batch` or `paper` run.

```bash
# Load existing reports and execute trades
tradingagents paper --skip-analysis

# Load reports + dry run (no analysis, no execution)
tradingagents paper --dry-run --skip-analysis NVDA
```

Tickers without an existing report are skipped with a warning.

### Live Trading

```bash
# REAL MONEY — use with caution
tradingagents paper --live NVDA
```

A bold red warning is displayed before proceeding.

### Investment Strategy

Same strategy flag as `batch` — controls risk appetite for the merge report and order sizing.

```bash
# Aggressive — will size into high-risk tickers more heavily
tradingagents paper --dry-run --skip-analysis -s aggressive NUVL CYTK

# Conservative — favor stable positions, limit speculative exposure
tradingagents paper --dry-run --skip-analysis -s conservative
```

### Tax-Aware Selling

The bot computes tax implications for each holding based on cost basis and holding period (short-term vs long-term capital gains). This is factored into both the merge report and trade recommendations.

```bash
# Top tax bracket (default) — 37% short-term, 20% long-term
tradingagents paper --dry-run --skip-analysis --tax-bracket top

# Mid bracket — 24% short-term, 15% long-term
tradingagents paper --dry-run --skip-analysis --tax-bracket mid

# Low bracket — 12% short-term, 0% long-term
tradingagents paper --dry-run --skip-analysis --tax-bracket low

# Disable tax awareness
tradingagents paper --dry-run --skip-analysis --tax-bracket none
```

Holding period is determined from Alpaca order history (earliest filled buy order per symbol).

### All Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `TICKERS` | | | Extra tickers to consider (portfolio tickers always included) |
| `--key` | | `ALPACA_API_KEY` env | Alpaca API key |
| `--secret` | | `ALPACA_API_SECRET` env | Alpaca API secret |
| `--live` | | `false` | Use live trading (default: paper) |
| `--output-dir` | `-o` | `./reports` | Report output directory |
| `--depth` | | `medium` | Research depth: `shallow`, `medium`, `deep` |
| `--provider` | `-p` | `ollama` | LLM provider |
| `--quick-model` | | `qwen3:8b` | Model for quick thinking |
| `--deep-model` | | `qwen3:32b` | Model for deep thinking |
| `--language` | `-l` | `English` | Output language |
| `--dry-run` | | `false` | Show trades but do not submit orders |
| `--auto-execute` | | `false` | Execute orders immediately without confirmation |
| `--skip-analysis` | | `false` | Use existing reports instead of running analysis |
| `--strategy` | `-s` | `balanced` | Risk strategy: `conservative`, `balanced`, `aggressive` |
| `--tax-bracket` | | `top` | Tax bracket: `top`, `mid`, `low`, `none` |

---

## Report Directory Structure

After a run, `--output-dir` (default `./reports`) contains:

```
reports/
  AAPL_2026-05-03/
    complete_report.md
    1_analysts/
      market.md
      sentiment.md
      news.md
      fundamentals.md
    2_research/
      bull.md, bear.md, manager.md
    3_trading/
      trader.md
    4_risk/
      aggressive.md, conservative.md, neutral.md
    5_portfolio/
      decision.md
  NVDA_2026-05-03/
    ...
  _comparison/
    merge_report.md
  _trades/                          # paper command only
    trade_log_20260503_141500.md
```
