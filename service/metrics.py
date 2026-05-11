"""Push-based metrics via InfluxDB. Per-process singleton — safe for multiprocessing."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_UNINITIALIZED = object()
_writer = _UNINITIALIZED
_config = None

COST_PER_MILLION = {
    "Qwen/Qwen3-235B-A22B-Thinking-2507": (0.071, 0.10),
    "Qwen/Qwen3.5-35B-A3B": (0.14, 1.00),
}


def _get_writer():
    global _writer, _config
    if _writer is not _UNINITIALIZED:
        return _writer

    from service.config import load_config
    cfg = load_config()
    _config = cfg.metrics

    if not _config.enabled:
        _writer = None
        return None

    try:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        client = InfluxDBClient(
            url=_config.influxdb_url,
            token=_config.influxdb_token,
            org=_config.influxdb_org,
        )
        _writer = client.write_api(write_options=SYNCHRONOUS)
    except Exception as e:
        logger.warning(f"Failed to initialize metrics client: {e}")
        _writer = None

    return _writer


def _write(measurement: str, tags: dict, fields: dict):
    try:
        writer = _get_writer()
        if writer is None:
            return
        from influxdb_client import Point

        p = Point(measurement)
        for k, v in tags.items():
            p = p.tag(k, v)
        for k, v in fields.items():
            p = p.field(k, v)
        writer.write(bucket=_config.influxdb_bucket, record=p)
    except Exception as e:
        logger.debug(f"Metrics write failed: {e}")


def record_task_completed(
    task_type: str,
    provider: str,
    model_tier: str,
    ticker: Optional[str],
    status: str,
    duration_s: float,
):
    _write(
        "task_completed",
        {"type": task_type, "provider": provider, "model_tier": model_tier, "ticker": ticker or "unknown", "status": status},
        {"duration_s": round(duration_s, 2), "count": 1},
    )


def record_token_usage(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
):
    total = prompt_tokens + completion_tokens
    cost = _estimate_cost(model, prompt_tokens, completion_tokens)
    _write(
        "token_usage",
        {"provider": provider, "model": model},
        {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": total, "cost_usd": cost},
    )


def record_news_ingested(source: str, ticker: str):
    _write(
        "news_ingested",
        {"source": source, "ticker": ticker},
        {"count": 1},
    )


def record_queue_depth(provider: str, depth: int):
    _write(
        "queue_depth",
        {"provider": provider},
        {"depth": depth},
    )


def record_worker_utilization(provider: str, active: int, capacity: int):
    _write(
        "worker_util",
        {"provider": provider},
        {"active": active, "capacity": capacity},
    )


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = COST_PER_MILLION.get(model)
    if rates is None:
        for key, val in COST_PER_MILLION.items():
            if key in model or model in key:
                rates = val
                break
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
