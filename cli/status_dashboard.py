import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from rich import box


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
            if len(summary_lines) >= 6:
                break

    header = f"{ticker} — {decision}"
    if summary_lines:
        body = "\n".join(summary_lines[:8])
        return f"{header}\n{body}"
    return header


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


def update_pipeline_display(layout: Layout, status: PipelineStatus):
    now = time.time()
    step_elapsed = _format_elapsed(now - status.step_start)
    total_elapsed = _format_elapsed(now - status.total_start)

    ticker_line = _build_ticker_line(status)

    phase_parts = [f"Tickers ({status.completed_tickers}/{status.total_tickers})"]
    phase_parts.append(f"Merge ({status.merge_completed}/{status.merge_total})")
    if status.show_allocation:
        phase_parts.append(f"Alloc ({status.alloc_completed}/{status.alloc_total})")

    phase_str = " │ ".join(phase_parts)
    time_str = f"Step: {step_elapsed} │ Total: {total_elapsed}"

    status_content = Text()
    status_content.append("Tickers: ")
    status_content.append_text(ticker_line)
    status_content.append(f"\n\n{phase_str}\n{time_str}")

    layout["status"].update(
        Panel(status_content, title="Pipeline Status", border_style="cyan", padding=(0, 2))
    )

    console_height = Console().height or 24
    available_lines = max(5, console_height - 6 - 4)

    # Reserve space for active ticker indicator
    active_suffix = ""
    if status.current_ticker and status.ticker_states.get(status.current_ticker) == "active":
        active_suffix = f"\n\n{status.current_ticker} — analyzing..."
        available_lines -= 3

    # Build visible entries from newest, fitting as many as possible
    visible_entries = []
    lines_used = 0
    for entry in reversed(status.output_log):
        entry_lines = entry.count("\n") + 1
        if lines_used + entry_lines + 1 > available_lines and visible_entries:
            break
        visible_entries.append(entry)
        lines_used += entry_lines + 1  # +1 for separator blank line

    visible_entries.reverse()
    output_text = "\n\n".join(visible_entries) if visible_entries else "Waiting..."
    output_text += active_suffix
    output_content = Text(output_text)

    layout["output"].update(
        Panel(output_content, title="Output", border_style="green", padding=(0, 2))
    )
