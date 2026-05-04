from typing import Literal, Optional

from pydantic import BaseModel, Field


class TradeOrder(BaseModel):
    symbol: str = Field(description="Ticker symbol to trade")
    side: Literal["buy", "sell"] = Field(description="Order direction")
    qty: float = Field(description="Number of shares to trade")


class TradePlan(BaseModel):
    orders: list[TradeOrder] = Field(
        description=(
            "Concrete orders to execute. Every order must have a symbol, side, "
            "and whole-share quantity. Sell qty must not exceed current holdings. "
            "Total buy notional must not exceed available cash."
        ),
    )
    reasoning: str = Field(
        description="Brief explanation of why these specific orders were chosen.",
    )


def format_pending_orders(pending: list[dict]) -> str:
    if not pending:
        return "**Pending Orders:** None"
    lines = ["**Pending Orders:**"]
    for o in pending:
        parts = [f"{o['side'].upper()} {o.get('qty') or '?'} {o['symbol']}"]
        parts.append(f"({o['type']}, {o['status']})")
        if o.get("filled_qty"):
            parts.append(f"filled {o['filled_qty']}/{o.get('qty', '?')}")
        lines.append("  - " + " ".join(parts))
    return "\n".join(lines)


def parse_orders(
    merge_report: str,
    portfolio_dict: dict,
    quotes: dict[str, float],
    pending: list[dict],
    config: dict,
) -> TradePlan:
    from tradingagents.llm_clients import create_llm_client

    llm_kwargs = {}
    provider = config.get("llm_provider", "").lower()
    if provider == "google" and config.get("google_thinking_level"):
        llm_kwargs["thinking_level"] = config["google_thinking_level"]
    elif provider == "openai" and config.get("openai_reasoning_effort"):
        llm_kwargs["reasoning_effort"] = config["openai_reasoning_effort"]
    elif provider == "anthropic" and config.get("anthropic_effort"):
        llm_kwargs["effort"] = config["anthropic_effort"]

    client = create_llm_client(
        provider=config["llm_provider"],
        model=config["deep_think_llm"],
        base_url=config.get("backend_url"),
        **llm_kwargs,
    )
    llm = client.get_llm()

    holdings_lines = []
    for sym, qty in portfolio_dict["holdings"].items():
        holdings_lines.append(f"  - {sym}: {qty} shares")
    holdings_str = "\n".join(holdings_lines) if holdings_lines else "  (no current holdings)"

    quotes_lines = []
    for sym, price in quotes.items():
        quotes_lines.append(f"  - {sym}: ${price:,.2f}")
    quotes_str = "\n".join(quotes_lines) if quotes_lines else "  (no additional quotes)"

    pending_str = format_pending_orders(pending)

    prompt = f"""You are a portfolio execution engine. Given the cross-ticker analysis report and the current portfolio state below, produce a concrete list of market orders to execute.

**Current Holdings:**
{holdings_str}

**Available Cash:** ${portfolio_dict['cash']:,.2f}

{pending_str}

**Latest Quotes for Tickers Not in Portfolio:**
{quotes_str}

**Cross-Ticker Analysis Report:**

{merge_report}

---

**Rules:**
- Only output orders that the analysis report supports. If the report says Hold, do not trade that ticker.
- Sell quantity must not exceed the current holding quantity for that symbol.
- Total buy cost (qty * approximate price) must not exceed available cash.
- Do not duplicate or conflict with any pending orders listed above.
- Use whole share quantities only.
- If no action is warranted, return an empty orders list.
- Be decisive: if the report recommends buying or selling, produce the order."""

    structured_llm = llm.with_structured_output(TradePlan)
    return structured_llm.invoke(prompt)
