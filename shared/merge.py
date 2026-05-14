"""Shared merge report generation and validation — no Rich/CLI dependencies."""

from tradingagents.llm_clients import create_llm_client


FULL_REPORT_TICKER_LIMIT = 10


def _get_llm(config: dict):
    """Create a deep-thinking LLM from config."""
    llm_kwargs = {}
    provider = config.get("llm_provider", "").lower()

    if config.get("api_key"):
        llm_kwargs["api_key"] = config["api_key"]

    if provider == "google" and config.get("google_thinking_level"):
        llm_kwargs["thinking_level"] = config["google_thinking_level"]
    elif provider == "openai" and config.get("openai_reasoning_effort"):
        llm_kwargs["reasoning_effort"] = config["openai_reasoning_effort"]
    elif provider == "anthropic" and config.get("anthropic_effort"):
        llm_kwargs["effort"] = config["anthropic_effort"]

    if config.get("llm_timeout"):
        llm_kwargs["timeout"] = config["llm_timeout"]

    client = create_llm_client(
        provider=config["llm_provider"],
        model=config["deep_think_llm"],
        base_url=config.get("backend_url"),
        **llm_kwargs,
    )
    return client.get_llm()


def build_ticker_section(ticker: str, decision: str, final_state: dict, include_analyst_reports: bool) -> str:
    parts = [f"#### {ticker} — Rating: {decision}"]

    if include_analyst_reports:
        for label, key in [
            ("Market Analysis", "market_report"),
            ("Social Sentiment", "sentiment_report"),
            ("News Analysis", "news_report"),
            ("Fundamentals", "fundamentals_report"),
        ]:
            report = final_state.get(key, "").strip()
            if report:
                parts.append(f"**{label}:**\n{report}")

    ftd = final_state.get("final_trade_decision", "(no decision available)")
    parts.append(f"**Portfolio Manager Decision:**\n{ftd}")

    return "\n\n".join(parts)


def generate_merge_report(
    ticker_results: list[tuple[str, str, dict]],
    config: dict,
    portfolio: dict | None = None,
    strategy: str | None = None,
    tax_summaries: dict | None = None,
    risk_context: str = "",
) -> str:
    """Generate a cross-ticker comparison report.

    ticker_results: list of (ticker, decision, final_state) tuples
    """
    llm = _get_llm(config)

    include_analyst_reports = len(ticker_results) <= FULL_REPORT_TICKER_LIMIT

    ticker_sections = [
        build_ticker_section(ticker, decision, final_state, include_analyst_reports)
        for ticker, decision, final_state in ticker_results
    ]
    ticker_decisions = "\n\n---\n\n".join(ticker_sections)

    if portfolio:
        holdings_lines = []
        for sym, qty in portfolio["holdings"].items():
            line = f"  - {sym}: {qty} shares"
            if tax_summaries and sym in tax_summaries:
                line += f" ({tax_summaries[sym]})"
            holdings_lines.append(line)
        portfolio_context = (
            f"**Current Portfolio:**\n" + "\n".join(holdings_lines) + "\n"
            f"  - Cash available: ${portfolio['cash']:,.2f}\n"
        )
        allocation_instruction = (
            "Recommend concrete rebalancing actions given the current holdings and "
            "available cash. Specify which positions to increase, decrease, or exit, "
            "and how to deploy available cash."
        )
    else:
        portfolio_context = (
            "**Portfolio Context:** No existing holdings. "
            "Assume fresh capital to be allocated across these tickers.\n"
        )
        allocation_instruction = (
            "Recommend a percentage allocation of capital across these tickers. "
            "Allocations must sum to 100% (cash is a valid allocation). "
            "Justify the weighting relative to each ticker's rating and risk profile."
        )

    lang = config.get("output_language", "English")
    language_instruction = "" if lang.strip().lower() == "english" else f" Write your entire response in {lang}."

    strategy_instruction = ""
    if strategy:
        strategy_instruction = f"**Investment Strategy:** {strategy}\nApply this risk profile when ranking tickers and recommending capital allocation.\n"

    risk_context_block = f"{risk_context}\n" if risk_context else ""

    report_type = "full analysis reports" if include_analyst_reports else "Portfolio Manager decisions"

    prompt = f"""You are a Chief Investment Officer. You have received {report_type} for {len(ticker_results)} tickers. Each ticker was analyzed by a team of market, sentiment, news, and fundamentals analysts, followed by research debate, trading, and risk management — culminating in a Portfolio Manager decision.

**Rating Scale Reference:**
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Individual Ticker Analyses:**

{ticker_decisions}

---

{portfolio_context}
{risk_context_block}{strategy_instruction}
Produce a report with these sections:

### 1. Cross-Ticker Dependencies and Contradictions
Identify cases where one ticker's bull/bear thesis conflicts with or depends on another's. Examples: competing for the same market, inverse correlation, shared supply chain risks, mutually exclusive macro assumptions. This is the most important section — surface hidden conflicts the per-ticker analyses could not see in isolation.

### 2. Comparative Ranking
Rank all tickers from most to least attractive, accounting for the dependencies found above. One-sentence justification each.

### 3. Capital Allocation
{allocation_instruction}

### 4. Cross-Cutting Risks
Risks affecting multiple tickers simultaneously (macro correlation, sector concentration, geopolitical exposure, interest rate sensitivity). Note which tickers share each risk and how it changes the portfolio's overall risk profile.

### 5. Actionable Summary
3-5 bullet action plan: what to buy first, what to avoid, what conditions would change the ranking.

Be decisive. Ground every conclusion in specific evidence from the analyst reports and decisions above.{language_instruction}"""

    response = llm.invoke(prompt)
    return response.content


def validate_merge_report(
    merge_report: str,
    ticker_results: list[tuple[str, str, dict]],
    config: dict,
    strategy: str | None = None,
    portfolio: dict | None = None,
) -> str:
    """Validate and optionally correct a merge report."""
    llm = _get_llm(config)

    ticker_summaries = []
    for ticker, decision, final_state in ticker_results:
        ftd = final_state.get("final_trade_decision", "")
        ticker_summaries.append(f"- **{ticker}**: Rating = {decision}\n  Decision: {ftd[:500]}")
    ticker_summaries_str = "\n".join(ticker_summaries)

    strategy_str = f"\n**Active Strategy:** {strategy}\n" if strategy else ""

    portfolio_str = ""
    if portfolio:
        holdings = ", ".join(f"{s}: {q} shares" for s, q in portfolio["holdings"].items())
        portfolio_str = f"\n**Current Portfolio:** {holdings}, Cash: ${portfolio['cash']:,.2f}\n"

    prompt = f"""You are a senior investment analyst reviewing a cross-ticker comparison report for factual accuracy and internal consistency.

**Per-Ticker Analysis Results (ground truth):**
{ticker_summaries_str}
{strategy_str}{portfolio_str}
**Cross-Ticker Comparison Report to Validate:**

{merge_report}

---

**Validation Checklist — check each item:**
1. All {len(ticker_results)} tickers appear in the Comparative Ranking. List any missing.
2. If the report changes a ticker's rating from the per-ticker analysis, it must cite a specific cross-ticker dependency as justification. Flag unjustified rating changes.
3. The Capital Allocation section is consistent with the ranking — higher-ranked tickers should receive proportionally more allocation.
4. The strategy "{strategy or 'balanced'}" is respected in tone and recommendations.
5. No hallucinated facts — every claim should be traceable to the per-ticker analysis.
6. Cross-Cutting Risks reference actual tickers from the analysis, not invented ones.

**Your task:** If you find issues, output a CORRECTED version of the full report (same 5 sections, same format). If the report passes all checks, output it unchanged. Do NOT add commentary outside the report — output only the report itself."""

    response = llm.invoke(prompt)
    return response.content
