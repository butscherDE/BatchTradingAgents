import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from pydantic import ValidationError
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich import box

from cli.order_parser import (
    AllocationPlan,
    TickerAllocation,
    TradePlan,
    _stage2_orders,
    format_pending_orders,
    get_llm,
)
from tradingagents.dataflows.utils import safe_ticker_component


SECTION_FILE_MAP = {
    "complete": "complete_report.md",
    "market": "1_analysts/market.md",
    "sentiment": "1_analysts/sentiment.md",
    "news": "1_analysts/news.md",
    "fundamentals": "1_analysts/fundamentals.md",
    "bull": "2_research/bull.md",
    "bear": "2_research/bear.md",
    "research_manager": "2_research/manager.md",
    "trader": "3_trading/trader.md",
    "aggressive": "4_risk/aggressive.md",
    "conservative": "4_risk/conservative.md",
    "neutral": "4_risk/neutral.md",
    "portfolio_decision": "5_portfolio/decision.md",
}

EXIT_KEYWORDS = {"/exit", "/quit", "/done", "/q"}
EXECUTE_KEYWORDS = {"/execute", "/exec", "/go"}
HISTORY_SOFT_CAP = 24
TOOL_LOOP_CAP = 5
TOOL_RESULT_CHAR_CAP = 32_000


@dataclass
class TradeChatContext:
    merge_report: str
    portfolio_dict: dict
    position_prices: dict
    quotes: dict
    pending: list[dict]
    strategy_text: str
    tax_prompt_str: str
    risk_context_str: str
    config: dict
    mode: str
    output_dir: Path

    trade_plan: TradePlan
    allocation_plan: AllocationPlan
    original_trade_plan: TradePlan
    original_allocation_plan: AllocationPlan

    transcript_path: Optional[Path] = None
    execute_requested: bool = False


def _find_latest_ticker_dir(output_dir: Path, ticker: str) -> Optional[Path]:
    if not output_dir.is_dir():
        return None
    candidates = sorted(
        d for d in output_dir.iterdir()
        if d.is_dir() and d.name.startswith(f"{ticker}_")
    )
    return candidates[-1] if candidates else None


def _render_holdings(ctx: TradeChatContext) -> str:
    holdings = ctx.portfolio_dict["holdings"]
    cash = ctx.portfolio_dict["cash"]
    prices = {**ctx.position_prices, **ctx.quotes}

    total = cash
    rows = []
    for sym, qty in holdings.items():
        price = prices.get(sym)
        if price is not None:
            val = qty * price
            total += val
            rows.append((sym, qty, price, val))
        else:
            rows.append((sym, qty, None, None))

    lines = []
    for sym, qty, price, val in rows:
        if price is None:
            lines.append(f"  - {sym}: {qty} shares (price unavailable)")
        else:
            pct = (val / total * 100) if total > 0 else 0
            lines.append(f"  - {sym}: {qty} shares @ ${price:,.2f} = ${val:,.2f} ({pct:.1f}%)")
    cash_pct = (cash / total * 100) if total > 0 else 0
    lines.append(f"  - CASH: ${cash:,.2f} ({cash_pct:.1f}%)")
    lines.append(f"  - TOTAL: ${total:,.2f}")
    return "\n".join(lines)


def _render_quotes(ctx: TradeChatContext) -> str:
    held = set(ctx.portfolio_dict["holdings"])
    lines = [f"  - {s}: ${p:,.2f}" for s, p in ctx.quotes.items() if s not in held]
    return "\n".join(lines) if lines else "  (none)"


def _render_allocation(plan: AllocationPlan) -> str:
    lines = [f"  - {a.symbol}: {a.action} → {a.pct:.1f}%" for a in plan.allocations]
    lines.append(f"  - CASH: {plan.cash_pct:.1f}%")
    return "\n".join(lines)


def _render_orders(plan: TradePlan) -> str:
    if not plan.orders:
        return "  (no orders)"
    return "\n".join(f"  - {o.side.upper()} {int(o.qty)} {o.symbol}" for o in plan.orders)


def _render_projected(ctx: TradeChatContext) -> str:
    holdings = dict(ctx.portfolio_dict["holdings"])
    cash = ctx.portfolio_dict["cash"]
    prices = {**ctx.position_prices, **ctx.quotes}

    for o in ctx.pending:
        sym = o["symbol"]
        remaining = (o.get("qty") or 0) - o.get("filled_qty", 0)
        if remaining <= 0:
            continue
        price = prices.get(sym, 0)
        if o["side"] == "buy":
            holdings[sym] = holdings.get(sym, 0) + remaining
            cash -= remaining * price
        else:
            holdings[sym] = holdings.get(sym, 0) - remaining
            cash += remaining * price

    for o in ctx.trade_plan.orders:
        price = prices.get(o.symbol, 0)
        if o.side == "buy":
            holdings[o.symbol] = holdings.get(o.symbol, 0) + int(o.qty)
            cash -= int(o.qty) * price
        else:
            holdings[o.symbol] = holdings.get(o.symbol, 0) - int(o.qty)
            cash += int(o.qty) * price

    holdings = {s: q for s, q in holdings.items() if q > 0}
    total = cash + sum(q * prices.get(s, 0) for s, q in holdings.items() if prices.get(s) is not None)

    lines = []
    for sym, qty in sorted(holdings.items()):
        price = prices.get(sym)
        if price is None:
            lines.append(f"  - {sym}: {qty} shares (price unavailable)")
        else:
            val = qty * price
            pct = (val / total * 100) if total > 0 else 0
            lines.append(f"  - {sym}: {qty} shares = ${val:,.2f} ({pct:.1f}%)")
    cash_pct = (cash / total * 100) if total > 0 else 0
    lines.append(f"  - CASH: ${cash:,.2f} ({cash_pct:.1f}%)")
    lines.append(f"  - TOTAL: ${total:,.2f}")
    return "\n".join(lines)


def build_system_prompt(ctx: TradeChatContext) -> str:
    parts = [
        "[ROLE]",
        "You are a portfolio analyst. The user just received a proposed trade plan from an automated "
        "allocation pipeline and wants to discuss it before deciding to execute. Be concise. Quote "
        "ticker-level numbers from the context, not the internet. Do not fetch new data — use the "
        "provided context and the read_ticker_report / list_ticker_reports tools to drill into "
        "per-ticker analyst details when needed.",
        "",
        "If the user wants the plan changed, tell them to use the /propose slash command (with an "
        "instruction). You cannot rewrite the plan inline — /propose runs the allocation engine again.",
        "",
        f"[STRATEGY]\n{ctx.strategy_text or '(none)'}",
        "",
        f"[TAX CONTEXT]\n{ctx.tax_prompt_str or '(none)'}",
        "",
        f"[RISK CONTEXT]\n{ctx.risk_context_str or '(none)'}",
        "",
        f"[CURRENT HOLDINGS]\n{_render_holdings(ctx)}",
        "",
        f"[PENDING ORDERS]\n{format_pending_orders(ctx.pending)}",
        "",
        f"[QUOTES (non-held)]\n{_render_quotes(ctx)}",
        "",
        "[CROSS-TICKER MERGE REPORT]",
        ctx.merge_report,
        "",
        f"[ALLOCATION PLAN]\n{_render_allocation(ctx.allocation_plan)}",
        "",
        f"[PROPOSED ORDERS]\n{_render_orders(ctx.trade_plan)}",
        "",
        f"[ALLOCATION REASONING]\n{ctx.trade_plan.reasoning}",
        "",
        f"[PROJECTED PORTFOLIO]\n{_render_projected(ctx)}",
    ]
    return "\n".join(parts)


def _build_tools(ctx: TradeChatContext):
    output_dir = ctx.output_dir

    @tool
    def list_ticker_reports() -> str:
        """List the tickers that have detailed per-ticker reports on disk for this run.

        Returns one line per ticker showing which sections are available
        (e.g. bull, bear, fundamentals). Use read_ticker_report to fetch one.
        """
        if not output_dir.is_dir():
            return "(no reports directory found)"
        seen = {}
        for d in sorted(output_dir.iterdir()):
            if not d.is_dir() or "_" not in d.name:
                continue
            ticker = d.name.rsplit("_", 1)[0]
            sections = [
                section for section, rel in SECTION_FILE_MAP.items()
                if (d / rel).is_file()
            ]
            if not sections:
                continue
            # keep latest dir per ticker (sorted so later overwrites earlier)
            seen[ticker] = sections
        if not seen:
            return "(no per-ticker reports found)"
        all_sections = set(SECTION_FILE_MAP)
        lines = []
        for t, secs in sorted(seen.items()):
            if set(secs) == all_sections:
                lines.append(f"  - {t}: (all sections)")
            else:
                lines.append(f"  - {t}: {', '.join(secs)}")
        return (
            "\n".join(lines)
            + f"\n\nValid sections: {', '.join(SECTION_FILE_MAP)}"
        )

    @tool
    def read_ticker_report(ticker: str, section: str = "complete") -> str:
        """Read one section of a per-ticker analysis report.

        Args:
            ticker: Ticker symbol (e.g. NVDA). Validated against the safe-path rules.
            section: One of: complete, market, sentiment, news, fundamentals,
                bull, bear, research_manager, trader, aggressive, conservative,
                neutral, portfolio_decision. Default: complete.

        Returns the file contents (truncated if very long), or an error message
        if the ticker is invalid or the section is unavailable.
        """
        try:
            safe = safe_ticker_component(ticker)
        except ValueError as e:
            return f"error: {e}"
        if section not in SECTION_FILE_MAP:
            valid = ", ".join(SECTION_FILE_MAP)
            return f"error: unknown section {section!r}. Valid: {valid}"
        ticker_dir = _find_latest_ticker_dir(output_dir, safe)
        if ticker_dir is None:
            return f"error: no report directory found for {safe}"
        path = ticker_dir / SECTION_FILE_MAP[section]
        if not path.is_file():
            return f"error: section {section!r} not found for {safe}"
        text = path.read_text(encoding="utf-8")
        if len(text) > TOOL_RESULT_CHAR_CAP:
            text = text[:TOOL_RESULT_CHAR_CAP] + f"\n\n[...truncated, original was {len(text):,} chars]"
        return text

    return [list_ticker_reports, read_ticker_report]


def _propose_new_plan(
    ctx: TradeChatContext,
    history: list[BaseMessage],
    instruction: str,
) -> tuple[TradePlan, AllocationPlan]:
    llm = get_llm(ctx.config)

    history_summary_lines = []
    for msg in history[-12:]:
        if isinstance(msg, HumanMessage):
            history_summary_lines.append(f"USER: {msg.content[:500]}")
        elif isinstance(msg, AIMessage) and msg.content:
            history_summary_lines.append(f"ASSISTANT: {msg.content[:500]}")
    history_str = "\n".join(history_summary_lines) if history_summary_lines else "(no prior turns)"

    current_alloc = _render_allocation(ctx.allocation_plan)

    prompt = f"""You are a portfolio allocation engine revising an existing plan based on user feedback.

**Current Allocation Plan:**
{current_alloc}

**Current Reasoning:**
{ctx.trade_plan.reasoning}

**Recent Conversation:**
{history_str}

**User Instruction:**
{instruction or "(none — re-run allocation considering the conversation above)"}

**Cross-Ticker Analysis Report (still authoritative for ratings/rankings):**

{ctx.merge_report}

---

**Instructions:**
- Apply the user's instruction to produce a REVISED AllocationPlan.
- Each ticker has action (buy/sell/hold) and pct (target % of portfolio).
- For full exit set pct=0; for partial trim set pct lower than current.
- Allocations + cash_pct MUST sum to 100.
- Stay consistent with the merge report's rankings unless the user explicitly overrides.
- Update the reasoning field to reflect the user's instruction."""

    structured = llm.with_structured_output(AllocationPlan)
    new_alloc = structured.invoke(prompt)
    quotes = {**ctx.position_prices, **ctx.quotes}
    return _stage2_orders(new_alloc, ctx.portfolio_dict, quotes), new_alloc


def _render_diff_table(old: TradePlan, new: TradePlan, console: Console) -> None:
    table = Table(title="Plan Diff (old → new)", box=box.ROUNDED, header_style="bold magenta")
    table.add_column("Symbol", style="cyan", justify="center")
    table.add_column("Old", justify="right")
    table.add_column("New", justify="right")
    old_map = {o.symbol: (o.side, int(o.qty)) for o in old.orders}
    new_map = {o.symbol: (o.side, int(o.qty)) for o in new.orders}
    for sym in sorted(set(old_map) | set(new_map)):
        old_str = f"{old_map[sym][0].upper()} {old_map[sym][1]}" if sym in old_map else "—"
        new_str = f"{new_map[sym][0].upper()} {new_map[sym][1]}" if sym in new_map else "—"
        if sym not in old_map:
            new_str = f"[green]{new_str}[/green]"
        elif sym not in new_map:
            old_str = f"[red]{old_str}[/red]"
        elif old_map[sym] != new_map[sym]:
            new_str = f"[yellow]{new_str}[/yellow]"
        table.add_row(sym, old_str, new_str)
    console.print(table)


class TranscriptWriter:
    def __init__(self, path: Path, mode_label: str, n_orders: int):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._fh.write(f"# Trade Plan Chat — {ts}\n")
        self._fh.write(f"**Mode:** {mode_label} · **Orders proposed:** {n_orders}\n\n")
        self._fh.flush()
        self._turn = 0

    def append_turn(self, user: str, assistant: str) -> None:
        self._turn += 1
        self._fh.write(f"## Turn {self._turn} (you)\n{user}\n\n")
        self._fh.write(f"## Turn {self._turn} (assistant)\n{assistant}\n\n")
        self._fh.flush()

    def append_note(self, note: str) -> None:
        self._fh.write(f"> {note}\n\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def _print_help(console: Console) -> None:
    table = Table(box=box.ROUNDED, header_style="bold magenta", title="Chat Commands")
    table.add_column("Command", style="cyan")
    table.add_column("What it does")
    table.add_row("/help", "Show this table")
    table.add_row("/execute, /exec, /go", "Submit orders and exit chat")
    table.add_row("/propose [instruction]", "Re-run allocation with the conversation + your instruction")
    table.add_row("/revert", "Restore the original plan")
    table.add_row("/diff", "Show old → new plan diff")
    table.add_row("/clear", "Drop conversation history (keep system context)")
    table.add_row("/save", "Force-flush transcript to disk")
    table.add_row("/exit, /quit, /done, /q", "Exit chat")
    table.add_row("Ctrl+C", "Interrupt the current LLM response (during a reply)")
    table.add_row("Ctrl+D", "Exit chat (EOF on input)")
    console.print(table)


def _trim_history(history: list[BaseMessage]) -> list[BaseMessage]:
    if len(history) <= HISTORY_SOFT_CAP:
        return history
    # Keep the most recent HISTORY_SOFT_CAP messages, but cut at a clean
    # turn boundary (don't strand a ToolMessage without its parent AIMessage).
    cut = len(history) - HISTORY_SOFT_CAP
    while cut < len(history) and isinstance(history[cut], (ToolMessage, AIMessage)):
        cut += 1
    return history[cut:]


def _rollback_to_last_human(history: list[BaseMessage]) -> None:
    """After an interrupted LLM call, drop everything from the last HumanMessage onward."""
    while history and not isinstance(history[-1], HumanMessage):
        history.pop()
    if history:
        history.pop()


def run_trade_chat(ctx: TradeChatContext, console: Console) -> TradeChatContext:
    """Run an interactive chat loop about the proposed trade plan.

    Returns the (possibly modified) context — trade_plan/allocation_plan
    may have been replaced via /propose.
    """
    base_llm = get_llm(ctx.config)
    tools = _build_tools(ctx)
    llm_with_tools = base_llm.bind_tools(tools)
    tool_map = {t.name: t for t in tools}

    if ctx.transcript_path is None:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        ctx.transcript_path = ctx.output_dir / "_trades" / f"chat_log_{ts}.md"
    transcript = TranscriptWriter(ctx.transcript_path, ctx.mode, len(ctx.trade_plan.orders))

    console.print(
        "[dim]/help · /propose [instruction] · /revert · /execute · /exit · "
        "Ctrl+C interrupts a response · Ctrl+D exits[/dim]"
    )

    history: list[BaseMessage] = []

    try:
        while True:
            try:
                line = console.input("[bold cyan]you[/] [dim](/help)[/] › ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim](exit)[/dim]")
                break
            except Exception:
                console.print("\n[dim](exit)[/dim]")
                break

            if not line:
                continue

            lower = line.lower()
            cmd = lower.split(maxsplit=1)[0] if lower.startswith("/") else None

            if cmd in EXIT_KEYWORDS:
                break
            if cmd in EXECUTE_KEYWORDS:
                ctx.execute_requested = True
                break
            if cmd == "/help":
                _print_help(console)
                continue
            if cmd == "/clear":
                history.clear()
                console.print("[dim]history cleared[/dim]")
                transcript.append_note("history cleared")
                continue
            if cmd == "/save":
                console.print(f"[dim]transcript at {ctx.transcript_path}[/dim]")
                continue
            if cmd == "/diff":
                _render_diff_table(ctx.original_trade_plan, ctx.trade_plan, console)
                continue
            if cmd == "/revert":
                ctx.trade_plan = ctx.original_trade_plan
                ctx.allocation_plan = ctx.original_allocation_plan
                console.print("[yellow]Reverted to original plan.[/yellow]")
                transcript.append_note("plan reverted to original")
                continue
            if cmd == "/propose":
                instruction = line[len("/propose"):].strip()
                console.print(f"[dim]proposing revised plan…[/dim]")
                try:
                    with console.status("re-running allocation…", spinner="dots"):
                        new_plan, new_alloc = _propose_new_plan(ctx, history, instruction)
                except (KeyboardInterrupt, ValidationError) as e:
                    console.print(f"[red]propose failed:[/red] {e}")
                    transcript.append_note(f"/propose failed: {e}")
                    continue
                except Exception as e:
                    console.print(f"[red]propose failed:[/red] {e}")
                    transcript.append_note(f"/propose failed: {e}")
                    continue
                old_plan = ctx.trade_plan
                ctx.trade_plan = new_plan
                ctx.allocation_plan = new_alloc
                _render_diff_table(old_plan, new_plan, console)
                console.print(f"[dim]new reasoning: {new_plan.reasoning}[/dim]")
                transcript.append_note(
                    f"/propose {instruction!r} → {len(new_plan.orders)} orders. "
                    f"reasoning: {new_plan.reasoning}"
                )
                continue
            if line.startswith("/"):
                console.print(f"[red]unknown command:[/red] {cmd}. /help for the list")
                continue

            history.append(HumanMessage(line))
            history = _trim_history(history)

            try:
                with console.status("thinking…", spinner="dots"):
                    final_reply = None
                    for _ in range(TOOL_LOOP_CAP):
                        reply = llm_with_tools.invoke(
                            [SystemMessage(build_system_prompt(ctx))] + history
                        )
                        history.append(reply)
                        if not getattr(reply, "tool_calls", None):
                            final_reply = reply
                            break
                        for tc in reply.tool_calls:
                            tool_fn = tool_map.get(tc["name"])
                            if tool_fn is None:
                                result = f"error: unknown tool {tc['name']!r}"
                            else:
                                try:
                                    result = tool_fn.invoke(tc.get("args", {}))
                                except Exception as e:
                                    result = f"error: {e}"
                            if not isinstance(result, str):
                                result = str(result)
                            history.append(ToolMessage(content=result, tool_call_id=tc["id"]))
                    else:
                        # tool loop didn't converge
                        console.print("[dim](tool loop cap reached)[/dim]")
            except KeyboardInterrupt:
                _rollback_to_last_human(history)
                console.print("\n[dim](aborted)[/dim]")
                continue

            content = (final_reply.content if final_reply is not None else "") or ""
            if content:
                console.print(Markdown(content))
            else:
                console.print("[dim](no reply)[/dim]")
            transcript.append_turn(line, content)
    finally:
        transcript.close()

    return ctx
