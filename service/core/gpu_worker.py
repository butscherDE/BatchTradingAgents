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

        self._publish_status("executing", f"{task_type} for {ticker or 'N/A'}")

        started_at = datetime.datetime.utcnow().isoformat()

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
            self._redis.publish(RESULT_CHANNEL, json.dumps({
                "task_id": task_id,
                "task_type": task_type,
                "ticker": ticker,
                "status": "failed",
                "error": str(e),
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

    def _full_analysis(self, payload: dict) -> dict:
        from shared.analysis import run_single_ticker
        from shared.config import build_graph_config

        ticker = payload["ticker"]
        config = build_graph_config(
            provider="ollama",
            quick_model=self.config.gpu.quick_model,
            deep_model=self.config.gpu.deep_model,
            backend_url=self.config.gpu.ollama_url,
        )

        result = run_single_ticker(
            ticker=ticker,
            config=config,
            past_context=payload.get("past_context", ""),
        )

        return {
            "ticker": ticker,
            "decision": result["decision"],
            "final_state": {
                "final_trade_decision": result["final_state"].get("final_trade_decision", ""),
                "market_report": result["final_state"].get("market_report", ""),
                "news_report": result["final_state"].get("news_report", ""),
                "sentiment_report": result["final_state"].get("sentiment_report", ""),
                "fundamentals_report": result["final_state"].get("fundamentals_report", ""),
            },
            "elapsed": result["elapsed"],
            "stats": result["stats"],
        }

    def _merge_and_allocate(self, payload: dict) -> dict:
        from shared.merge import generate_merge_report, validate_merge_report
        from shared.config import build_graph_config

        tickers_data = payload["tickers_data"]  # list of {ticker, decision, final_state}
        account_id = payload.get("account_id")
        strategy = payload.get("strategy", "balanced")
        portfolio = payload.get("portfolio")

        config = build_graph_config(
            provider="ollama",
            quick_model=self.config.gpu.quick_model,
            deep_model=self.config.gpu.deep_model,
            backend_url=self.config.gpu.ollama_url,
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

        validated_report = validate_merge_report(
            merge_report=merge_report,
            ticker_results=ticker_results,
            config=config,
            strategy=strategy,
            portfolio=portfolio,
        )

        return {
            "account_id": account_id,
            "merge_report": validated_report,
            "tickers": [t["ticker"] for t in tickers_data],
        }

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
