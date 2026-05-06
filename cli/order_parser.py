from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


_FULL_EXIT_SYNONYMS = {"exit", "liquidate", "close", "dump"}
_SELL_SYNONYMS = {"sell", "trim", "reduce", "underweight", "decrease", "lighten"}
_BUY_SYNONYMS = {"buy", "add", "enter", "overweight", "increase", "accumulate"}
_HOLD_SYNONYMS = {"hold", "keep", "maintain", "neutral", "stay"}


class TickerAllocation(BaseModel):
    symbol: str = Field(description="Ticker symbol")
    action: Literal["buy", "sell", "hold"] = Field(description="Direction")
    pct: float = Field(
        description=(
            "Target allocation as a percentage of total portfolio value (0-100). "
            "For sell: set to 0 to exit entirely, or a reduced target %. "
            "For hold: set to the current allocation % (no change). "
            "For buy: set to the desired target %."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_action(cls, data):
        if not isinstance(data, dict):
            return data
        raw = data.get("action")
        if not isinstance(raw, str):
            return data
        norm = raw.strip().lower()
        if norm in _FULL_EXIT_SYNONYMS:
            data["action"] = "sell"
            data["pct"] = 0.0
        elif norm in _SELL_SYNONYMS:
            data["action"] = "sell"
        elif norm in _BUY_SYNONYMS:
            data["action"] = "buy"
        elif norm in _HOLD_SYNONYMS:
            data["action"] = "hold"
        return data


class AllocationPlan(BaseModel):
    allocations: list[TickerAllocation] = Field(
        description="Target allocation for every ticker mentioned in the report.",
    )
    cash_pct: float = Field(
        description="Target cash allocation as percentage of total portfolio (0-100).",
    )
    reasoning: str = Field(
        description="Brief explanation of the allocation rationale.",
    )


class TradeOrder(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    qty: int


class TradePlan(BaseModel):
    orders: list[TradeOrder]
    reasoning: str


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


def get_llm(config):
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
    return client.get_llm()


def _build_holdings_context(portfolio_dict, quotes):
    lines = []
    total_value = 0.0
    for sym, qty in portfolio_dict["holdings"].items():
        price = quotes.get(sym)
        if price is not None:
            val = qty * price
            total_value += val
            lines.append(f"  - {sym}: {qty} shares @ ${price:,.2f} = ${val:,.2f}")
        else:
            lines.append(f"  - {sym}: {qty} shares (price unavailable)")
    cash = portfolio_dict["cash"]
    total_value += cash
    return "\n".join(lines) if lines else "  (no current holdings)", cash, total_value


def _stage1_allocations(
    llm,
    merge_report: str,
    portfolio_dict: dict,
    quotes: dict[str, float],
    pending: list[dict],
    strategy: Optional[str] = None,
    tax_context_str: str = "",
    risk_context_str: str = "",
) -> AllocationPlan:
    holdings_str, cash, total_value = _build_holdings_context(portfolio_dict, quotes)

    quotes_lines = []
    for sym, price in quotes.items():
        if sym not in portfolio_dict["holdings"]:
            quotes_lines.append(f"  - {sym}: ${price:,.2f}")
    quotes_str = "\n".join(quotes_lines) if quotes_lines else "  (none)"

    pending_str = format_pending_orders(pending)

    strategy_block = ""
    if strategy:
        strategy_block = f"\n**Investment Strategy:** {strategy}\nApply this risk profile when deciding allocations.\n"

    tax_block = f"\n{tax_context_str}\n" if tax_context_str else ""
    risk_block = f"\n{risk_context_str}\n" if risk_context_str else ""

    # Compute current allocation percentages for context
    alloc_lines = []
    for sym, qty in portfolio_dict["holdings"].items():
        price = quotes.get(sym)
        if price is not None and total_value > 0:
            pct = (qty * price / total_value) * 100
            alloc_lines.append(f"  - {sym}: {pct:.1f}%")
    if total_value > 0:
        alloc_lines.append(f"  - CASH: {(cash / total_value) * 100:.1f}%")
    current_alloc_str = "\n".join(alloc_lines) if alloc_lines else "  (empty)"

    prompt = f"""You are a portfolio allocation engine. Given the analysis report and current portfolio, decide target allocation percentages for each ticker.

**Current Holdings:**
{holdings_str}

**Cash:** ${cash:,.2f}
**Total Portfolio Value:** ${total_value:,.2f}

**Current Allocation:**
{current_alloc_str}

{pending_str}

**Quotes for Tickers Not in Portfolio:**
{quotes_str}
{tax_block}{risk_block}{strategy_block}
**Cross-Ticker Analysis Report:**

{merge_report}

---

**Instructions:**
- For each ticker in the report, output a target allocation percentage of total portfolio value and an action (buy, sell, hold).
- Sell means reduce or exit: set pct to 0 for full exit, or a lower % to trim.
- Buy means add or enter: set pct to desired target.
- Hold means keep current allocation: set pct to the current %.
- All allocation percentages plus cash_pct MUST sum to 100.
- Do not leave excessive cash. Only allocate to cash what the strategy or report explicitly justifies.
- Account for pending orders — do not allocate to tickers with conflicting open orders.
- Be decisive and follow the report's ratings."""

    structured_llm = llm.with_structured_output(AllocationPlan)
    return structured_llm.invoke(prompt)


def _validate_allocation(
    llm,
    allocation: AllocationPlan,
    merge_report: str,
    strategy: Optional[str] = None,
) -> AllocationPlan:
    alloc_lines = []
    for a in allocation.allocations:
        alloc_lines.append(f"  - {a.symbol}: {a.action} → {a.pct:.1f}%")
    alloc_lines.append(f"  - CASH: {allocation.cash_pct:.1f}%")
    alloc_str = "\n".join(alloc_lines)

    total = sum(a.pct for a in allocation.allocations) + allocation.cash_pct

    strategy_block = f"\n**Active Strategy:** {strategy}\n" if strategy else ""

    prompt = f"""You are a portfolio compliance officer validating that an allocation plan matches the cross-ticker analysis report that produced it.

**Current Allocation Plan:**
{alloc_str}
Total: {total:.1f}%
Reasoning: {allocation.reasoning}
{strategy_block}
**Cross-Ticker Analysis Report (source of truth for rankings and recommendations):**

{merge_report}

---

**Validation Checklist:**
1. Ranking-allocation consistency: if the report ranks Ticker A higher than Ticker B, A's allocation % should be ≥ B's.
2. Action-rating alignment: a ticker rated Buy/Overweight in the report should have action "buy" with meaningful %. A ticker rated Sell/Underweight should have action "sell" or reduced %.
3. All Buy/Overweight-rated tickers from the report's ranking are present in the allocation.
4. Strategy compliance: the allocations match the stated investment strategy.
5. Percentages sum to 100 (±2% for rounding).
6. No ticker appears that wasn't discussed in the report.

**Your task:** If you find issues, output a CORRECTED allocation with the same format. Fix percentage inconsistencies, add missing tickers, and ensure ranking order is respected. If the allocation passes all checks, return it unchanged."""

    structured_llm = llm.with_structured_output(AllocationPlan)
    return structured_llm.invoke(prompt)


def _stage2_orders(
    allocation: AllocationPlan,
    portfolio_dict: dict,
    quotes: dict[str, float],
) -> TradePlan:
    holdings = portfolio_dict["holdings"]
    cash = portfolio_dict["cash"]

    total_value = cash
    for sym, qty in holdings.items():
        price = quotes.get(sym)
        if price is not None:
            total_value += qty * price

    sell_orders = []
    buy_targets = []

    for alloc in allocation.allocations:
        if alloc.action == "hold":
            continue

        sym = alloc.symbol
        price = quotes.get(sym)
        if price is None or price <= 0:
            continue

        current_qty = holdings.get(sym, 0)
        target_value = total_value * (alloc.pct / 100.0)
        target_qty = int(target_value / price)

        if alloc.action == "sell":
            sell_qty = int(current_qty) - target_qty
            if sell_qty > 0:
                sell_qty = min(sell_qty, int(current_qty))
                sell_orders.append(TradeOrder(symbol=sym, side="sell", qty=sell_qty))
        elif alloc.action == "buy":
            buy_qty = target_qty - int(current_qty)
            if buy_qty > 0:
                buy_targets.append({"symbol": sym, "qty": buy_qty, "price": price})

    sell_proceeds = sum(o.qty * quotes.get(o.symbol, 0) for o in sell_orders)
    effective_cash = cash + sell_proceeds

    total_buy_cost = sum(b["qty"] * b["price"] for b in buy_targets)

    if total_buy_cost < effective_cash and buy_targets:
        leftover = effective_cash - total_buy_cost
        buy_values = [b["qty"] * b["price"] for b in buy_targets]
        for i, b in enumerate(buy_targets):
            share = buy_values[i] / total_buy_cost if total_buy_cost > 0 else 1.0 / len(buy_targets)
            extra_dollars = leftover * share
            extra_shares = int(extra_dollars / b["price"])
            b["qty"] += extra_shares

    total_buy_cost = sum(b["qty"] * b["price"] for b in buy_targets)
    if total_buy_cost > effective_cash and buy_targets:
        scale = effective_cash / total_buy_cost
        for b in buy_targets:
            b["qty"] = int(b["qty"] * scale)

    buy_orders = [
        TradeOrder(symbol=b["symbol"], side="buy", qty=b["qty"])
        for b in buy_targets if b["qty"] > 0
    ]

    return TradePlan(orders=sell_orders + buy_orders, reasoning=allocation.reasoning)


def parse_orders(
    merge_report: str,
    portfolio_dict: dict,
    quotes: dict[str, float],
    pending: list[dict],
    config: dict,
    strategy: Optional[str] = None,
    tax_context_str: str = "",
    allocation_checks: int = 0,
    risk_context_str: str = "",
    on_stage1_done: Optional[callable] = None,
    on_check_start: Optional[callable] = None,
    on_check_done: Optional[callable] = None,
) -> tuple[TradePlan, AllocationPlan]:
    llm = get_llm(config)

    allocation = _stage1_allocations(
        llm, merge_report, portfolio_dict, quotes, pending, strategy,
        tax_context_str=tax_context_str,
        risk_context_str=risk_context_str,
    )
    if on_stage1_done:
        on_stage1_done()

    for i in range(allocation_checks):
        if on_check_start:
            on_check_start(i + 1, allocation_checks)
        allocation = _validate_allocation(llm, allocation, merge_report, strategy)
        if on_check_done:
            on_check_done()

    return _stage2_orders(allocation, portfolio_dict, quotes), allocation
