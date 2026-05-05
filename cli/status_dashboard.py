import time
from dataclasses import dataclass, field

from rich.console import Console, ConsoleOptions, RenderResult, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from rich import box
from rich.measure import Measurement


@dataclass
class PipelineStatus:
    tickers: list[str] = field(default_factory=list)
    ticker_states: dict[str, str] = field(default_factory=dict)

    total_tickers: int = 0
    completed_tickers: int = 0

    merge_total: int = 1
    merge_completed: int = 0

    alloc_total: int = 0
    alloc_completed: int = 0

    current_phase: str = "ticker"
    current_ticker: str | None = None
    output_log: list[str] = field(default_factory=list)

    step_start: float = field(default_factory=time.time)
    total_start: float = field(default_factory=time.time)

    show_allocation: bool = True

    def _append_output(self, line: str):
        self.output_log.append(line)

    def mark_ticker_active(self, ticker):
        self.current_phase = "ticker"
        self.current_ticker = ticker
        self.ticker_states[ticker] = "active"
        self.step_start = time.time()

    def mark_ticker_done(self, ticker, decision=""):
        self.ticker_states[ticker] = "done"
        self.completed_tickers += 1
        if decision:
            self._append_output(decision)

    def mark_ticker_reused(self, ticker, decision=""):
        self.ticker_states[ticker] = "reused"
        self.completed_tickers += 1
        if decision:
            self._append_output(decision)

    def mark_ticker_failed(self, ticker, error=""):
        self.ticker_states[ticker] = "failed"
        self._append_output(f"{ticker} — FAILED: {error[:80]}")

    def start_merge(self):
        self.current_phase = "merge"
        self.current_ticker = None
        self.step_start = time.time()
        self._append_output("Generating cross-ticker comparison...")

    def finish_merge(self):
        self.merge_completed += 1

    def start_merge_check(self, i, total):
        self.current_phase = "merge_check"
        self.step_start = time.time()
        self._append_output(f"Validating merge report (pass {i}/{total})...")

    def finish_merge_check(self):
        self.merge_completed += 1

    def start_allocation(self):
        self.current_phase = "allocation"
        self.step_start = time.time()
        self._append_output("Generating allocation plan...")

    def finish_allocation(self, reasoning=""):
        self.alloc_completed += 1
        if reasoning:
            self._append_output(f"Allocation: {reasoning[:120]}")

    def start_alloc_check(self, i, total):
        self.current_phase = "alloc_check"
        self.step_start = time.time()
        self._append_output(f"Validating allocation (pass {i}/{total})...")

    def finish_alloc_check(self):
        self.alloc_completed += 1


def extract_report_summary(final_trade_decision: str, ticker: str, decision: str) -> str:
    lines = final_trade_decision.splitlines()
    summary_lines = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("**executive summary**"):
            content = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if content:
                summary_lines.append(content)
            capturing = True
            continue
        if capturing:
            if stripped.startswith("**"):
                break
            if not stripped and summary_lines:
                break
            if stripped:
                summary_lines.append(stripped)

    header = f"{ticker} — {decision}"
    if not summary_lines:
        return header

    body = " ".join(summary_lines)
    return f"{header}\n{body}"


def create_pipeline_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="status", size=6),
        Layout(name="output"),
    )
    return layout


def _format_elapsed(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _build_ticker_line(status: PipelineStatus, max_width: int = 100) -> Text:
    parts = []
    styles = {
        "done": "green",
        "reused": "cyan",
        "active": "bold orange3",
        "failed": "red",
        "pending": "dim",
    }

    for ticker in status.tickers:
        state = status.ticker_states.get(ticker, "pending")
        parts.append((ticker, styles.get(state, "dim")))

    sep = "  "
    full_len = sum(len(t) for t, _ in parts) + len(sep) * (len(parts) - 1)

    if full_len <= max_width:
        text = Text()
        for i, (ticker, style) in enumerate(parts):
            if i > 0:
                text.append(sep)
            text.append(ticker, style=style)
        return text

    active_idx = 0
    for i, (ticker, _) in enumerate(parts):
        state = status.ticker_states.get(ticker, "pending")
        if state == "active":
            active_idx = i
            break

    window_chars = max_width - 10
    avg_ticker = (full_len / len(parts)) if parts else 5
    window_size = max(3, int(window_chars / (avg_ticker + len(sep))))
    half = window_size // 2

    start = max(0, active_idx - half)
    end = min(len(parts), start + window_size)
    if end == len(parts):
        start = max(0, end - window_size)

    text = Text()
    if start > 0:
        text.append("... ", style="dim")
    for i in range(start, end):
        if i > start:
            text.append(sep)
        ticker, style = parts[i]
        text.append(ticker, style=style)
    if end < len(parts):
        text.append(" ...", style="dim")

    return text


class _LiveStatusRenderable:
    """Renderable that recomputes timers on every Rich refresh frame."""

    def __init__(self, status: PipelineStatus):
        self.status = status

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        status = self.status
        now = time.time()
        step_elapsed = _format_elapsed(now - status.step_start)
        total_elapsed = _format_elapsed(now - status.total_start)

        ticker_line = _build_ticker_line(status, max_width=options.max_width - 12)

        phase_parts = [f"Tickers ({status.completed_tickers}/{status.total_tickers})"]
        phase_parts.append(f"Merge ({status.merge_completed}/{status.merge_total})")
        if status.show_allocation:
            phase_parts.append(f"Alloc ({status.alloc_completed}/{status.alloc_total})")

        phase_str = " │ ".join(phase_parts)
        time_str = f"Step: {step_elapsed} │ Total: {total_elapsed}"

        content = Text()
        content.append("Tickers: ")
        content.append_text(ticker_line)
        content.append(f"\n\n{phase_str}\n{time_str}")

        yield content

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(options.min_width, options.max_width)


def update_pipeline_display(layout: Layout, status: PipelineStatus):
    layout["status"].update(
        Panel(_LiveStatusRenderable(status), title="Pipeline Status", border_style="cyan", padding=(0, 2))
    )

    # Show complete report entries only — never truncate mid-entry.
    # Calculate how many lines each entry takes when word-wrapped to terminal width.
    try:
        term_width = Console().width or 120
    except Exception:
        term_width = 120
    # Account for panel border (2) + padding (4)
    content_width = max(40, term_width - 6)

    try:
        term_height = Console().height or 30
    except Exception:
        term_height = 30
    # Status panel (6 lines + 2 border) + output panel border (2) + padding (2)
    available_lines = max(5, term_height - 12)

    active_line = ""
    if status.current_ticker and status.ticker_states.get(status.current_ticker) == "active":
        active_line = f"{status.current_ticker} — analyzing..."
        available_lines -= 2  # header + blank line separator

    def _wrapped_line_count(text):
        count = 0
        for line in text.split("\n"):
            count += max(1, -(-len(line) // content_width))  # ceiling division
        return count

    # Pick entries from the end, fitting as many complete entries as possible
    visible = []
    lines_used = 0
    for entry in reversed(status.output_log):
        entry_lines = _wrapped_line_count(entry) + 1  # +1 for blank separator
        if lines_used + entry_lines > available_lines and visible:
            break
        visible.append(entry)
        lines_used += entry_lines

    visible.reverse()

    parts = list(visible)
    if active_line:
        parts.append(active_line)

    output_text = "\n\n".join(parts) if parts else "Waiting..."
    output_content = Text(output_text)

    layout["output"].update(
        Panel(output_content, title="Output", border_style="green", padding=(0, 2))
    )
