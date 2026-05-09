"""GPU Worker process — runs synchronously, pulls tasks from Redis, executes LLM calls."""

import datetime
import json
import signal
import sys
import time
from pathlib import Path

import redis

from service.config import load_config, ServiceConfig


QUICK_QUEUE = "gpu:queue:quick"
DEEP_QUEUE = "gpu:queue:deep"
RESULT_CHANNEL = "gpu:results"
STATUS_CHANNEL = "gpu:status"


class GpuWorker:
    def __init__(self, config: ServiceConfig):
        self.config = config
        self._redis = redis.from_url(config.redis_url, decode_responses=True)
        self._current_model: str | None = None
        self._running = True
        self._task_count = 0
        self._model_switches = 0

    def run(self):
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

        self._publish_status("starting", "Worker starting up")

        while self._running:
            queue, tier = self._pick_queue()
            if queue is None:
                self._publish_status("idle", "Waiting for tasks")
                time.sleep(0.5)
                continue

            needed_model = (
                self.config.gpu.quick_model if tier == "quick"
                else self.config.gpu.deep_model
            )
            if self._current_model != needed_model:
                self._switch_model(needed_model, tier)

            batch_count = 0
            while self._running and batch_count < self.config.gpu.max_batch_before_yield:
                raw = self._redis.lpop(queue)
                if raw is None:
                    break

                task = json.loads(raw)
                self._execute_task(task)
                batch_count += 1
                self._task_count += 1

        self._publish_status("stopped", "Worker shut down")

    def _pick_queue(self) -> tuple[str | None, str | None]:
        quick_emergency = self._has_emergency(QUICK_QUEUE)
        deep_emergency = self._has_emergency(DEEP_QUEUE)

        if quick_emergency:
            return QUICK_QUEUE, "quick"
        if deep_emergency:
            return DEEP_QUEUE, "deep"

        quick_len = self._redis.llen(QUICK_QUEUE)
        deep_len = self._redis.llen(DEEP_QUEUE)

        if quick_len == 0 and deep_len == 0:
            return None, None

        if quick_len >= deep_len:
            return QUICK_QUEUE, "quick"
        return DEEP_QUEUE, "deep"

    def _has_emergency(self, queue: str) -> bool:
        raw = self._redis.lindex(queue, 0)
        if raw:
            task = json.loads(raw)
            return task.get("priority", 1) == 0
        return False

    def _switch_model(self, model: str, tier: str):
        self._publish_status("switching_model", f"Loading {model}")
        self._current_model = model
        self._model_switches += 1
        self._publish_status("ready", f"Model {model} loaded ({tier})")

    def _execute_task(self, task: dict):
        task_id = task["task_id"]
        task_type = task["task_type"]
        ticker = task.get("ticker")

        # Check if task was already cancelled before we start
        if self._is_cancelled(task_id):
            return

        self._publish_status("executing", f"{task_type} for {ticker or 'N/A'}")

        started_at = datetime.datetime.utcnow().isoformat()

        # Publish "started" so the API shows task as running
        self._redis.publish(RESULT_CHANNEL, json.dumps({
            "task_id": task_id,
            "task_type": task_type,
            "ticker": ticker,
            "status": "running",
            "started_at": started_at,
        }))

        try:
            result = self._dispatch(task_type, task.get("payload", {}))
            self._redis.publish(RESULT_CHANNEL, json.dumps({
                "task_id": task_id,
                "task_type": task_type,
                "ticker": ticker,
                "status": "completed",
                "result": result,
                "started_at": started_at,
                "completed_at": datetime.datetime.utcnow().isoformat(),
            }))
        except Exception as e:
            import traceback
            error_detail = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
            self._redis.publish(RESULT_CHANNEL, json.dumps({
                "task_id": task_id,
                "task_type": task_type,
                "ticker": ticker,
                "status": "failed",
                "error": error_detail,
                "started_at": started_at,
                "completed_at": datetime.datetime.utcnow().isoformat(),
            }))

    def _dispatch(self, task_type: str, payload: dict) -> dict:
        if task_type == "news_screen":
            return self._screen_news(payload)
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
        else:
            raise ValueError(f"Unknown task type: {task_type}")

    def _screen_news(self, payload: dict) -> dict:
        from service.core.news_screener import screen_news_quick
        return screen_news_quick(
            headline=payload["headline"],
            summary=payload.get("summary", ""),
            symbols=payload.get("symbols", []),
            ollama_url=self.config.gpu.ollama_url,
            model=self.config.gpu.quick_model,
        )

    def _investigate(self, payload: dict) -> dict:
        from service.core.news_screener import investigate_deep
        return investigate_deep(
            headline=payload["headline"],
            summary=payload.get("summary", ""),
            symbols=payload.get("symbols", []),
            ticker=payload.get("ticker", ""),
            ollama_url=self.config.gpu.ollama_url,
            model=self.config.gpu.deep_model,
        )

    def _watchlist_discovery(self, payload: dict) -> dict:
        from service.core.news_screener import evaluate_watchlist_addition
        from cli.position_risk import STRATEGY_THRESHOLDS
        symbol = payload.get("symbols", [""])[0] or payload.get("ticker", "")
        strategy = payload.get("strategy", "balanced")
        thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
        return evaluate_watchlist_addition(
            headline=payload["headline"],
            summary=payload.get("summary", ""),
            symbol=symbol,
            strategy=strategy,
            strategy_instruction=thresholds.get("instruction", ""),
            ollama_url=self.config.gpu.ollama_url,
            model=self.config.gpu.quick_model,
        )

    def _watchlist_prune(self, payload: dict) -> dict:
        from cli.position_risk import STRATEGY_THRESHOLDS
        strategy = payload.get("strategy", "balanced")
        thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
        stage = payload.get("stage", "quick")

        if stage == "deep":
            from service.core.news_screener import confirm_watchlist_prune
            return confirm_watchlist_prune(
                symbol=payload["symbol"],
                strategy=strategy,
                strategy_instruction=thresholds.get("instruction", ""),
                recent_headlines=payload.get("recent_headlines", []),
                quick_reasoning=payload.get("quick_reasoning", ""),
                ollama_url=self.config.gpu.ollama_url,
                model=self.config.gpu.deep_model,
            )
        else:
            from service.core.news_screener import evaluate_watchlist_prune
            return evaluate_watchlist_prune(
                symbol=payload["symbol"],
                strategy=strategy,
                strategy_instruction=thresholds.get("instruction", ""),
                recent_headlines=payload.get("recent_headlines", []),
                ollama_url=self.config.gpu.ollama_url,
                model=self.config.gpu.quick_model,
            )

    def _full_analysis(self, payload: dict) -> dict:
        from shared.analysis import run_single_ticker
        from shared.config import build_graph_config
        from pathlib import Path
        import json

        ticker = payload["ticker"]
        ollama_base = self.config.gpu.ollama_url.rstrip("/")
        if not ollama_base.endswith("/v1"):
            ollama_base += "/v1"

        config = build_graph_config(
            provider="ollama",
            quick_model=self.config.gpu.quick_model,
            deep_model=self.config.gpu.deep_model,
            backend_url=ollama_base,
        )

        result = run_single_ticker(
            ticker=ticker,
            config=config,
            past_context=payload.get("past_context", ""),
        )

        final_state = result["final_state"]

        # Save report to disk with date suffix (same format as CLI)
        analysis_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        reports_dir = Path("reports") / f"{ticker}_{analysis_date}"
        try:
            from cli.main import save_report_to_disk
            save_report_to_disk(final_state, ticker, reports_dir)
        except Exception:
            pass

        # Save the full state as JSON for the merge step (always latest per ticker)
        state_dir = Path("reports") / "_states"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / f"{ticker}.json"
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
        state_file.write_text(json.dumps(serializable_state, indent=2, default=str), encoding="utf-8")

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

        tickers_data = payload["tickers_data"]  # list of {ticker, decision, final_state}
        account_id = payload.get("account_id")
        strategy = payload.get("strategy", "balanced")
        portfolio = payload.get("portfolio")

        ollama_base = self.config.gpu.ollama_url.rstrip("/")
        if not ollama_base.endswith("/v1"):
            ollama_base += "/v1"

        config = build_graph_config(
            provider="ollama",
            quick_model=self.config.gpu.quick_model,
            deep_model=self.config.gpu.deep_model,
            backend_url=ollama_base,
        )

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

        # Validate merge report (N passes)
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

        # Generate allocation plan + concrete orders
        allocation_data = []
        proposed_orders = []
        allocation_reasoning = ""

        if portfolio:
            try:
                portfolio_dict = {
                    "holdings": portfolio.get("holdings", {}),
                    "cash": portfolio.get("cash", 0),
                }
                # Get current prices for order sizing
                quotes = {}
                for t in tickers_data:
                    fs = t.get("final_state", {})
                    # Try to get price from the state if available
                    if "current_price" in t:
                        quotes[t["ticker"]] = t["current_price"]

                # Fetch live quotes if available
                try:
                    from cli.alpaca_client import create_client, fetch_quotes
                    from service.app import _config
                    acct = _config.accounts.get(account_id) if _config else None
                    if acct:
                        client = create_client(acct.api_key, acct.api_secret, paper=acct.is_paper)
                        live_quotes = fetch_quotes(client, [t["ticker"] for t in tickers_data])
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

                # Compute current allocations for comparison
                holdings = portfolio_dict.get("holdings", {})
                cash = portfolio_dict.get("cash", 0)
                portfolio_value = sum(holdings.get(s, 0) * quotes.get(s, 0) for s in holdings) + cash

                allocation_data = []
                for a in allocation_plan.allocations:
                    current_qty = holdings.get(a.symbol, 0)
                    current_price = quotes.get(a.symbol, 0)
                    current_value = current_qty * current_price
                    current_pct = (current_value / portfolio_value * 100) if portfolio_value > 0 else 0
                    target_value = portfolio_value * a.pct / 100 if portfolio_value > 0 else 0

                    allocation_data.append({
                        "symbol": a.symbol,
                        "action": a.action,
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
        """Check if a cancel signal exists for this task."""
        return bool(self._redis.get(f"gpu:cancel:{task_id}"))

    def _publish_status(self, state: str, message: str):
        status = {
            "state": state,
            "message": message,
            "current_model": self._current_model,
            "task_count": self._task_count,
            "model_switches": self._model_switches,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        self._redis.set("gpu:worker:status", json.dumps(status))
        self._redis.publish(STATUS_CHANNEL, json.dumps(status))

    def _handle_shutdown(self, signum, frame):
        self._running = False


def main():
    config = load_config()
    worker = GpuWorker(config)
    worker.run()


if __name__ == "__main__":
    main()
