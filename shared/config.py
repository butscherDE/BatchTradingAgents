"""Shared configuration builder for TradingAgentsGraph."""

from tradingagents.default_config import DEFAULT_CONFIG


def build_graph_config(
    provider: str,
    quick_model: str,
    deep_model: str,
    backend_url: str | None = None,
    research_depth: int = 1,
    language: str = "English",
    checkpoint: bool = False,
    google_thinking_level: str | None = None,
    openai_reasoning_effort: str | None = None,
    anthropic_effort: str | None = None,
) -> dict:
    """Build a TradingAgentsGraph config dict from parameters."""
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = research_depth
    config["max_risk_discuss_rounds"] = research_depth
    config["quick_think_llm"] = quick_model
    config["deep_think_llm"] = deep_model
    config["backend_url"] = backend_url
    config["llm_provider"] = provider.lower()
    config["google_thinking_level"] = google_thinking_level
    config["openai_reasoning_effort"] = openai_reasoning_effort
    config["anthropic_effort"] = anthropic_effort
    config["output_language"] = language
    config["checkpoint_enabled"] = checkpoint
    return config
