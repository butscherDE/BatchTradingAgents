"""Shared analysis pipeline runner — no Rich/CLI dependencies."""

import datetime
from pathlib import Path
from time import time
from typing import Optional

from tradingagents.graph.trading_graph import TradingAgentsGraph


ANALYST_ORDER = ["market", "social", "news", "fundamentals"]


def run_single_ticker(
    ticker: str,
    config: dict,
    analysis_date: Optional[str] = None,
    selected_analysts: Optional[list[str]] = None,
    past_context: str = "",
    output_dir: Optional[Path] = None,
    on_progress: Optional[callable] = None,
) -> dict:
    """Run the full multi-agent analysis pipeline for a single ticker.

    Returns dict with: final_state, decision, elapsed, stats
    """
    if analysis_date is None:
        analysis_date = datetime.date.today().strftime("%Y-%m-%d")

    if selected_analysts is None:
        selected_analysts = ANALYST_ORDER.copy()

    graph = TradingAgentsGraph(selected_analysts=selected_analysts, config=config)

    start_time = time()

    init_state = graph.propagator.create_initial_state(
        ticker, analysis_date, past_context=past_context
    )

    from cli.stats_handler import StatsCallbackHandler
    stats_handler = StatsCallbackHandler()
    args = graph.propagator.get_graph_args(callbacks=[stats_handler])

    trace = []
    for chunk in graph.graph.stream(init_state, **args):
        trace.append(chunk)
        if on_progress:
            on_progress(chunk)

    final_state = trace[-1]
    decision = graph.process_signal(final_state["final_trade_decision"])

    elapsed = time() - start_time

    if output_dir is not None:
        from cli.utils import save_report_to_disk
        save_report_to_disk(final_state, ticker, output_dir)

    return {
        "final_state": final_state,
        "decision": decision,
        "elapsed": elapsed,
        "stats": stats_handler.get_stats(),
    }
