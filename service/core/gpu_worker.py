"""GPU Worker process — runs synchronously, pulls tasks from Redis, executes LLM calls."""

import datetime
import json
import signal
import sys
import time
from functools import partial
from pathlib import Path

import redis

from service.config import load_config, ServiceConfig, ProviderConfig
from service.core.llm_adapter import call_llm_sync
from service.metrics import record_task_completed, record_token_usage, record_queue_depth, record_worker_utilization


RESULT_QUEUE = "gpu:results:queue"
STATUS_CHANNEL = "gpu:status"


class OllamaWorker:
    def __init__(self, config: ServiceConfig, provider_name: str):
        self.config = config
        self.provider_name = provider_name
        self.provider_config = config.providers[provider_name]
        self._redis = redis.from_url(config.redis_url, decode_responses=True)
        self._queue_key = f"gpu:provider:{provider_name}:queue"
        self._status_key = f"gpu:provider:{provider_name}:status"
        self._active_key = f"gpu:provider:{provider_name}:active"
        self._paused_key = f"gpu:provider:{provider_name}:paused"
        self._current_model: str | None = None
        self._running = True
        self._task_count = 0
        self._model_switches = 0

    def run(self):
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        self._publish_status("starting", "Worker starting up")
        self._redis.set(self._active_key, 0)

        while self._running:
            if self._is_paused():
                self._publish_status("paused", "Paused — waiting for resume")
                time.sleep(2)
                continue

            raw = self._redis.lpop(self._queue_key)
            if raw is None:
                self._publish_status("idle", "Waiting for tasks")
                depth = self._redis.llen(self._queue_key)
                record_queue_depth(self.provider_name, depth)
                record_worker_utilization(self.provider_name, 0, 1)
                time.sleep(0.5)
                continue

            task = json.loads(raw)
            tier = task.get("model_tier", "quick")
            needed_model = (
                self.provider_config.quick_model if tier == "quick"
                else self.provider_config.deep_model
            )
            if self._current_model != needed_model:
                self._switch_model(needed_model, tier)

            self._redis.incr(self._active_key)
            try:
                self._execute_task(task)
            finally:
                self._redis.decr(self._active_key)
            self._task_count += 1

        self._publish_status("stopped", "Worker shut down")

    def _switch_model(self, model: str, tier: str):
        self._publish_status("switching_model", f"Loading {model}")
        self._current_model = model
        self._model_switches += 1
        self._publish_status("ready", f"Model {model} loaded ({tier})")

    def _execute_task(self, task: dict):
        task_id = task["task_id"]
        task_type = task["task_type"]
        ticker = task.get("ticker")

        if self._is_cancelled(task_id):
            return

        self._publish_status("executing", f"{task_type} for {ticker or 'N/A'}")

        started_at = datetime.datetime.utcnow().isoformat()

        self._push_result({
            "task_id": task_id,
            "task_type": task_type,
            "ticker": ticker,
            "status": "running",
            "started_at": started_at,
        })

        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._dispatch, task_type, task.get("payload", {}))

        try:
            while True:
                try:
                    result = future.result(timeout=2.0)
                    break
                except concurrent.futures.TimeoutError:
                    if self._is_cancelled(task_id):
                        future.cancel()
                        executor.shutdown(wait=False)
                        self._push_result({
                            "task_id": task_id,
                            "task_type": task_type,
                            "ticker": ticker,
                            "status": "failed",
                            "error": "Cancelled by user",
                            "started_at": started_at,
                            "completed_at": datetime.datetime.utcnow().isoformat(),
                        })
                        return

            self._push_result({
                "task_id": task_id,
                "task_type": task_type,
                "ticker": ticker,
                "status": "completed",
                "result": result,
                "started_at": started_at,
                "completed_at": datetime.datetime.utcnow().isoformat(),
            })
            completed_at = datetime.datetime.utcnow()
            duration = (completed_at - datetime.datetime.fromisoformat(started_at)).total_seconds()
            record_task_completed(task_type, self.provider_name, task.get("model_tier", "quick"), ticker, "completed", duration)
        except Exception as e:
            import traceback
            error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
            self._push_result({
                "task_id": task_id,
                "task_type": task_type,
                "ticker": ticker,
                "status": "failed",
                "error": error_detail,
                "started_at": started_at,
                "completed_at": datetime.datetime.utcnow().isoformat(),
            })
            duration = (datetime.datetime.utcnow() - datetime.datetime.fromisoformat(started_at)).total_seconds()
            record_task_completed(task_type, self.provider_name, task.get("model_tier", "quick"), ticker, "failed", duration)
        finally:
            executor.shutdown(wait=False)

    def _push_result(self, data: dict):
        data["provider"] = self.provider_name
        self._redis.rpush(RESULT_QUEUE, json.dumps(data))

    def _llm_call(self, model: str, prompt: str) -> str:
        def _on_usage(prompt_tokens: int, completion_tokens: int):
            record_token_usage(self.provider_name, model, prompt_tokens, completion_tokens)
        return call_llm_sync(self.provider_config, model, prompt, on_usage=_on_usage)

    def _dispatch(self, task_type: str, payload: dict) -> dict:
        if task_type == "news_screen":
            return self._screen_news(payload)
        elif task_type == "news_consolidate":
            return self._consolidate_news(payload)
        elif task_type == "investigation":
            return self._investigate(payload)
        elif task_type == "full_analysis":
            return self._full_analysis(payload)
        elif task_type == "merge_and_allocate":
            return self._merge_and_allocate(payload)
        elif task_type == "watchlist_discovery":
            return self._watchlist_discovery(payload)
        elif task_type == "watchlist_prune":
            return self._watchlist_prune(payload)
        elif task_type == "watchlist_rank_prune":
            return self._watchlist_rank_prune(payload)
        else:
            raise ValueError(f"Unknown task type: {task_type}")

    def _screen_news(self, payload: dict) -> dict:
        from service.core.news_screener import screen_news_quick
        model = self.provider_config.quick_model
        return screen_news_quick(
            headline=payload["headline"],
            summary=payload.get("summary", ""),
            symbols=payload.get("symbols", []),
            llm_call=partial(self._llm_call, model),
        )

    def _consolidate_news(self, payload: dict) -> dict:
        from service.core.news_screener import consolidate_news
        model = self.provider_config.quick_model
        return consolidate_news(
            ticker=payload.get("ticker", ""),
            articles=payload.get("articles", []),
            llm_call=partial(self._llm_call, model),
        )

    def _investigate(self, payload: dict) -> dict:
        from service.core.news_screener import investigate_deep
        model = self.provider_config.deep_model
        return investigate_deep(
            headline=payload["headline"],
            summary=payload.get("summary", ""),
            symbols=payload.get("symbols", []),
            ticker=payload.get("ticker", ""),
            current_thesis=payload.get("current_thesis", ""),
            llm_call=partial(self._llm_call, model),
        )

    def _watchlist_discovery(self, payload: dict) -> dict:
        from service.core.news_screener import evaluate_watchlist_addition
        from cli.position_risk import STRATEGY_THRESHOLDS
        symbols = payload.get("symbols", [])
        symbol = symbols[0] if symbols else payload.get("ticker", "")
        strategy = payload.get("strategy", "balanced")
        thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
        model = self.provider_config.quick_model
        return evaluate_watchlist_addition(
            headline=payload["headline"],
            summary=payload.get("summary", ""),
            symbol=symbol,
            strategy=strategy,
            strategy_instruction=thresholds.get("discovery_instruction", thresholds.get("instruction", "")),
            num_symbols=len(symbols),
            llm_call=partial(self._llm_call, model),
        )

    def _watchlist_prune(self, payload: dict) -> dict:
        from cli.position_risk import STRATEGY_THRESHOLDS
        strategy = payload.get("strategy", "balanced")
        thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
        stage = payload.get("stage", "quick")

        if stage == "deep":
            from service.core.news_screener import confirm_watchlist_prune
            model = self.provider_config.deep_model
            return confirm_watchlist_prune(
                symbol=payload["symbol"],
                strategy=strategy,
                strategy_instruction=thresholds.get("instruction", ""),
                recent_headlines=payload.get("recent_headlines", []),
                quick_reasoning=payload.get("quick_reasoning", ""),
                llm_call=partial(self._llm_call, model),
            )
        else:
            from service.core.news_screener import evaluate_watchlist_prune
            model = self.provider_config.quick_model
            return evaluate_watchlist_prune(
                symbol=payload["symbol"],
                strategy=strategy,
                strategy_instruction=thresholds.get("instruction", ""),
                recent_headlines=payload.get("recent_headlines", []),
                llm_call=partial(self._llm_call, model),
            )

    def _watchlist_rank_prune(self, payload: dict) -> dict:
        from service.core.news_screener import rank_and_prune_watchlist
        from cli.position_risk import STRATEGY_THRESHOLDS
        strategy = payload.get("strategy", "balanced")
        thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
        model = self.provider_config.deep_model
        return rank_and_prune_watchlist(
            tickers_with_context=payload["tickers"],
            max_tickers=payload["max_tickers"],
            strategy=strategy,
            strategy_instruction=thresholds.get("instruction", ""),
            held_symbols=payload.get("held_symbols", []),
            llm_call=partial(self._llm_call, model),
        )

    def _full_analysis(self, payload: dict) -> dict:
        from shared.analysis import run_single_ticker
        from shared.config import build_graph_config
        import json as json_mod

        ticker = payload["ticker"]
        ollama_base = self.provider_config.url.rstrip("/")
        if "/v1" not in ollama_base:
            ollama_base += "/v1"

        provider_type = "ollama" if self.provider_config.type == "ollama" else "openrouter"
        config = build_graph_config(
            provider=provider_type,
            quick_model=self.provider_config.quick_model,
            deep_model=self.provider_config.deep_model,
            backend_url=ollama_base,
        )
        config["data_vendors"]["news_data"] = "database"
        config["database_path"] = str(Path(self.config.database_path).resolve())

        result = run_single_ticker(
            ticker=ticker,
            config=config,
            past_context=payload.get("past_context", ""),
        )

        final_state = result["final_state"]

        analysis_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        fs_ticker = ticker.replace(":", "_")
        reports_dir = Path("reports") / f"{fs_ticker}_{analysis_date}"
        try:
            from cli.main import save_report_to_disk
            save_report_to_disk(final_state, ticker, reports_dir)
        except Exception:
            pass

        state_dir = Path("reports") / "_states"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / f"{fs_ticker}.json"
        serializable_state = {
            "generated_at": datetime.datetime.utcnow().isoformat(),
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

    def _merge_and_allocate(self, payload: dict) -> dict:
        from shared.merge import generate_merge_report, validate_merge_report
        from shared.config import build_graph_config
        from cli.order_parser import parse_orders

        tickers_data = payload["tickers_data"]
        account_id = payload.get("account_id")
        strategy = payload.get("strategy", "balanced")
        portfolio = payload.get("portfolio")

        ollama_base = self.provider_config.url.rstrip("/")
        if "/v1" not in ollama_base:
            ollama_base += "/v1"

        provider_type = "ollama" if self.provider_config.type == "ollama" else "openrouter"
        config = build_graph_config(
            provider=provider_type,
            quick_model=self.provider_config.quick_model,
            deep_model=self.provider_config.deep_model,
            backend_url=ollama_base,
        )
        config["llm_timeout"] = 3600

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
                    {
                        "ticker": o.symbol,
                        "side": o.side,
                        "qty": o.qty,
                    }
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

    def _is_cancelled(self, task_id: str) -> bool:
        return bool(self._redis.get(f"gpu:cancel:{task_id}"))

    def _is_paused(self) -> bool:
        return bool(self._redis.get(self._paused_key))

    def _publish_status(self, state: str, message: str):
        status = {
            "state": state,
            "message": message,
            "current_model": self._current_model,
            "task_count": self._task_count,
            "model_switches": self._model_switches,
            "provider": self.provider_name,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        self._redis.set(self._status_key, json.dumps(status))
        self._redis.publish(STATUS_CHANNEL, json.dumps(status))

    def _handle_shutdown(self, signum, frame):
        self._running = False


def main(provider_name: str = "local"):
    config = load_config()
    worker = OllamaWorker(config, provider_name)
    worker.run()


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "local"
    main(name)
