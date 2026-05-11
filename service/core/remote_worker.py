"""Remote API worker — async, supports concurrent task execution."""

import asyncio
import datetime
import json
import signal
import sys
import traceback
from functools import partial
from pathlib import Path

import redis.asyncio as aioredis

from service.config import load_config, ServiceConfig, ProviderConfig
from service.core.llm_adapter import call_llm_async


RESULT_QUEUE = "gpu:results:queue"
STATUS_CHANNEL = "gpu:status"


class RemoteWorker:
    def __init__(self, config: ServiceConfig, provider_name: str):
        self.config = config
        self.provider_name = provider_name
        self.provider_config = config.providers[provider_name]
        self._queue_key = f"gpu:provider:{provider_name}:queue"
        self._status_key = f"gpu:provider:{provider_name}:status"
        self._active_key = f"gpu:provider:{provider_name}:active"
        self._paused_key = f"gpu:provider:{provider_name}:paused"
        self._redis: aioredis.Redis | None = None
        self._running = True
        self._task_count = 0
        self._max_concurrent = self.provider_config.max_concurrent

    async def run(self):
        self._redis = aioredis.from_url(self.config.redis_url, decode_responses=True)
        await self._publish_status("starting", "Worker starting up")

        if sys.platform != "win32":
            loop = asyncio.get_event_loop()
            loop.add_signal_handler(signal.SIGTERM, self._handle_shutdown)
            loop.add_signal_handler(signal.SIGINT, self._handle_shutdown)

        while self._running:
            paused = await self._redis.get(self._paused_key)
            if paused:
                await self._publish_status("paused", "Paused — waiting for resume")
                await asyncio.sleep(2)
                continue

            # Only pop if we have capacity (avoids unbounded in-memory buildup)
            active = int(await self._redis.get(self._active_key) or 0)
            if active >= self._max_concurrent:
                await self._publish_status("executing", f"{active} tasks running (at capacity)")
                await asyncio.sleep(0.5)
                continue

            raw = await self._redis.lpop(self._queue_key)
            if raw is None:
                if active > 0:
                    await self._publish_status("executing", f"{active} tasks running")
                else:
                    await self._publish_status("idle", "Waiting for tasks")
                await asyncio.sleep(0.5)
                continue

            task = json.loads(raw)
            # Increment active immediately so the next loop iteration sees it
            await self._redis.incr(self._active_key)
            asyncio.create_task(self._execute_task_tracked(task))

        await self._publish_status("stopped", "Worker shut down")
        await self._redis.close()

    async def _execute_task_tracked(self, task: dict):
        try:
            await self._execute_task(task)
        finally:
            await self._redis.decr(self._active_key)
            self._task_count += 1

    async def _execute_task(self, task: dict):
        task_id = task["task_id"]
        task_type = task["task_type"]
        ticker = task.get("ticker")

        cancel_key = f"gpu:cancel:{task_id}"
        if await self._redis.get(cancel_key):
            return

        await self._publish_status("executing", f"{task_type} for {ticker or 'N/A'}")

        started_at = datetime.datetime.utcnow().isoformat()

        await self._push_result({
            "task_id": task_id,
            "task_type": task_type,
            "ticker": ticker,
            "status": "running",
            "started_at": started_at,
        })

        try:
            result = await self._dispatch(task_type, task.get("payload", {}))
            await self._push_result({
                "task_id": task_id,
                "task_type": task_type,
                "ticker": ticker,
                "status": "completed",
                "result": result,
                "started_at": started_at,
                "completed_at": datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
            await self._push_result({
                "task_id": task_id,
                "task_type": task_type,
                "ticker": ticker,
                "status": "failed",
                "error": error_detail,
                "started_at": started_at,
                "completed_at": datetime.datetime.utcnow().isoformat(),
            })

    async def _push_result(self, data: dict):
        data["provider"] = self.provider_name
        await self._redis.rpush(RESULT_QUEUE, json.dumps(data))

    def _full_analysis_sync(self, payload: dict) -> dict:
        from shared.analysis import run_single_ticker
        from shared.config import build_graph_config
        from pathlib import Path
        import json as json_mod

        ticker = payload["ticker"]
        url = self.provider_config.url.rstrip("/")
        if "/v1" not in url:
            url += "/v1"

        config = build_graph_config(
            provider="openrouter",
            quick_model=self.provider_config.quick_model,
            deep_model=self.provider_config.deep_model,
            backend_url=url,
        )
        if self.provider_config.api_key:
            config["api_key"] = self.provider_config.api_key
        config["data_vendors"]["news_data"] = "database"
        config["database_path"] = str(Path(self.config.database_path).resolve())

        result = run_single_ticker(
            ticker=ticker,
            config=config,
            past_context=payload.get("past_context", ""),
        )

        final_state = result["final_state"]

        import datetime as dt
        analysis_date = dt.datetime.utcnow().strftime("%Y-%m-%d")
        reports_dir = Path("reports") / f"{ticker}_{analysis_date}"
        try:
            from cli.main import save_report_to_disk
            save_report_to_disk(final_state, ticker, reports_dir)
        except Exception:
            pass

        state_dir = Path("reports") / "_states"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / f"{ticker}.json"
        serializable_state = {
            "generated_at": dt.datetime.utcnow().isoformat(),
            "final_trade_decision": final_state.get("final_trade_decision", ""),
            "market_report": final_state.get("market_report", ""),
            "news_report": final_state.get("news_report", ""),
            "sentiment_report": final_state.get("sentiment_report", ""),
            "fundamentals_report": final_state.get("fundamentals_report", ""),
            "investment_debate_state": final_state.get("investment_debate_state", {}),
            "trader_investment_plan": final_state.get("trader_investment_plan", ""),
            "risk_debate_state": final_state.get("risk_debate_state", {}),
        }
        state_file.write_text(json_mod.dumps(serializable_state, indent=2, default=str), encoding="utf-8")

        return {
            "ticker": ticker,
            "decision": result["decision"],
            "final_state": serializable_state,
            "elapsed": result["elapsed"],
            "stats": result["stats"],
        }

    def _merge_and_allocate_sync(self, payload: dict) -> dict:
        from shared.merge import generate_merge_report, validate_merge_report
        from shared.config import build_graph_config
        from cli.order_parser import parse_orders

        tickers_data = payload["tickers_data"]
        account_id = payload.get("account_id")
        strategy = payload.get("strategy", "balanced")
        portfolio = payload.get("portfolio")

        url = self.provider_config.url.rstrip("/")
        if "/v1" not in url:
            url += "/v1"

        config = build_graph_config(
            provider="openrouter",
            quick_model=self.provider_config.quick_model,
            deep_model=self.provider_config.deep_model,
            backend_url=url,
        )
        if self.provider_config.api_key:
            config["api_key"] = self.provider_config.api_key

        ticker_results = [
            (t["ticker"], t["decision"], t["final_state"])
            for t in tickers_data
        ]

        merge_report = generate_merge_report(
            ticker_results=ticker_results,
            config=config,
            portfolio=portfolio,
            strategy=strategy,
        )

        merge_checks = payload.get("merge_checks_override") or self.config.evaluation.merge_checks
        validated_report = merge_report
        for _ in range(merge_checks):
            validated_report = validate_merge_report(
                merge_report=validated_report,
                ticker_results=ticker_results,
                config=config,
                strategy=strategy,
                portfolio=portfolio,
            )

        allocation_data = []
        proposed_orders = []
        allocation_reasoning = ""

        if portfolio:
            try:
                portfolio_dict = {
                    "holdings": portfolio.get("holdings", {}),
                    "cash": portfolio.get("cash", 0),
                }
                quotes = dict(portfolio.get("prices", {}))

                try:
                    from cli.alpaca_client import create_client, fetch_quotes
                    acct = self.config.accounts.get(account_id)
                    if acct:
                        all_symbols = list(set(
                            [t["ticker"] for t in tickers_data] +
                            list(portfolio_dict["holdings"].keys())
                        ))
                        missing = [s for s in all_symbols if s not in quotes]
                        if missing:
                            client = create_client(acct.api_key, acct.api_secret, paper=acct.is_paper)
                            live_quotes = fetch_quotes(client, missing)
                            quotes.update(live_quotes)
                except Exception:
                    pass

                trade_plan, allocation_plan = parse_orders(
                    merge_report=validated_report,
                    portfolio_dict=portfolio_dict,
                    quotes=quotes,
                    pending=[],
                    config=config,
                    strategy=strategy,
                    allocation_checks=payload.get("allocation_checks_override") or self.config.evaluation.allocation_checks,
                )

                holdings = portfolio_dict.get("holdings", {})
                cash = portfolio_dict.get("cash", 0)
                portfolio_value = sum(holdings.get(s, 0) * quotes.get(s, 0) for s in holdings) + cash

                order_map = {}
                for o in trade_plan.orders:
                    order_map[o.symbol] = o.side

                allocation_data = []
                for a in allocation_plan.allocations:
                    current_qty = holdings.get(a.symbol, 0)
                    current_price = quotes.get(a.symbol, 0)
                    current_value = current_qty * current_price
                    current_pct = (current_value / portfolio_value * 100) if portfolio_value > 0 else 0
                    target_value = portfolio_value * a.pct / 100 if portfolio_value > 0 else 0
                    action = order_map.get(a.symbol, "hold")
                    if current_value < 1 and target_value < 1:
                        continue
                    allocation_data.append({
                        "symbol": a.symbol,
                        "action": action,
                        "current_pct": round(current_pct, 2),
                        "target_pct": round(a.pct, 2),
                        "current_value": round(current_value, 2),
                        "target_value": round(target_value, 2),
                        "current_qty": current_qty,
                        "price": round(current_price, 2) if current_price else None,
                    })

                allocation_reasoning = allocation_plan.reasoning
                proposed_orders = [
                    {"ticker": o.symbol, "side": o.side, "qty": o.qty}
                    for o in trade_plan.orders
                ]
            except Exception as e:
                allocation_reasoning = f"Allocation failed: {e}"
                portfolio_value = 0

        return {
            "account_id": account_id,
            "merge_report": validated_report,
            "tickers": [t["ticker"] for t in tickers_data],
            "ticker_data": [
                {
                    "ticker": t["ticker"],
                    "decision": t["decision"],
                    "reasoning": t["final_state"].get("final_trade_decision", "")[:500],
                }
                for t in tickers_data
            ],
            "strategy": strategy,
            "proposed_orders": proposed_orders,
            "allocation": allocation_data,
            "allocation_reasoning": allocation_reasoning,
            "cash_pct": allocation_plan.cash_pct if 'allocation_plan' in dir() else None,
            "portfolio_value": portfolio_value if 'portfolio_value' in dir() else None,
            "cash_after": (portfolio_value * allocation_plan.cash_pct / 100) if 'allocation_plan' in dir() and 'portfolio_value' in dir() and portfolio_value else None,
        }

    async def _llm_call(self, model: str, prompt: str) -> str:
        return await call_llm_async(self.provider_config, model, prompt)

    async def _dispatch(self, task_type: str, payload: dict) -> dict:
        if task_type == "news_screen":
            return await self._screen_news(payload)
        elif task_type == "news_consolidate":
            return await self._consolidate_news(payload)
        elif task_type == "investigation":
            return await self._investigate(payload)
        elif task_type == "watchlist_discovery":
            return await self._watchlist_discovery(payload)
        elif task_type == "watchlist_prune":
            return await self._watchlist_prune(payload)
        elif task_type == "full_analysis":
            return await asyncio.to_thread(self._full_analysis_sync, payload)
        elif task_type == "merge_and_allocate":
            return await asyncio.to_thread(self._merge_and_allocate_sync, payload)
        else:
            raise ValueError(f"Unknown task type: {task_type}")

    async def _screen_news(self, payload: dict) -> dict:
        from service.core.news_screener import _parse_json_response
        if "headline" not in payload:
            raise ValueError(f"Task payload missing 'headline'. Got keys: {list(payload.keys())}")
        model = self.provider_config.quick_model
        response = await self._llm_call(model, _build_screen_prompt(payload))
        return _parse_json_response(response, default_score=0.0)

    async def _consolidate_news(self, payload: dict) -> dict:
        from service.core.news_screener import _parse_json_response
        if not payload.get("articles"):
            raise ValueError(f"Task payload missing 'articles'. Got keys: {list(payload.keys())}")
        model = self.provider_config.quick_model
        response = await self._llm_call(model, _build_consolidate_prompt(payload))
        return _parse_json_response(response, default_score=0.0)

    async def _investigate(self, payload: dict) -> dict:
        from service.core.news_screener import _parse_json_response
        if "headline" not in payload:
            raise ValueError(f"Task payload missing 'headline'. Got keys: {list(payload.keys())}")
        model = self.provider_config.deep_model
        response = await self._llm_call(model, _build_investigate_prompt(payload))
        return _parse_json_response(response, default_score=0.0)

    async def _watchlist_discovery(self, payload: dict) -> dict:
        from service.core.news_screener import _parse_json_response
        if "headline" not in payload:
            raise ValueError(f"Task payload missing 'headline'. Got keys: {list(payload.keys())}")
        from cli.position_risk import STRATEGY_THRESHOLDS
        symbols = payload.get("symbols", [])
        symbol = symbols[0] if symbols else payload.get("ticker", "")
        strategy = payload.get("strategy", "balanced")
        thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
        model = self.provider_config.quick_model
        response = await self._llm_call(model, _build_watchlist_addition_prompt(
            payload, symbol, strategy, thresholds.get("instruction", ""), len(symbols)
        ))
        return _parse_json_response(response, default_score=0.0)

    async def _watchlist_prune(self, payload: dict) -> dict:
        from service.core.news_screener import _parse_json_response
        from cli.position_risk import STRATEGY_THRESHOLDS
        strategy = payload.get("strategy", "balanced")
        thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
        stage = payload.get("stage", "quick")
        model = self.provider_config.deep_model if stage == "deep" else self.provider_config.quick_model
        if stage == "deep":
            response = await self._llm_call(model, _build_prune_confirm_prompt(
                payload, strategy, thresholds.get("instruction", "")
            ))
        else:
            response = await self._llm_call(model, _build_prune_quick_prompt(
                payload, strategy, thresholds.get("instruction", "")
            ))
        return _parse_json_response(response, default_score=0.0)

    async def _publish_status(self, state: str, message: str):
        status = {
            "state": state,
            "message": message,
            "current_model": None,
            "task_count": self._task_count,
            "model_switches": 0,
            "provider": self.provider_name,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        await self._redis.set(self._status_key, json.dumps(status))
        await self._redis.publish(STATUS_CHANNEL, json.dumps(status))

    def _handle_shutdown(self):
        self._running = False


def _build_screen_prompt(payload: dict) -> str:
    """Reconstruct the screening prompt from payload."""
    from service.core.news_screener import screen_news_quick
    import inspect
    headline = payload["headline"]
    summary = payload.get("summary", "")
    symbols = payload.get("symbols", [])
    num_symbols = len(symbols)
    return f"""You are a trading news screener. Evaluate whether this news article contains a SPECIFIC, NAMED CATALYST for any of the mentioned tickers.

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


def _build_consolidate_prompt(payload: dict) -> str:
    ticker = payload.get("ticker", "")
    articles = payload.get("articles", [])
    articles_text = "\n".join(
        f"[{a['id']}] {a['headline']}"
        + (f"\n    {a['summary'][:300]}" if a.get("summary") else "")
        for a in articles
    )
    return f"""You are a news consolidation assistant for ticker {ticker}.

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


def _build_investigate_prompt(payload: dict) -> str:
    headline = payload["headline"]
    summary = payload.get("summary", "")
    symbols = payload.get("symbols", [])
    ticker = payload.get("ticker", "")
    current_thesis = payload.get("current_thesis", "")
    thesis_section = f"\n**Current Investment Thesis:**\n{current_thesis}\n" if current_thesis else "\n**Current Investment Thesis:** No prior analysis exists for this ticker.\n"
    return f"""You are a senior portfolio analyst performing a deep news investigation for ticker {ticker}.

**News Headline:** {headline}
**News Summary:** {summary or "(none available)"}
**All Mentioned Symbols:** {", ".join(symbols)}
**Focus Ticker:** {ticker}
{thesis_section}
Determine if this news represents:
1. MATERIAL CHANGE — New information that CONTRADICTS or SIGNIFICANTLY ALTERS the current thesis.
2. THESIS CONFIRMATION — News that is consistent with or already reflected in the existing thesis.
3. NOISE — Not relevant to the investment thesis.

IMPORTANT: Only mark should_regenerate_report as true if the news introduces genuinely NEW information that the current thesis does not account for.

Respond in this exact JSON format:
{{"verdict": "material_change|thesis_confirmation|noise", "direction": "buy|hold|sell|null", "reasoning": "<2-3 sentences>", "should_regenerate_report": true|false}}"""


def _build_watchlist_addition_prompt(payload: dict, symbol: str, strategy: str, instruction: str, num_symbols: int) -> str:
    headline = payload["headline"]
    summary = payload.get("summary", "")
    return f"""You are a watchlist curator for a **{strategy}** trading portfolio.

**Strategy description:** {instruction}

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


def _build_prune_quick_prompt(payload: dict, strategy: str, instruction: str) -> str:
    symbol = payload["symbol"]
    recent_headlines = payload.get("recent_headlines", [])
    headlines_str = "\n".join(f"  - {h}" for h in recent_headlines) if recent_headlines else "(no recent news)"
    return f"""You are a watchlist curator reviewing whether a ticker still belongs on a **{strategy}** watchlist.

**Strategy description:** {instruction}

**Ticker:** {symbol}
**Recent Headlines:**
{headlines_str}

Should we REMOVE this ticker from the {strategy} watchlist?

Remove if:
- The ticker no longer fits the {strategy} risk profile
- No meaningful catalysts in sight for this strategy
- The thesis has played out or broken

Keep if:
- There's an upcoming catalyst (earnings, FDA, conference)
- Recent news shows the story is still developing and fits the strategy

Respond in JSON:
{{"remove": "yes"|"maybe"|"no", "reasoning": "<one sentence>"}}"""


def _build_prune_confirm_prompt(payload: dict, strategy: str, instruction: str) -> str:
    symbol = payload["symbol"]
    recent_headlines = payload.get("recent_headlines", [])
    quick_reasoning = payload.get("quick_reasoning", "")
    headlines_str = "\n".join(f"  - {h}" for h in recent_headlines) if recent_headlines else "(no recent news)"
    return f"""You are a senior portfolio strategist making the final decision on whether to remove a ticker from a **{strategy}** watchlist.

**Strategy description:** {instruction}
**Ticker:** {symbol}

**Recent Headlines:**
{headlines_str}

**Initial screening said:** {quick_reasoning}

Perform a thorough evaluation:
1. Does this ticker still have a plausible investment thesis for a {strategy} approach?
2. Are there any upcoming catalysts within the next 30 days?
3. Is the sector/theme still relevant to this strategy?

Respond in JSON:
{{"remove": true|false, "reasoning": "<2-3 sentences>"}}"""


def main(provider_name: str):
    config = load_config()
    worker = RemoteWorker(config, provider_name)
    asyncio.run(worker.run())


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if not name:
        print("Usage: python -m service.core.remote_worker <provider_name>")
        sys.exit(1)
    main(name)
