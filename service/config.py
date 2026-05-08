from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings
from pyhocon import ConfigFactory


class GpuConfig(BaseSettings):
    quick_model: str = "qwen3:8b"
    deep_model: str = "qwen3:32b"
    ollama_url: str = "http://localhost:11434"
    max_batch_before_yield: int = 50


class EvaluationConfig(BaseSettings):
    debounce_seconds: int = 300
    sell_fraction: float = 0.5
    news_relevance_min_score: float = 0.6


class AccountConfig(BaseSettings):
    api_key: str = ""
    api_secret: str = ""
    is_paper: bool = True
    strategy: str = "balanced"
    watchlist: str = "aggressive"


class ServiceConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    database_path: str = "./data/service.db"
    redis_url: str = "redis://localhost:6379/0"
    gpu: GpuConfig = Field(default_factory=GpuConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    accounts: dict[str, AccountConfig] = Field(default_factory=dict)
    news_symbols: list[str] = Field(default_factory=lambda: ["*"])


def load_config(config_path: Optional[Path] = None) -> ServiceConfig:
    if config_path is None:
        config_path = Path("config/application.conf")

    if not config_path.exists():
        return ServiceConfig()

    hocon = ConfigFactory.parse_file(str(config_path), resolve=True)
    raw = _hocon_to_dict(hocon)

    gpu_raw = raw.get("gpu", {})
    eval_raw = raw.get("evaluation", {})
    service_raw = raw.get("service", {})
    accounts_raw = raw.get("accounts", {})
    streams_raw = raw.get("streams", {})
    db_raw = raw.get("database", {})
    redis_raw = raw.get("redis", {})

    accounts = {}
    for name, acct in accounts_raw.items():
        accounts[name] = AccountConfig(
            api_key=acct.get("api_key", ""),
            api_secret=acct.get("api_secret", ""),
            is_paper=acct.get("is_paper", True),
            strategy=acct.get("strategy", "balanced"),
            watchlist=acct.get("watchlist", "aggressive"),
        )

    return ServiceConfig(
        host=service_raw.get("host", "0.0.0.0"),
        port=service_raw.get("port", 8000),
        database_path=db_raw.get("path", "./data/service.db"),
        redis_url=redis_raw.get("url", "redis://localhost:6379/0"),
        gpu=GpuConfig(**gpu_raw),
        evaluation=EvaluationConfig(**eval_raw),
        accounts=accounts,
        news_symbols=streams_raw.get("news_symbols", ["*"]),
    )


def _hocon_to_dict(config) -> dict:
    result = {}
    for key in config:
        val = config[key]
        if hasattr(val, "__iter__") and hasattr(val, "keys"):
            result[key] = _hocon_to_dict(val)
        elif isinstance(val, list):
            result[key] = list(val)
        else:
            result[key] = val
    return result
