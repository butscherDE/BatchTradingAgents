import datetime


TAX_BRACKETS = {
    "top": {"short_term": 0.37, "long_term": 0.20},
    "mid": {"short_term": 0.24, "long_term": 0.15},
    "low": {"short_term": 0.12, "long_term": 0.00},
    "none": {"short_term": 0.00, "long_term": 0.00},
}


def holding_period_type(earliest_fill_date: str) -> str:
    if not earliest_fill_date:
        return "unknown"
    fill = datetime.datetime.strptime(earliest_fill_date, "%Y-%m-%d").date()
    days_held = (datetime.date.today() - fill).days
    return "long_term" if days_held >= 365 else "short_term"


def compute_tax_context(
    position_details: dict[str, dict],
    current_prices: dict[str, float],
    bracket: str = "top",
) -> dict[str, dict]:
    rates = TAX_BRACKETS.get(bracket, TAX_BRACKETS["top"])

    result = {}
    for sym, details in position_details.items():
        price = current_prices.get(sym)
        entry = details.get("avg_entry_price")
        if price is None or entry is None:
            continue

        gain_per_share = price - entry
        period = details.get("holding_period", "unknown")

        if period == "long_term":
            rate = rates["long_term"]
        elif period == "short_term":
            rate = rates["short_term"]
        else:
            rate = rates["short_term"]

        if gain_per_share > 0:
            tax_per_share = gain_per_share * rate
        else:
            tax_per_share = 0.0

        net_proceeds = price - tax_per_share
        qty = details.get("qty", 0)
        total_tax = tax_per_share * qty

        result[sym] = {
            "gain_per_share": gain_per_share,
            "holding_period": period,
            "tax_rate": rate,
            "tax_per_share": tax_per_share,
            "net_proceeds_per_share": net_proceeds,
            "total_unrealized_gain": gain_per_share * qty,
            "total_estimated_tax": total_tax,
        }

    return result


def format_tax_context_for_prompt(tax_ctx: dict[str, dict]) -> str:
    if not tax_ctx:
        return ""
    lines = ["**Tax Impact on Sells:**"]
    for sym, info in sorted(tax_ctx.items()):
        gain = info["gain_per_share"]
        period = info["holding_period"].replace("_", "-")
        rate_pct = info["tax_rate"] * 100

        if gain > 0:
            lines.append(
                f"  - {sym}: {period} gain, ${gain:+,.2f}/share gain, "
                f"~${info['tax_per_share']:,.2f}/share tax ({rate_pct:.0f}%), "
                f"net proceeds ${info['net_proceeds_per_share']:,.2f}/share"
            )
        elif gain < 0:
            lines.append(
                f"  - {sym}: {period} loss, ${gain:,.2f}/share, "
                f"no tax on sale — loss may offset gains elsewhere"
            )
        else:
            lines.append(f"  - {sym}: {period}, breakeven, no tax impact")

    lines.append(
        "\nFactor in tax costs when deciding to sell. "
        "Short-term gains are taxed at a much higher rate than long-term gains. "
        "Tax-loss harvesting (selling losers to offset gains) is valuable."
    )
    return "\n".join(lines)


def format_tax_context_for_portfolio(
    tax_ctx: dict[str, dict],
    position_details: dict[str, dict],
) -> dict[str, str]:
    summaries = {}
    for sym, info in tax_ctx.items():
        details = position_details.get(sym, {})
        period = info["holding_period"].replace("_", "-")
        gain = info["total_unrealized_gain"]
        rate_pct = info["tax_rate"] * 100
        tax = info["total_estimated_tax"]

        if gain > 0:
            summaries[sym] = (
                f"{period}, unrealized gain ${gain:+,.2f}, "
                f"est. tax on sale ~${tax:,.2f} ({rate_pct:.0f}%)"
            )
        elif gain < 0:
            summaries[sym] = f"{period}, unrealized loss ${gain:,.2f}, tax-loss harvesting candidate"
        else:
            summaries[sym] = f"{period}, breakeven"

    return summaries
