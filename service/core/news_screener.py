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
    prompt = f"""You are a trading news screener. Evaluate whether this news article could materially affect the investment thesis for any of the mentioned tickers.

**Headline:** {headline}
**Summary:** {summary or "(none)"}
**Symbols:** {", ".join(symbols) if symbols else "(none mentioned)"}

Rate the relevance on a scale of 0.0 to 1.0:
- 0.0-0.3: Noise (routine analyst notes, minor price targets, general market commentary)
- 0.4-0.6: Possibly relevant (sector news, competitor developments, moderate guidance changes)
- 0.7-1.0: Highly material (earnings surprise, FDA decision, M&A, fraud, major contract, guidance revision)

Respond in this exact JSON format:
{{"score": <float>, "reasoning": "<one sentence>", "affected_ticker": "<most affected symbol or null>"}}"""

    response = _call_ollama(ollama_url, model, prompt)
    return _parse_json_response(response, default_score=0.0)


def investigate_deep(
    headline: str,
    summary: str,
    symbols: list[str],
    ticker: str,
    ollama_url: str,
    model: str,
) -> dict:
    """Deep investigation: does this news break the current thesis?"""
    prompt = f"""You are a senior portfolio analyst performing a deep news investigation for ticker {ticker}.

**News Headline:** {headline}
**News Summary:** {summary or "(none available)"}
**All Mentioned Symbols:** {", ".join(symbols)}
**Focus Ticker:** {ticker}

Determine if this news represents:
1. MATERIAL CHANGE — New information that could change the investment rating (earnings miss/beat, M&A, regulatory action, management change, fraud, guidance revision, major contract win/loss)
2. THESIS CONFIRMATION — News that confirms the existing analysis (expected results, routine updates)
3. NOISE — Not relevant to the investment thesis

If MATERIAL CHANGE: recommend whether this likely moves the thesis toward BUY, HOLD, or SELL.

Respond in this exact JSON format:
{{"verdict": "material_change|thesis_confirmation|noise", "direction": "buy|hold|sell|null", "reasoning": "<2-3 sentences explaining your assessment>", "should_regenerate_report": true|false}}"""

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
    ollama_url: str,
    model: str,
) -> dict:
    """Quick model: should this unwatched ticker be added to the watchlist? Strategy-aware."""
    prompt = f"""You are a watchlist curator for a **{strategy}** trading portfolio.

**Strategy description:** {strategy_instruction}

A news article just came in for a ticker that is NOT currently on our watchlist.

**Ticker:** {symbol}
**Headline:** {headline}
**Summary:** {summary or "(none)"}

Should we ADD this ticker to our **{strategy}** watchlist for further monitoring and potential investment?

Add to watchlist if:
- The news suggests a significant catalyst that fits the {strategy} risk profile
- The ticker is in a sector aligned with this strategy
- There's a time-sensitive opportunity warranting analysis

Do NOT add if:
- It's routine news (analyst price target changes, minor upgrades/downgrades)
- The ticker doesn't fit the {strategy} approach (e.g., a speculative biotech for a conservative strategy)
- The news is negative with no contrarian opportunity relevant to this strategy

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
