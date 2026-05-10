"""Quick and deep news screening via Ollama LLM calls."""

import json

import requests


def screen_news_quick(
    headline: str,
    summary: str,
    symbols: list[str],
    ollama_url: str,
    model: str,
) -> dict:
    """Quick screening: is this news worth investigating further?"""
    num_symbols = len(symbols)
    prompt = f"""You are a trading news screener. Evaluate whether this news article contains a SPECIFIC, NAMED CATALYST for any of the mentioned tickers.

**Headline:** {headline}
**Summary:** {summary or "(none)"}
**Symbols:** {", ".join(symbols) if symbols else "(none mentioned)"}
**Number of symbols tagged:** {num_symbols}

IMPORTANT RULES:
- Only score above 0.3 if the headline or summary describes a concrete event SPECIFIC to that ticker (e.g., earnings, contract win, FDA ruling, executive change, guidance revision).
- If the article tags 4+ symbols, it is likely a roundup or listicle. Score 0.0 unless the summary explicitly names a material event for a specific ticker.
- Generic market commentary ("stocks to watch", "market hits record high", "sector outlook") is ALWAYS 0.0-0.2 regardless of which tickers are tagged.
- The ticker merely appearing in a symbol list is NOT evidence of relevance.

Rate the relevance on a scale of 0.0 to 1.0:
- 0.0-0.2: Noise (roundups, listicles, generic market commentary, "stocks to watch")
- 0.3-0.5: Possibly relevant (sector-specific news naming this company, competitor M&A)
- 0.6-1.0: Highly material (earnings surprise, FDA decision, M&A, fraud, major contract, guidance revision directly about this ticker)

Respond in this exact JSON format:
{{"score": <float>, "reasoning": "<one sentence>", "affected_ticker": "<most affected symbol or null>"}}"""

    response = _call_ollama(ollama_url, model, prompt)
    return _parse_json_response(response, default_score=0.0)


def investigate_deep(
    headline: str,
    summary: str,
    symbols: list[str],
    ticker: str,
    current_thesis: str,
    ollama_url: str,
    model: str,
) -> dict:
    """Deep investigation: does this news break the current thesis?"""
    thesis_section = ""
    if current_thesis:
        thesis_section = f"""
**Current Investment Thesis:**
{current_thesis}

"""
    else:
        thesis_section = """
**Current Investment Thesis:** No prior analysis exists for this ticker.

"""

    prompt = f"""You are a senior portfolio analyst performing a deep news investigation for ticker {ticker}.

**News Headline:** {headline}
**News Summary:** {summary or "(none available)"}
**All Mentioned Symbols:** {", ".join(symbols)}
**Focus Ticker:** {ticker}
{thesis_section}Determine if this news represents:
1. MATERIAL CHANGE — New information that CONTRADICTS or SIGNIFICANTLY ALTERS the current thesis. This must be something the thesis did not already account for.
2. THESIS CONFIRMATION — News that is consistent with or already reflected in the existing thesis.
3. NOISE — Not relevant to the investment thesis.

IMPORTANT: Only mark should_regenerate_report as true if the news introduces genuinely NEW information that the current thesis does not account for. If the thesis already reflects this news (e.g., it was written after this event), set should_regenerate_report to false.

If no current thesis exists, set should_regenerate_report to true for any material news.

Respond in this exact JSON format:
{{"verdict": "material_change|thesis_confirmation|noise", "direction": "buy|hold|sell|null", "reasoning": "<2-3 sentences explaining your assessment>", "should_regenerate_report": true|false}}"""

    response = _call_ollama(ollama_url, model, prompt)
    return _parse_json_response(response, default_score=0.0)


def consolidate_news(
    ticker: str,
    articles: list[dict],
    ollama_url: str,
    model: str,
) -> dict:
    """Consolidate multiple articles into distinct events, preserving all information."""
    articles_text = "\n".join(
        f"[{a['id']}] {a['headline']}"
        + (f"\n    {a['summary'][:300]}" if a.get("summary") else "")
        for a in articles
    )

    prompt = f"""You are a news consolidation assistant for ticker {ticker}.

Below are {len(articles)} recent news articles that may cover overlapping events.
Group them by distinct underlying event/story. Multiple articles covering the same
event should be merged into ONE consolidated entry.

IMPORTANT: When merging articles, preserve ALL unique facts, data points, price
targets, analyst names, percentages, and details from every article in the group.
The consolidated summary must be richer and more complete than any single article.
Do not discard information.

**Articles:**
{articles_text}

For each distinct event, produce a consolidated headline and a comprehensive summary
that combines all key facts from the grouped articles.

Respond in this exact JSON format:
{{"events": [{{"headline": "...", "summary": "...", "article_ids": [1, 2, 3]}}]}}"""

    response = _call_ollama(ollama_url, model, prompt)
    return _parse_json_response(response, default_score=0.0)


def _call_ollama(ollama_url: str, model: str, prompt: str) -> str:
    url = f"{ollama_url}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1},
    }
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json().get("response", "")


def _parse_json_response(response: str, default_score: float = 0.0) -> dict:
    text = response.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {"score": default_score, "reasoning": text[:200], "parse_error": True}


def evaluate_watchlist_addition(
    headline: str,
    summary: str,
    symbol: str,
    strategy: str,
    strategy_instruction: str,
    num_symbols: int,
    ollama_url: str,
    model: str,
) -> dict:
    """Quick model: should this unwatched ticker be added to the watchlist? Strategy-aware."""
    prompt = f"""You are a watchlist curator for a **{strategy}** trading portfolio.

**Strategy description:** {strategy_instruction}

A news article just came in that mentions a ticker NOT currently on our watchlist.
Decide whether this news justifies adding the ticker to active monitoring — this means
committing GPU resources to run full analysis on every future article about this stock.

**Ticker:** {symbol}
**Headline:** {headline}
**Summary:** {summary or "(none)"}
**Total symbols tagged in this article:** {num_symbols}

Should we ADD this ticker to our **{strategy}** watchlist?

Add ONLY if ALL of the following are true:
- The headline or summary describes a SPECIFIC event about THIS ticker (not a market-wide roundup)
- The event is a significant catalyst that fits the {strategy} risk profile
- There is a clear, time-sensitive opportunity warranting analysis

Do NOT add if:
- The article tags many symbols and is a listicle/roundup ("top stocks to watch", "market recap")
- The summary does not mention this specific ticker by name or describe an event unique to it
- It's routine news (analyst price target changes, minor upgrades/downgrades)
- The ticker doesn't fit the {strategy} approach
- The news is generic market or sector commentary that happens to tag this symbol

Respond in JSON:
{{"add": true|false, "reasoning": "<one sentence>"}}"""

    response = _call_ollama(ollama_url, model, prompt)
    return _parse_json_response(response, default_score=0.0)


def evaluate_watchlist_prune(
    symbol: str,
    strategy: str,
    strategy_instruction: str,
    recent_headlines: list[str],
    ollama_url: str,
    model: str,
) -> dict:
    """Quick model: should this ticker be removed from the watchlist? Strategy-aware."""
    headlines_str = "\n".join(f"  - {h}" for h in recent_headlines) if recent_headlines else "(no recent news)"

    prompt = f"""You are a watchlist curator reviewing whether a ticker still belongs on a **{strategy}** watchlist.

**Strategy description:** {strategy_instruction}

**Ticker:** {symbol}
**Recent Headlines:**
{headlines_str}

Should we REMOVE this ticker from the {strategy} watchlist?

Remove if:
- The ticker no longer fits the {strategy} risk profile
- No meaningful catalysts in sight for this strategy
- The thesis has played out or broken
- The ticker is in a structural decline with no reversal catalyst relevant to this strategy

Keep if:
- There's an upcoming catalyst (earnings, FDA, conference)
- Recent news shows the story is still developing and fits the strategy
- The ticker could present an entry opportunity aligned with {strategy}

Answer YES, MAYBE, or NO with one sentence of reasoning.

Respond in JSON:
{{"remove": "yes"|"maybe"|"no", "reasoning": "<one sentence>"}}"""

    response = _call_ollama(ollama_url, model, prompt)
    return _parse_json_response(response, default_score=0.0)


def confirm_watchlist_prune(
    symbol: str,
    strategy: str,
    strategy_instruction: str,
    recent_headlines: list[str],
    quick_reasoning: str,
    ollama_url: str,
    model: str,
) -> dict:
    """Deep model: confirm whether to prune a ticker (called when quick model didn't say 'no')."""
    headlines_str = "\n".join(f"  - {h}" for h in recent_headlines) if recent_headlines else "(no recent news)"

    prompt = f"""You are a senior portfolio strategist making the final decision on whether to remove a ticker from a **{strategy}** watchlist.

**Strategy description:** {strategy_instruction}
**Ticker:** {symbol}

**Recent Headlines:**
{headlines_str}

**Initial screening said:** {quick_reasoning}

Perform a thorough evaluation:
1. Does this ticker still have a plausible investment thesis for a {strategy} approach?
2. Are there any upcoming catalysts within the next 30 days?
3. Is the sector/theme still relevant to this strategy?
4. Could removing it cause us to miss an opportunity?

Respond in JSON:
{{"remove": true|false, "reasoning": "<2-3 sentences>"}}"""

    response = _call_ollama(ollama_url, model, prompt)
    return _parse_json_response(response, default_score=0.0)
