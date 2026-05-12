from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings
from pyhocon import ConfigFactory


class ProviderConfig(BaseSettings):
    type: str = "ollama"
    url: str = "http://localhost:11434"
    api_key: str = ""
    quick_model: str = "qwen3:8b"
    deep_model: str = "qwen3:32b"
    priority: int = 1
    max_queue: int = -1
    max_concurrent: int = 1


class GpuConfig(BaseSettings):
    quick_model: str = "qwen3:8b"
    deep_model: str = "qwen3:32b"
    ollama_url: str = "http://localhost:11434"


class EvaluationConfig(BaseSettings):
    debounce_seconds: int = 300
    sell_fraction: float = 0.5
    news_relevance_min_score: float = 0.6
    merge_checks: int = 1
    allocation_checks: int = 1


class WatchlistConfig(BaseSettings):
    dynamic_discovery: bool = False
    auto_prune: bool = False
    tickers: list[str] = []
    exclude: list[str] = []


class PollingConfig(BaseSettings):
    yfinance_enabled: bool = True
    yfinance_interval_minutes: int = 30
    yfinance_articles_per_ticker: int = 10
    yfinance_backoff_seconds: int = 60
    yfinance_max_failures: int = 5


class MetricsConfig(BaseSettings):
    enabled: bool = False
    influxdb_url: str = "http://localhost:8086"
    influxdb_token: str = ""
    influxdb_org: str = "trading"
    influxdb_bucket: str = "trading_metrics"


class AccountConfig(BaseSettings):
    brokerage: str = "alpaca"
    api_key: str = ""
    api_secret: str = ""
    is_paper: bool = True
    strategy: str = "balanced"
    watchlist: str = "aggressive"
    dynamic_discovery: bool = False
    auto_prune: bool = False
    max_watchlist: int = 20
    # E*TRADE specific
    oauth_token: str = ""
    oauth_token_secret: str = ""
    etrade_account_id_key: str = ""


class ServiceConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    database_path: str = "./data/service.db"
    redis_url: str = "redis://localhost:6379/0"
    auth_password: str = ""
    auth_secret: str = "change-me-in-production"
    gpu: GpuConfig = Field(default_factory=GpuConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    watchlist: WatchlistConfig = Field(default_factory=WatchlistConfig)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    accounts: dict[str, AccountConfig] = Field(default_factory=dict)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
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
    watchlist_raw = raw.get("watchlist", {})
    polling_raw = raw.get("polling", {})
    auth_raw = raw.get("auth", {})
    providers_raw = raw.get("providers", {})
    metrics_raw = raw.get("metrics", {})

    accounts = {}
    for name, acct in accounts_raw.items():
        accounts[name] = AccountConfig(
            brokerage=acct.get("brokerage", "alpaca"),
            api_key=acct.get("api_key", ""),
            api_secret=acct.get("api_secret", ""),
            is_paper=acct.get("is_paper", True),
            strategy=acct.get("strategy", "balanced"),
            watchlist=acct.get("watchlist", "aggressive"),
            dynamic_discovery=acct.get("dynamic_discovery", False),
            auto_prune=acct.get("auto_prune", False),
            max_watchlist=acct.get("max_watchlist", 20),
            oauth_token=acct.get("oauth_token", ""),
            oauth_token_secret=acct.get("oauth_token_secret", ""),
            etrade_account_id_key=acct.get("etrade_account_id_key", ""),
        )

    providers = {}
    for name, prov in providers_raw.items():
        providers[name] = ProviderConfig(
            type=prov.get("type", "ollama"),
            url=prov.get("url", "http://localhost:11434"),
            api_key=prov.get("api_key", ""),
            quick_model=prov.get("quick_model", "qwen3:8b"),
            deep_model=prov.get("deep_model", "qwen3:32b"),
            priority=prov.get("priority", 1),
            max_queue=prov.get("max_queue", -1),
            max_concurrent=prov.get("max_concurrent", 1),
        )

    # Backward compat: if no providers defined, synthesize from gpu.* block
    if not providers:
        providers = {"local": ProviderConfig(
            type="ollama",
            url=gpu_raw.get("ollama_url", "http://localhost:11434"),
            quick_model=gpu_raw.get("quick_model", "qwen3:8b"),
            deep_model=gpu_raw.get("deep_model", "qwen3:32b"),
            priority=1,
            max_queue=-1,
            max_concurrent=1,
        )}

    return ServiceConfig(
        host=service_raw.get("host", "0.0.0.0"),
        port=service_raw.get("port", 8000),
        database_path=db_raw.get("path", "./data/service.db"),
        redis_url=redis_raw.get("url", "redis://localhost:6379/0"),
        auth_password=auth_raw.get("password", ""),
        auth_secret=auth_raw.get("secret", "change-me-in-production"),
        gpu=GpuConfig(**gpu_raw),
        evaluation=EvaluationConfig(**eval_raw),
        watchlist=WatchlistConfig(
            dynamic_discovery=watchlist_raw.get("dynamic_discovery", False),
            auto_prune=watchlist_raw.get("auto_prune", False),
            tickers=watchlist_raw.get("tickers", []),
            exclude=watchlist_raw.get("exclude", []),
        ),
        polling=PollingConfig(**polling_raw),
        metrics=MetricsConfig(**metrics_raw),
        accounts=accounts,
        providers=providers,
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
