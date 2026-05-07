"""Watchlist pruning — recommends tickers to remove based on pipeline output."""

from typing import Optional

from cli.order_parser import AllocationPlan


def generate_prune_recommendations(
    llm,
    watchlist_tickers: list[str],
    allocation_plan: AllocationPlan,
    merge_report: str,
    ticker_ratings: dict[str, str],
    keep_tickers: set[str],
) -> list[tuple[str, str]]:
    """Run a single LLM call to identify tickers with no buying prospect this week.

    Returns list of (symbol, reason) tuples. Empty list means no removals.
    """
    # Build allocation summary
    alloc_lines = []
    for a in allocation_plan.allocations:
        alloc_lines.append(f"  - {a.symbol}: {a.action} → {a.pct:.1f}%")
    alloc_lines.append(f"  - CASH: {allocation_plan.cash_pct:.1f}%")
    allocation_summary = "\n".join(alloc_lines)

    # Build ratings summary
    ratings_lines = [f"  - {sym}: {rating}" for sym, rating in sorted(ticker_ratings.items())]
    ratings_str = "\n".join(ratings_lines)

    # Build keep list explanation
    keep_str = ", ".join(sorted(keep_tickers)) if keep_tickers else "(none)"

    prompt = f"""You are a watchlist maintenance assistant. Given the complete analysis pipeline output below, identify tickers that should be REMOVED from the active watchlist because they have no realistic buying prospect for the next 7 days.

**Criteria for removal:**
- Rated Sell or Underweight with no near-term catalyst that could reverse the rating
- Allocation plan assigns 0% or "sell" with full exit
- Thesis is broken (not just a temporary dip — fundamentals have deteriorated)
- No upcoming earnings, FDA decisions, or other catalysts within 7 days that could change the picture
- The ticker is in a structural downtrend with no technical reversal signal

**Criteria to KEEP (do NOT recommend removal):**
- Rated Hold or better — even if not currently allocated, it may rotate back in
- Has a known catalyst in the next 7 days (earnings, ex-dividend, conference, data release)
- Currently experiencing a dip that the analysis flagged as a potential entry opportunity
- Rated Sell but with a note that the situation is volatile and could reverse quickly

**NEVER recommend removing these tickers (currently held or pending buy):** {keep_str}

**Current Watchlist:** {', '.join(watchlist_tickers)}

**Allocation Plan:**
{allocation_summary}

**Allocation Reasoning:** {allocation_plan.reasoning}

**Merge Report (cross-ticker comparison):**
{merge_report}

**Per-Ticker Ratings:**
{ratings_str}

---

**Output format:**
For each ticker you recommend removing, output exactly one line:
SYMBOL — one-sentence reason for removal

If no tickers should be removed, output exactly: NONE"""

    response = llm.invoke(prompt)
    text = response.content.strip()

    if not text or text.upper() == "NONE":
        return []

    results = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.upper() == "NONE":
            continue
        if "—" in line:
            parts = line.split("—", 1)
        elif "-" in line:
            parts = line.split("-", 1)
        else:
            continue

        sym = parts[0].strip().upper()
        reason = parts[1].strip() if len(parts) > 1 else ""

        # Never recommend removing held/buy-order tickers
        if sym in keep_tickers:
            continue

        if sym and reason:
            results.append((sym, reason))

    return results
