from typing import Optional


STRATEGY_THRESHOLDS = {
    "conservative": {
        "warning_pct": 15,
        "critical_pct": 25,
        "concentration_warn_pct": 25,
        "concentration_crit_pct": 40,
        "instruction": (
            "Positions with >15% drawdown from entry are strong sell signals — exit unless "
            "the analyst case presents an imminent, specific recovery catalyst with a clear timeline. "
            "Positions with >25% drawdown are critical: recommend exit regardless of analyst outlook."
        ),
    },
    "balanced": {
        "warning_pct": 20,
        "critical_pct": 35,
        "concentration_warn_pct": 35,
        "concentration_crit_pct": 50,
        "instruction": (
            "Positions with >20% drawdown from entry are sell signals — reduce or exit unless "
            "the analyst report explicitly justifies holding through with a strong thesis. "
            "Positions with >35% drawdown are critical: strongly recommend exit."
        ),
    },
    "aggressive": {
        "warning_pct": 35,
        "critical_pct": 50,
        "concentration_warn_pct": 50,
        "concentration_crit_pct": 70,
        "instruction": (
            "Drawdowns below 35% are acceptable volatility for this strategy. "
            "Only flag as a sell signal if the drawdown exceeds 35% AND the underlying thesis "
            "appears broken (not just price action). Drawdowns >50% warrant exit review."
        ),
    },
    "yolo": {
        "warning_pct": 50,
        "critical_pct": 70,
        "concentration_warn_pct": 70,
        "concentration_crit_pct": 90,
        "instruction": (
            "Drawdowns are context only — a large drawdown on a pre-catalyst name may be an add "
            "opportunity, not a sell signal. Only recommend exit if the underlying thesis has "
            "fundamentally collapsed (regulatory rejection, failed trial, fraud, etc.), "
            "not merely because the price is down."
        ),
    },
    "mean": {
        "warning_pct": 30,
        "critical_pct": 45,
        "concentration_warn_pct": 40,
        "concentration_crit_pct": 60,
        "instruction": (
            "These positions are tied to macro policy tailwinds (defense spending, deregulation, "
            "fossil fuel subsidies). A drawdown >30% may signal the political thesis is weakening — "
            "review whether the regulatory or geopolitical catalyst is still intact. "
            "Exit at >45% only if the policy environment has reversed (e.g., defense cuts, "
            "re-regulation, international de-escalation)."
        ),
    },
}


def format_position_risk_context(
    position_details: dict[str, dict],
    current_prices: dict[str, float],
    strategy: str = "balanced",
) -> str:
    thresholds = STRATEGY_THRESHOLDS.get(strategy, STRATEGY_THRESHOLDS["balanced"])
    warning_pct = thresholds["warning_pct"]
    critical_pct = thresholds["critical_pct"]
    instruction = thresholds["instruction"]

    lines = ["**Position Performance (from entry):**"]
    has_entries = False

    for sym, details in sorted(position_details.items()):
        entry = details.get("avg_entry_price")
        price = current_prices.get(sym)
        if entry is None or price is None or entry <= 0:
            continue

        has_entries = True
        change_pct = ((price - entry) / entry) * 100
        tag = ""
        if change_pct <= -critical_pct:
            tag = " [CRITICAL]"
        elif change_pct <= -warning_pct:
            tag = " [WARNING]"

        lines.append(
            f"  - {sym}: entry ${entry:,.2f}, now ${price:,.2f}, "
            f"{'gain' if change_pct >= 0 else 'drawdown'} {change_pct:+.1f}%{tag}"
        )

    if not has_entries:
        return ""

    lines.append("")
    lines.append(f"**Stop-Loss Guidance ({strategy}):** {instruction}")

    return "\n".join(lines)
