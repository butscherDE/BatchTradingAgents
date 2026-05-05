"""Continuity modes for report generation.

- anchored: injects the previous report into the per-ticker analysis so the PM
  must explicitly justify deviations.
- reconcile: after generating a new merge report, runs a second pass comparing
  it to the previous merge and flags/corrects unjustified rating changes.
"""

from typing import Optional


def build_anchor_context(previous_final_decision: str, ticker: str) -> str:
    """Build past_context string from a previous report for anchored mode."""
    if not previous_final_decision:
        return ""

    lines = previous_final_decision.strip().splitlines()
    truncated = "\n".join(lines[:80])

    return (
        f"**Previous Analysis for {ticker}:**\n"
        f"{truncated}\n\n"
        f"If your current recommendation differs from the above, you MUST explicitly "
        f"state what new evidence or changed conditions justify the change. "
        f"Do not silently reverse a prior position — acknowledge it and explain why."
    )


def reconcile_merge_reports(
    llm,
    new_report: str,
    previous_report: str,
    ticker_results: list,
) -> str:
    """Compare a new merge report against the previous one and correct unjustified changes."""
    tickers_str = ", ".join(t for t, _, _ in ticker_results)

    prompt = f"""You are a portfolio continuity auditor. You have two versions of a cross-ticker comparison report for the same set of tickers: the PREVIOUS report (from an earlier run) and the NEW report (just generated with fresh data).

**Your job:**
1. Identify every rating change (e.g., NVDA Buy→Hold, AAPL Overweight→Buy).
2. For each change, determine whether it is JUSTIFIED by new evidence cited in the new report, or UNJUSTIFIED (appears to be LLM randomness / rephrasing without substantive new data).
3. Output a corrected final report that:
   - Keeps all justified changes (new evidence warrants the shift)
   - Reverts unjustified changes back to the previous rating
   - Adds a "### Changes from Prior Analysis" section at the end listing each change with a one-line justification or "reverted — no new evidence"

**Tickers under analysis:** {tickers_str}

---

**PREVIOUS REPORT:**

{previous_report}

---

**NEW REPORT:**

{new_report}

---

Output the corrected report in full (not just the diff). Preserve the structure and section headings of the new report. Only modify ratings/rankings where you found unjustified changes."""

    response = llm.invoke(prompt)
    return response.content
