"""Lightweight portfolio health check (no full analysis pipeline).

Tiers:
- numeric: price-based checks only (stop-loss, intraday drop, concentration, portfolio drawdown)
- headlines: numeric + fetch recent news + quick LLM thesis validation
- escalate: headlines + re-run full analysis for flagged tickers and regenerate merge
"""

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cli.position_risk import STRATEGY_THRESHOLDS


@dataclass
class TickerAlert:
    symbol: str
    level: str  # "green", "yellow", "red"
    reasons: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    alerts: list[TickerAlert] = field(default_factory=list)
    portfolio_alerts: list[str] = field(default_factory=list)
    has_red: bool = False
    has_yellow: bool = False

    def add_alert(self, symbol: str, level: str, reason: str):
        existing = next((a for a in self.alerts if a.symbol == symbol), None)
        if existing is None:
            existing = TickerAlert(symbol=symbol, level=level)
            self.alerts.append(existing)
        existing.reasons.append(reason)
        if level == "red" or (existing.level != "red" and level == "yellow"):
            existing.level = level
        if level == "red":
            self.has_red = True
        if level == "yellow":
            self.has_yellow = True


def run_numeric_checks(
    position_details: dict[str, dict],
    current_prices: dict[str, float],
    portfolio_value: float,
    previous_portfolio_value: Optional[float],
    strategy: str = "balanced",
    concentration_warn_pct: float = 35.0,
    concentration_crit_pct: float = 50.0,
    intraday_warn_pct: float = 5.0,
    intraday_crit_pct: float = 10.0,
    portfolio_drawdown_warn_pct: float = 5.0,
    portfolio_drawdown_crit_pct: float = 10.0,
) -> CheckResult:
    result = CheckResult()
    thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
    stop_warn = thresholds["warning_pct"]
    stop_crit = thresholds["critical_pct"]

    for sym, details in position_details.items():
        entry = details.get("avg_entry_price")
        price = current_prices.get(sym)
        if entry is None or price is None or entry <= 0:
            continue

        # Stop-loss check
        change_pct = ((price - entry) / entry) * 100
        if change_pct <= -stop_crit:
            result.add_alert(sym, "red", f"stop-loss CRITICAL: {change_pct:+.1f}% from entry (threshold: -{stop_crit}%)")
        elif change_pct <= -stop_warn:
            result.add_alert(sym, "yellow", f"stop-loss WARNING: {change_pct:+.1f}% from entry (threshold: -{stop_warn}%)")

        # Intraday drop (if we have today's open)
        today_open = details.get("today_open")
        if today_open and today_open > 0:
            intraday_change = ((price - today_open) / today_open) * 100
            if intraday_change <= -intraday_crit_pct:
                result.add_alert(sym, "red", f"intraday crash: {intraday_change:+.1f}% today")
            elif intraday_change <= -intraday_warn_pct:
                result.add_alert(sym, "yellow", f"intraday drop: {intraday_change:+.1f}% today")

        # Concentration check
        if portfolio_value > 0:
            qty = details.get("qty", 0)
            position_value = qty * price
            concentration = (position_value / portfolio_value) * 100
            if concentration >= concentration_crit_pct:
                result.add_alert(sym, "red", f"concentration CRITICAL: {concentration:.1f}% of portfolio")
            elif concentration >= concentration_warn_pct:
                result.add_alert(sym, "yellow", f"concentration WARNING: {concentration:.1f}% of portfolio")

    # Portfolio-level drawdown
    if previous_portfolio_value and previous_portfolio_value > 0 and portfolio_value > 0:
        portfolio_change = ((portfolio_value - previous_portfolio_value) / previous_portfolio_value) * 100
        if portfolio_change <= -portfolio_drawdown_crit_pct:
            result.portfolio_alerts.append(f"Portfolio drawdown CRITICAL: {portfolio_change:+.1f}% since last check")
            result.has_red = True
        elif portfolio_change <= -portfolio_drawdown_warn_pct:
            result.portfolio_alerts.append(f"Portfolio drawdown WARNING: {portfolio_change:+.1f}% since last check")
            result.has_yellow = True

    # Mark tickers with no alerts as green
    for sym in position_details:
        if not any(a.symbol == sym for a in result.alerts):
            result.alerts.append(TickerAlert(symbol=sym, level="green"))

    return result


def fetch_news_headlines(api_key: str, api_secret: str, symbols: list[str], limit: int = 5) -> dict[str, list[str]]:
    """Fetch recent news headlines per symbol via Alpaca News API."""
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest

    client = NewsClient(api_key, api_secret)

    headlines: dict[str, list[str]] = {}
    for sym in symbols:
        try:
            request = NewsRequest(symbols=sym, limit=limit)
            news = client.get_news(request)
            headlines[sym] = [article.headline for article in news.news]
        except Exception:
            headlines[sym] = []

    return headlines


def validate_thesis_against_news(
    llm,
    ticker: str,
    previous_thesis: str,
    headlines: list[str],
) -> tuple[bool, str]:
    """Quick LLM call: does the news invalidate the prior thesis?

    Returns (invalidated: bool, explanation: str).
    """
    if not headlines:
        return False, "No recent news"

    headlines_str = "\n".join(f"  - {h}" for h in headlines)

    prompt = f"""You are a portfolio risk monitor. Given the previous investment thesis and recent news headlines, determine if any headline materially invalidates the thesis.

**Ticker:** {ticker}

**Previous thesis:**
{previous_thesis}

**Recent headlines:**
{headlines_str}

Answer in this exact format:
INVALIDATED: YES or NO
REASON: one sentence explanation

Only answer YES if a headline represents a material change that directly contradicts or undermines the thesis (earnings miss, regulatory rejection, fraud allegation, bankruptcy, loss of key contract, etc.). General market noise is NOT invalidation."""

    response = llm.invoke(prompt)
    text = response.content.strip()

    invalidated = "INVALIDATED: YES" in text.upper()
    reason_line = ""
    for line in text.splitlines():
        if line.upper().startswith("REASON:"):
            reason_line = line.split(":", 1)[1].strip()
            break

    return invalidated, reason_line or text[:100]


def extract_thesis_oneliner(final_trade_decision: str) -> str:
    """Extract a short thesis from the PM decision for headline comparison."""
    lines = final_trade_decision.strip().splitlines()
    # Look for executive summary or first substantive paragraph
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "executive summary" in stripped.lower() or "summary" in stripped.lower():
            # Grab the next non-empty lines
            summary_parts = []
            for j in range(i + 1, min(i + 4, len(lines))):
                s = lines[j].strip()
                if not s or s.startswith("**"):
                    break
                summary_parts.append(s)
            if summary_parts:
                return " ".join(summary_parts)[:300]
    # Fallback: first 3 non-header lines
    content_lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("**")]
    return " ".join(content_lines[:3])[:300]
