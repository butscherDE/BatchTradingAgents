# Batch & Paper Trading CLI Reference

All commands are run via `uv run python -m cli.main` (or `tradingagents` if installed).

---

## Watchlists

Manage ticker lists in `watchlists.toml` (searched at `./watchlists.toml` then `~/.tradingagents/watchlists.toml`).

```toml
[conservative]
tickers = [
    "VTI",
    "BND",
    "VNQ",
    "AAPL",
    "MSFT",
]

[aggressive]
extends = "conservative"   # inherits all conservative tickers
tickers = [                # adds these on top
    "NVDA",
    "TQQQ",
    "SOXL",
]
exclude = ["BND"]          # removes BND from the inherited set

[yolo]
extends = "aggressive"
tickers = ["NUVL", "CYTK", "ARKK"]
exclude = ["VTI", "VNQ"]
```

Use `--watchlist` / `-w` with any command:

```bash
# Use a named watchlist
tradingagents batch -w yolo
tradingagents paper -w aggressive
tradingagents check -w conservative

# Watchlist + extra tickers on top
tradingagents paper -w yolo SILJ FCX

# Custom file location
tradingagents batch -w yolo --watchlist-file ~/my_lists.toml
```

Comment out tickers with `#` to temporarily exclude them:

```toml
[yolo]
tickers = [
    "NVDA",
    # "DFTX",   # delisted
    "NUVL",
]
```

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

### Reuse Today's Reports

Only run analysis for tickers that don't already have a report from today. Tickers with a fresh report are loaded from disk.

```bash
# Analyze 5 tickers — if AAPL and NVDA already ran today, skip them
tradingagents batch AAPL NVDA MSFT GOOGL AVGO --reuse-today

# Combine with portfolio — only analyze what's new
tradingagents batch --portfolio ~/Downloads/PortfolioDownload.csv --reuse-today
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

# YOLO — all-in on growth plays, exit stable ETFs/blue-chips, diversify across speculative sectors
tradingagents batch NUVL CYTK NVDA ARKK -s yolo

# Mean — exploit geopolitical chaos, deregulation, fossil resurgence, inequality
tradingagents batch LMT RTX XOM GEO PLTR -s mean
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
| `--reuse-today` | | `false` | Skip analysis for tickers with a report from today |
| `--merge-checks` | | `0` | Validation passes on the merge report |
| `--continuity` | | `none` | Report continuity: `none`, `anchored`, `reconcile` |
| `--watchlist` | `-w` | | Named watchlist section from `watchlists.toml` |
| `--watchlist-file` | | *(auto)* | Path to watchlists TOML file |
| `--portfolio` | | | CSV file with holdings (E\*Trade or generic) |
| `--position` | | | Inline position as `TICKER:QUANTITY` (repeatable) |
| `--cash` | | `0.0` | Cash available for allocation |
| `--portfolio-format` | | *(auto)* | Force format: `etrade` or `generic` |
| `--strategy` | `-s` | `balanced` | Risk strategy: `conservative`, `balanced`, `aggressive`, `yolo`, `mean` |

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
tradingagents paper --dry-run -u skip
```

### Update Strategy

Controls how much analysis to run before trading. Set with `--update-strategy` (or `-t`).

| Strategy | Behavior |
|----------|----------|
| `skip` | Load existing reports only — no analysis |
| `numeric` | Check prices; only re-analyze tickers with stop-loss/concentration alerts |
| `headlines` | + fetch news; re-analyze if thesis invalidated |
| `escalate` | + re-analyze all yellow and red flagged tickers |
| `full` | Re-analyze everything (default) |

```bash
# Use existing reports (fastest)
tradingagents paper -u skip

# Only re-analyze tickers that hit price alerts
tradingagents paper -t numeric

# Check news + re-analyze if thesis broken
tradingagents paper -u headlines

# Re-analyze anything flagged yellow or red
tradingagents paper -u escalate

# Full re-analysis (default)
tradingagents paper -u full
```

Tickers without an existing report are skipped with a warning.

### Reuse Last Merge Report

Skip the merge report step entirely and reuse the most recent `_comparison/merge_report.md` from the output directory. The command fails fast if no merge report exists. Combine with `-u skip` to re-run trade allocation against an already-produced merge without re-doing per-ticker analyses.

```bash
# Reuse last merge — still runs analyses, but skips the merge generation step
tradingagents paper --reuse-merge

# Fastest re-run: skip analysis and reuse the last merge to recompute allocation only
tradingagents paper -u skip --reuse-merge

# Pair with --dry-run to inspect the trade plan without submitting orders
tradingagents paper -u skip --reuse-merge --dry-run
```

When `--reuse-merge` is set, `--merge-checks` is ignored.

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
tradingagents paper --dry-run -u skip -s aggressive NUVL CYTK

# Conservative — favor stable positions, limit speculative exposure
tradingagents paper --dry-run -u skip -s conservative

# YOLO — exit ETFs/blue-chips, go all-in on speculative growth plays
tradingagents paper --dry-run -u skip -s yolo NUVL CYTK

# Mean — exploit geopolitical tension, fossil resurgence, deregulation
tradingagents paper --dry-run -u skip -s mean LMT XOM GEO
```

### Stop-Loss Guidance

By default, the merge report and allocation step receive position performance data (entry price, current price, drawdown %) with strategy-calibrated stop-loss instructions. Thresholds adapt to the strategy:

| Strategy | Warning | Critical | Behavior |
|----------|---------|----------|----------|
| conservative | -15% | -25% | Strong sell signal; exit at critical |
| balanced | -20% | -35% | Sell signal unless thesis justifies holding |
| aggressive | -35% | -50% | Only flag if thesis appears broken |
| yolo | -50% | -70% | Context only; drawdowns may be add opportunities |
| mean | -30% | -45% | Review if policy catalyst weakened; exit if reversed |

```bash
# Disable stop-loss guidance entirely
tradingagents paper --no-stop-loss
```

### Tax-Aware Selling

The bot computes tax implications for each holding based on cost basis and holding period (short-term vs long-term capital gains). This is factored into both the merge report and trade recommendations.

```bash
# Top tax bracket (default) — 37% short-term, 20% long-term
tradingagents paper --dry-run -u skip --tax-bracket top

# Mid bracket — 24% short-term, 15% long-term
tradingagents paper --dry-run -u skip --tax-bracket mid

# Low bracket — 12% short-term, 0% long-term
tradingagents paper --dry-run -u skip --tax-bracket low

# Disable tax awareness
tradingagents paper --dry-run -u skip --tax-bracket none
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
| `--update-strategy` | `-u` | `full` | Analysis depth: `skip`, `numeric`, `headlines`, `escalate`, `full` |
| `--reuse-today` | | `false` | Skip analysis for tickers with a report from today |
| `--reuse-merge` | | `false` | Skip merge report generation; reuse last saved merge report (fails if none) |
| `--merge-checks` | | `0` | Validation passes on the merge report |
| `--allocation-checks` | | `0` | Validation passes on the allocation plan |
| `--strategy` | `-s` | `balanced` | Risk strategy: `conservative`, `balanced`, `aggressive`, `yolo`, `mean` |
| `--tax-bracket` | | `top` | Tax bracket: `top`, `mid`, `low`, `none` |
| `--no-stop-loss` | | `false` | Disable position drawdown guidance in merge/allocation prompts |
| `--continuity` | | `none` | Report continuity: `none`, `anchored`, `reconcile` |
| `--watchlist` | `-w` | | Named watchlist section from `watchlists.toml` |
| `--watchlist-file` | | *(auto)* | Path to watchlists TOML file |
| `--reuse-alloc` | | `false` | Skip allocation; reuse last saved plan (fails if none) |
| `--prune-watchlist` | | `false` | Recommend tickers to remove from watchlist after allocation |

---

## Report Continuity

By default each run is independent — the LLM does not see previous reports. The `--continuity` flag enables cross-run consistency:

### `--continuity anchored`

The previous per-ticker report (most recent from a different date) is injected into the Portfolio Manager prompt. The PM must explicitly justify any rating change relative to the prior analysis. Prevents silent reversals due to LLM randomness.

```bash
# Re-analyze but anchor against yesterday's reports
tradingagents batch AAPL NVDA MSFT --continuity anchored

# Paper trading with anchored continuity
tradingagents paper --continuity anchored
```

### `--continuity reconcile`

Per-ticker analysis runs freely (no anchoring bias), then after the merge report is generated, a reconciliation pass compares the new merge to the previous one. It identifies each rating change, determines whether new evidence justifies it, and reverts unjustified changes. Adds a "Changes from Prior Analysis" section to the final report.

```bash
# Generate fresh analysis, then reconcile merge against previous
tradingagents batch AAPL NVDA MSFT --continuity reconcile

# Combine with validation passes
tradingagents paper --continuity reconcile --merge-checks 1
```

Both modes require a previous report to exist in the output directory. If no prior report is found, the run proceeds normally without continuity enforcement.

---

## `check` — Lightweight Health Check

Quick portfolio sanity check without running the full analysis pipeline. Connects to Alpaca, pulls current prices, and validates against existing reports.

### Tiers

| Update Strategy | What it does | Cost |
|------|-------------|------|
| `numeric` | Price-based checks only: stop-loss, intraday drop, concentration, portfolio drawdown | Free (API only) |
| `headlines` | numeric + fetch news headlines + quick LLM thesis validation per ticker | ~1 cheap LLM call per ticker |
| `escalate` | headlines + full re-analysis for any ticker flagged red | Full pipeline cost per flagged ticker |
| `full` | headlines + full re-analysis for ALL tickers regardless of alerts | Full pipeline cost for entire portfolio |

### Usage

```bash
# Quick numeric check — runs in seconds, no LLM
tradingagents check

# Include news headline validation
tradingagents check -u headlines

# Auto-escalate: re-analyze anything that flags red
tradingagents check -u escalate

# Full: check everything, then re-analyze all positions
tradingagents check -u full

# Check specific tickers (in addition to portfolio holdings)
tradingagents check NUVL CYTK -u headlines

# Cron-friendly: exit code 0 = all clear, 1 = red alerts
tradingagents check --quiet

# Use aggressive thresholds (more tolerant of drawdowns)
tradingagents check -s aggressive

# With custom LLM for headline validation
tradingagents check -u headlines -p openai --quick-model gpt-4o-mini
```

### Numeric Checks

- **Stop-loss:** Drawdown from entry price vs strategy thresholds (same as `--no-stop-loss` feature)
- **Intraday crash:** >5% drop since today's open (warning), >10% (critical)
- **Concentration:** Single position >35% of portfolio (warning), >50% (critical)
- **Portfolio drawdown:** Total value drop since last check >5% (warning), >10% (critical)

### Headline Validation

For each ticker with a previous report on disk, fetches recent news via Alpaca News API and asks a quick LLM: "Does any headline invalidate the investment thesis?" Only flags as red if a headline represents a material change (earnings miss, regulatory rejection, fraud, etc.).

### All Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `TICKERS` | | *(from portfolio)* | Extra tickers to check |
| `--key` | | `ALPACA_API_KEY` env | Alpaca API key |
| `--secret` | | `ALPACA_API_SECRET` env | Alpaca API secret |
| `--live` | | `false` | Use live trading account |
| `--output-dir` | `-o` | `./reports` | Directory with existing reports |
| `--strategy` | `-s` | `balanced` | Strategy for stop-loss thresholds |
| `--update-strategy` | `-u` | `numeric` | Check depth: `numeric`, `headlines`, `escalate`, `full` |
| `--provider` | `-p` | `ollama` | LLM provider (headlines/escalate) |
| `--quick-model` | | `qwen3:8b` | Model for headline validation |
| `--deep-model` | | `qwen3:32b` | Model for escalated re-analysis |
| `--quiet` | `-q` | `false` | Minimal output; exit 0=clear, 1=alerts |
| `--watchlist` | `-w` | | Named watchlist section from `watchlists.toml` |
| `--watchlist-file` | | *(auto)* | Path to watchlists TOML file |

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
  _allocations/                      # paper command
    allocation_plan.json
    trade_plan.json
  _trades/                          # paper command only
    trade_log_20260503_141500.md
  _check/                           # check command state
    last_value.txt
```
