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
