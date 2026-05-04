from typing import Optional
import datetime
import typer
from pathlib import Path
from functools import wraps
from rich.console import Console
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
load_dotenv(".env.enterprise", override=False)
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from collections import deque
import time
from rich.tree import Tree
from rich import box
from rich.align import Align
from rich.rule import Rule

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from cli.models import AnalystType
from cli.utils import *
from cli.announcements import fetch_announcements, display_announcements
from cli.stats_handler import StatsCallbackHandler

console = Console()

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)


# Create a deque to store recent messages with a maximum length
class MessageBuffer:
    # Fixed teams that always run (not user-selectable)
    FIXED_AGENTS = {
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Analyst name mapping
    ANALYST_MAPPING = {
        "market": "Market Analyst",
        "social": "Social Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
    }

    # Report section mapping: section -> (analyst_key for filtering, finalizing_agent)
    # analyst_key: which analyst selection controls this section (None = always included)
    # finalizing_agent: which agent must be "completed" for this report to count as done
    REPORT_SECTIONS = {
        "market_report": ("market", "Market Analyst"),
        "sentiment_report": ("social", "Social Analyst"),
        "news_report": ("news", "News Analyst"),
        "fundamentals_report": ("fundamentals", "Fundamentals Analyst"),
        "investment_plan": (None, "Research Manager"),
        "trader_investment_plan": (None, "Trader"),
        "final_trade_decision": (None, "Portfolio Manager"),
    }

    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {}
        self.current_agent = None
        self.report_sections = {}
        self.selected_analysts = []
        self._processed_message_ids = set()
        self._orig_add_message = self.add_message
        self._orig_add_tool_call = self.add_tool_call
        self._orig_update_report_section = self.update_report_section

    def init_for_analysis(self, selected_analysts):
        """Initialize agent status and report sections based on selected analysts.

        Args:
            selected_analysts: List of analyst type strings (e.g., ["market", "news"])
        """
        self.selected_analysts = [a.lower() for a in selected_analysts]

        # Build agent_status dynamically
        self.agent_status = {}

        # Add selected analysts
        for analyst_key in self.selected_analysts:
            if analyst_key in self.ANALYST_MAPPING:
                self.agent_status[self.ANALYST_MAPPING[analyst_key]] = "pending"

        # Add fixed teams
        for team_agents in self.FIXED_AGENTS.values():
            for agent in team_agents:
                self.agent_status[agent] = "pending"

        # Build report_sections dynamically
        self.report_sections = {}
        for section, (analyst_key, _) in self.REPORT_SECTIONS.items():
            if analyst_key is None or analyst_key in self.selected_analysts:
                self.report_sections[section] = None

        # Restore original undecorated methods so decorators don't stack
        self.add_message = self._orig_add_message
        self.add_tool_call = self._orig_add_tool_call
        self.update_report_section = self._orig_update_report_section

        # Reset other state
        self.current_report = None
        self.final_report = None
        self.current_agent = None
        self.messages.clear()
        self.tool_calls.clear()
        self._processed_message_ids.clear()

    def get_completed_reports_count(self):
        """Count reports that are finalized (their finalizing agent is completed).

        A report is considered complete when:
        1. The report section has content (not None), AND
        2. The agent responsible for finalizing that report has status "completed"

        This prevents interim updates (like debate rounds) from counting as completed.
        """
        count = 0
        for section in self.report_sections:
            if section not in self.REPORT_SECTIONS:
                continue
            _, finalizing_agent = self.REPORT_SECTIONS[section]
            # Report is complete if it has content AND its finalizing agent is done
            has_content = self.report_sections.get(section) is not None
            agent_done = self.agent_status.get(finalizing_agent) == "completed"
            if has_content and agent_done:
                count += 1
        return count

    def add_message(self, message_type, content):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name, args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent, status):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def update_report_section(self, section_name, content):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        # For the panel display, only show the most recently updated section
        latest_section = None
        latest_content = None

        # Find the most recently updated section
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content
               
        if latest_section and latest_content:
            # Format the current section for display
            section_titles = {
                "market_report": "Market Analysis",
                "sentiment_report": "Social Sentiment",
                "news_report": "News Analysis",
                "fundamentals_report": "Fundamentals Analysis",
                "investment_plan": "Research Team Decision",
                "trader_investment_plan": "Trading Team Plan",
                "final_trade_decision": "Portfolio Management Decision",
            }
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # Update the final complete report
        self._update_final_report()

    def _update_final_report(self):
        report_parts = []

        # Analyst Team Reports - use .get() to handle missing sections
        analyst_sections = ["market_report", "sentiment_report", "news_report", "fundamentals_report"]
        if any(self.report_sections.get(section) for section in analyst_sections):
            report_parts.append("## Analyst Team Reports")
            if self.report_sections.get("market_report"):
                report_parts.append(
                    f"### Market Analysis\n{self.report_sections['market_report']}"
                )
            if self.report_sections.get("sentiment_report"):
                report_parts.append(
                    f"### Social Sentiment\n{self.report_sections['sentiment_report']}"
                )
            if self.report_sections.get("news_report"):
                report_parts.append(
                    f"### News Analysis\n{self.report_sections['news_report']}"
                )
            if self.report_sections.get("fundamentals_report"):
                report_parts.append(
                    f"### Fundamentals Analysis\n{self.report_sections['fundamentals_report']}"
                )

        # Research Team Reports
        if self.report_sections.get("investment_plan"):
            report_parts.append("## Research Team Decision")
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # Trading Team Reports
        if self.report_sections.get("trader_investment_plan"):
            report_parts.append("## Trading Team Plan")
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # Portfolio Management Decision
        if self.report_sections.get("final_trade_decision"):
            report_parts.append("## Portfolio Management Decision")
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


message_buffer = MessageBuffer()


def create_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout


def format_tokens(n):
    """Format token count for display."""
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def update_display(layout, spinner_text=None, stats_handler=None, start_time=None):
    # Header with welcome message
    layout["header"].update(
        Panel(
            "[bold green]Welcome to TradingAgents CLI[/bold green]\n"
            "[dim]© [Tauric Research](https://github.com/TauricResearch)[/dim]",
            title="Welcome to TradingAgents",
            border_style="green",
            padding=(1, 2),
            expand=True,
        )
    )

    # Progress panel showing agent status
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # Use simple header with horizontal lines
        title=None,  # Remove the redundant Progress title
        padding=(0, 2),  # Add horizontal padding
        expand=True,  # Make table expand to fill available space
    )
    progress_table.add_column("Team", style="cyan", justify="center", width=20)
    progress_table.add_column("Agent", style="green", justify="center", width=20)
    progress_table.add_column("Status", style="yellow", justify="center", width=20)

    # Group agents by team - filter to only include agents in agent_status
    all_teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    # Filter teams to only include agents that are in agent_status
    teams = {}
    for team, agents in all_teams.items():
        active_agents = [a for a in agents if a in message_buffer.agent_status]
        if active_agents:
            teams[team] = active_agents

    for team, agents in teams.items():
        # Add first agent with team name
        first_agent = agents[0]
        status = message_buffer.agent_status.get(first_agent, "pending")
        if status == "in_progress":
            spinner = Spinner(
                "dots", text="[blue]in_progress[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_cell = f"[{status_color}]{status}[/{status_color}]"
        progress_table.add_row(team, first_agent, status_cell)

        # Add remaining agents in team
        for agent in agents[1:]:
            status = message_buffer.agent_status.get(agent, "pending")
            if status == "in_progress":
                spinner = Spinner(
                    "dots", text="[blue]in_progress[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_cell = f"[{status_color}]{status}[/{status_color}]"
            progress_table.add_row("", agent, status_cell)

        # Add horizontal line after each team
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    layout["progress"].update(
        Panel(progress_table, title="Progress", border_style="cyan", padding=(1, 2))
    )

    # Messages panel showing recent messages and tool calls
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # Make table expand to fill available space
        box=box.MINIMAL,  # Use minimal box style for a lighter look
        show_lines=True,  # Keep horizontal lines
        padding=(0, 1),  # Add some padding between columns
    )
    messages_table.add_column("Time", style="cyan", width=8, justify="center")
    messages_table.add_column("Type", style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )  # Make content column expand

    # Combine tool calls and messages
    all_messages = []

    # Add tool calls
    for timestamp, tool_name, args in message_buffer.tool_calls:
        formatted_args = format_tool_args(args)
        all_messages.append((timestamp, "Tool", f"{tool_name}: {formatted_args}"))

    # Add regular messages
    for timestamp, msg_type, content in message_buffer.messages:
        content_str = str(content) if content else ""
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # Sort by timestamp descending (newest first)
    all_messages.sort(key=lambda x: x[0], reverse=True)

    # Calculate how many messages we can show based on available space
    max_messages = 12

    # Get the first N messages (newest ones)
    recent_messages = all_messages[:max_messages]

    # Add messages to table (already in newest-first order)
    for timestamp, msg_type, content in recent_messages:
        # Format content with word wrapping
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    layout["messages"].update(
        Panel(
            messages_table,
            title="Messages & Tools",
            border_style="blue",
            padding=(1, 2),
        )
    )

    # Analysis panel showing current report
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                "[italic]Waiting for analysis report...[/italic]",
                title="Current Report",
                border_style="green",
                padding=(1, 2),
            )
        )

    # Footer with statistics
    # Agent progress - derived from agent_status dict
    agents_completed = sum(
        1 for status in message_buffer.agent_status.values() if status == "completed"
    )
    agents_total = len(message_buffer.agent_status)

    # Report progress - based on agent completion (not just content existence)
    reports_completed = message_buffer.get_completed_reports_count()
    reports_total = len(message_buffer.report_sections)

    # Build stats parts
    stats_parts = [f"Agents: {agents_completed}/{agents_total}"]

    # LLM and tool stats from callback handler
    if stats_handler:
        stats = stats_handler.get_stats()
        stats_parts.append(f"LLM: {stats['llm_calls']}")
        stats_parts.append(f"Tools: {stats['tool_calls']}")

        # Token display with graceful fallback
        if stats["tokens_in"] > 0 or stats["tokens_out"] > 0:
            tokens_str = f"Tokens: {format_tokens(stats['tokens_in'])}\u2191 {format_tokens(stats['tokens_out'])}\u2193"
        else:
            tokens_str = "Tokens: --"
        stats_parts.append(tokens_str)

    stats_parts.append(f"Reports: {reports_completed}/{reports_total}")

    # Elapsed time
    if start_time:
        elapsed = time.time() - start_time
        elapsed_str = f"\u23f1 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
        stats_parts.append(elapsed_str)

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column("Stats", justify="center")
    stats_table.add_row(" | ".join(stats_parts))

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def get_user_selections():
    """Get all user selections before starting the analysis display."""
    # Display ASCII art welcome message
    with open(Path(__file__).parent / "static" / "welcome.txt", "r", encoding="utf-8") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]\n\n"
    welcome_content += "[bold]Workflow Steps:[/bold]\n"
    welcome_content += "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management\n\n"
    welcome_content += (
        "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"
    )

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title="Welcome to TradingAgents",
        subtitle="Multi-Agents LLM Financial Trading Framework",
    )
    console.print(Align.center(welcome_box))
    console.print()
    console.print()  # Add vertical space before announcements

    # Fetch and display announcements (silent on failure)
    announcements = fetch_announcements()
    display_announcements(console, announcements)

    # Create a boxed questionnaire for each step
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += f"\n[dim]Default: {default}[/dim]"
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # Step 1: Ticker symbol
    console.print(
        create_question_box(
            "Step 1: Ticker Symbol",
            "Enter the exact ticker symbol to analyze, including exchange suffix when needed (examples: SPY, CNC.TO, 7203.T, 0700.HK)",
            "SPY",
        )
    )
    selected_ticker = get_ticker()

    # Step 2: Analysis date
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            "Step 2: Analysis Date",
            "Enter the analysis date (YYYY-MM-DD)",
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # Step 3: Output language
    console.print(
        create_question_box(
            "Step 3: Output Language",
            "Select the language for analyst reports and final decision"
        )
    )
    output_language = ask_output_language()

    # Step 4: Select analysts
    console.print(
        create_question_box(
            "Step 4: Analysts Team", "Select your LLM analyst agents for the analysis"
        )
    )
    selected_analysts = select_analysts()
    console.print(
        f"[green]Selected analysts:[/green] {', '.join(analyst.value for analyst in selected_analysts)}"
    )

    # Step 5: Research depth
    console.print(
        create_question_box(
            "Step 5: Research Depth", "Select your research depth level"
        )
    )
    selected_research_depth = select_research_depth()

    # Step 6: LLM Provider
    console.print(
        create_question_box(
            "Step 6: LLM Provider", "Select your LLM provider"
        )
    )
    selected_llm_provider, backend_url = select_llm_provider()

    # Step 7: Thinking agents
    console.print(
        create_question_box(
            "Step 7: Thinking Agents", "Select your thinking agents for analysis"
        )
    )
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    # Step 8: Provider-specific thinking configuration
    thinking_level = None
    reasoning_effort = None
    anthropic_effort = None

    provider_lower = selected_llm_provider.lower()
    if provider_lower == "google":
        console.print(
            create_question_box(
                "Step 8: Thinking Mode",
                "Configure Gemini thinking mode"
            )
        )
        thinking_level = ask_gemini_thinking_config()
    elif provider_lower == "openai":
        console.print(
            create_question_box(
                "Step 8: Reasoning Effort",
                "Configure OpenAI reasoning effort level"
            )
        )
        reasoning_effort = ask_openai_reasoning_effort()
    elif provider_lower == "anthropic":
        console.print(
            create_question_box(
                "Step 8: Effort Level",
                "Configure Claude effort level"
            )
        )
        anthropic_effort = ask_anthropic_effort()

    return {
        "ticker": selected_ticker,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
        "google_thinking_level": thinking_level,
        "openai_reasoning_effort": reasoning_effort,
        "anthropic_effort": anthropic_effort,
        "output_language": output_language,
    }


def get_ticker():
    """Get ticker symbol from user input."""
    return typer.prompt("", default="SPY")


def get_analysis_date():
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt(
            "", default=datetime.datetime.now().strftime("%Y-%m-%d")
        )
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print(
                "[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]"
            )


def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """Save complete analysis report to disk with organized subfolders."""
    save_path.mkdir(parents=True, exist_ok=True)
    sections = []

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "market.md").write_text(final_state["market_report"], encoding="utf-8")
        analyst_parts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "sentiment.md").write_text(final_state["sentiment_report"], encoding="utf-8")
        analyst_parts.append(("Social Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "news.md").write_text(final_state["news_report"], encoding="utf-8")
        analyst_parts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        (analysts_dir / "fundamentals.md").write_text(final_state["fundamentals_report"], encoding="utf-8")
        analyst_parts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analyst_parts:
        content = "\n\n".join(f"### {name}\n{text}" for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"], encoding="utf-8")
            research_parts.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"], encoding="utf-8")
            research_parts.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "manager.md").write_text(debate["judge_decision"], encoding="utf-8")
            research_parts.append(("Research Manager", debate["judge_decision"]))
        if research_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        (trading_dir / "trader.md").write_text(final_state["trader_investment_plan"], encoding="utf-8")
        sections.append(f"## III. Trading Team Plan\n\n### Trader\n{final_state['trader_investment_plan']}")

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        risk_parts = []
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"], encoding="utf-8")
            risk_parts.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"], encoding="utf-8")
            risk_parts.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"], encoding="utf-8")
            risk_parts.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_parts:
            content = "\n\n".join(f"### {name}\n{text}" for name, text in risk_parts)
            sections.append(f"## IV. Risk Management Team Decision\n\n{content}")

        # 5. Portfolio Manager
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            (portfolio_dir / "decision.md").write_text(risk["judge_decision"], encoding="utf-8")
            sections.append(f"## V. Portfolio Manager Decision\n\n### Portfolio Manager\n{risk['judge_decision']}")

    # Write consolidated report
    header = f"# Trading Analysis Report: {ticker}\n\nGenerated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    (save_path / "complete_report.md").write_text(header + "\n\n".join(sections), encoding="utf-8")
    return save_path / "complete_report.md"


def display_complete_report(final_state):
    """Display the complete analysis report sequentially (avoids truncation)."""
    console.print()
    console.print(Rule("Complete Analysis Report", style="bold green"))

    # I. Analyst Team Reports
    analysts = []
    if final_state.get("market_report"):
        analysts.append(("Market Analyst", final_state["market_report"]))
    if final_state.get("sentiment_report"):
        analysts.append(("Social Analyst", final_state["sentiment_report"]))
    if final_state.get("news_report"):
        analysts.append(("News Analyst", final_state["news_report"]))
    if final_state.get("fundamentals_report"):
        analysts.append(("Fundamentals Analyst", final_state["fundamentals_report"]))
    if analysts:
        console.print(Panel("[bold]I. Analyst Team Reports[/bold]", border_style="cyan"))
        for title, content in analysts:
            console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        debate = final_state["investment_debate_state"]
        research = []
        if debate.get("bull_history"):
            research.append(("Bull Researcher", debate["bull_history"]))
        if debate.get("bear_history"):
            research.append(("Bear Researcher", debate["bear_history"]))
        if debate.get("judge_decision"):
            research.append(("Research Manager", debate["judge_decision"]))
        if research:
            console.print(Panel("[bold]II. Research Team Decision[/bold]", border_style="magenta"))
            for title, content in research:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

    # III. Trading Team
    if final_state.get("trader_investment_plan"):
        console.print(Panel("[bold]III. Trading Team Plan[/bold]", border_style="yellow"))
        console.print(Panel(Markdown(final_state["trader_investment_plan"]), title="Trader", border_style="blue", padding=(1, 2)))

    # IV. Risk Management Team
    if final_state.get("risk_debate_state"):
        risk = final_state["risk_debate_state"]
        risk_reports = []
        if risk.get("aggressive_history"):
            risk_reports.append(("Aggressive Analyst", risk["aggressive_history"]))
        if risk.get("conservative_history"):
            risk_reports.append(("Conservative Analyst", risk["conservative_history"]))
        if risk.get("neutral_history"):
            risk_reports.append(("Neutral Analyst", risk["neutral_history"]))
        if risk_reports:
            console.print(Panel("[bold]IV. Risk Management Team Decision[/bold]", border_style="red"))
            for title, content in risk_reports:
                console.print(Panel(Markdown(content), title=title, border_style="blue", padding=(1, 2)))

        # V. Portfolio Manager Decision
        if risk.get("judge_decision"):
            console.print(Panel("[bold]V. Portfolio Manager Decision[/bold]", border_style="green"))
            console.print(Panel(Markdown(risk["judge_decision"]), title="Portfolio Manager", border_style="blue", padding=(1, 2)))


def update_research_team_status(status):
    """Update status for research team members (not Trader)."""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)


# Ordered list of analysts for status transitions
ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
ANALYST_AGENT_NAMES = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
ANALYST_REPORT_MAP = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


def update_analyst_statuses(message_buffer, chunk):
    """Update analyst statuses based on accumulated report state.

    Logic:
    - Store new report content from the current chunk if present
    - Check accumulated report_sections (not just current chunk) for status
    - Analysts with reports = completed
    - First analyst without report = in_progress
    - Remaining analysts without reports = pending
    - When all analysts done, set Bull Researcher to in_progress
    """
    selected = message_buffer.selected_analysts
    found_active = False

    for analyst_key in ANALYST_ORDER:
        if analyst_key not in selected:
            continue

        agent_name = ANALYST_AGENT_NAMES[analyst_key]
        report_key = ANALYST_REPORT_MAP[analyst_key]

        # Capture new report content from current chunk
        if chunk.get(report_key):
            message_buffer.update_report_section(report_key, chunk[report_key])

        # Determine status from accumulated sections, not just current chunk
        has_report = bool(message_buffer.report_sections.get(report_key))

        if has_report:
            message_buffer.update_agent_status(agent_name, "completed")
        elif not found_active:
            message_buffer.update_agent_status(agent_name, "in_progress")
            found_active = True
        else:
            message_buffer.update_agent_status(agent_name, "pending")

    # When all analysts complete, transition research team to in_progress
    if not found_active and selected:
        if message_buffer.agent_status.get("Bull Researcher") == "pending":
            message_buffer.update_agent_status("Bull Researcher", "in_progress")

def extract_content_string(content):
    """Extract string content from various message formats.
    Returns None if no meaningful text content is found.
    """
    import ast

    def is_empty(val):
        """Check if value is empty using Python's truthiness."""
        if val is None or val == '':
            return True
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return True
            try:
                return not bool(ast.literal_eval(s))
            except (ValueError, SyntaxError):
                return False  # Can't parse = real text
        return not bool(val)

    if is_empty(content):
        return None

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        text = content.get('text', '')
        return text.strip() if not is_empty(text) else None

    if isinstance(content, list):
        text_parts = [
            item.get('text', '').strip() if isinstance(item, dict) and item.get('type') == 'text'
            else (item.strip() if isinstance(item, str) else '')
            for item in content
        ]
        result = ' '.join(t for t in text_parts if t and not is_empty(t))
        return result if result else None

    return str(content).strip() if not is_empty(content) else None


def classify_message_type(message) -> tuple[str, str | None]:
    """Classify LangChain message into display type and extract content.

    Returns:
        (type, content) - type is one of: User, Agent, Data, Control
                        - content is extracted string or None
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = extract_content_string(getattr(message, 'content', None))

    if isinstance(message, HumanMessage):
        if content and content.strip() == "Continue":
            return ("Control", content)
        return ("User", content)

    if isinstance(message, ToolMessage):
        return ("Data", content)

    if isinstance(message, AIMessage):
        return ("Agent", content)

    # Fallback for unknown types
    return ("System", content)


def format_tool_args(args, max_length=80) -> str:
    """Format tool arguments for terminal display."""
    result = str(args)
    if len(result) > max_length:
        return result[:max_length - 3] + "..."
    return result

DEPTH_MAP = {"shallow": 1, "medium": 3, "deep": 5}

STRATEGY_PROFILES = {
    "conservative": (
        "Conservative: Prioritize capital preservation. Favor large-cap, low-volatility, "
        "and dividend-paying positions. Limit speculative or high-beta tickers to ≤5% of "
        "portfolio. Prefer Hold over Buy when conviction is moderate."
    ),
    "balanced": (
        "Balanced: Mix of stability and growth. Allocate to high-conviction Buy-rated tickers "
        "regardless of volatility, but size positions proportional to risk — larger positions "
        "in blue-chips, smaller positions in speculative names. Target 10-20% in higher-risk "
        "tickers if the analysis supports them."
    ),
    "aggressive": (
        "Aggressive: Maximize growth potential. Overweight high-conviction, high-risk/high-reward "
        "tickers. Willing to accept significant drawdowns for outsized upside. Speculative and "
        "small-cap positions can be 30%+ of portfolio. Only avoid a ticker if the analysis "
        "explicitly rates it Sell."
    ),
}

PROVIDER_BACKEND_URLS = {
    "openai": "https://api.openai.com/v1",
    "google": None,
    "anthropic": "https://api.anthropic.com/",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "glm": "https://open.bigmodel.cn/api/paas/v4/",
    "openrouter": "https://openrouter.ai/api/v1",
    "azure": None,
    "ollama": "http://localhost:11434/v1",
}


def _build_config(selections, checkpoint=False):
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    config["google_thinking_level"] = selections.get("google_thinking_level")
    config["openai_reasoning_effort"] = selections.get("openai_reasoning_effort")
    config["anthropic_effort"] = selections.get("anthropic_effort")
    config["output_language"] = selections.get("output_language", "English")
    config["checkpoint_enabled"] = checkpoint
    return config


def _run_single_ticker(
    ticker,
    analysis_date,
    selected_analyst_keys,
    config,
    graph,
    output_dir=None,
):
    stats_handler = StatsCallbackHandler()

    message_buffer.init_for_analysis(selected_analyst_keys)

    start_time = time.time()

    results_dir = Path(config["results_dir"]) / ticker / analysis_date
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            content = content.replace("\n", " ")
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper

    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(text)
        return wrapper

    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(message_buffer, "update_report_section")

    layout = create_layout()

    with Live(layout, refresh_per_second=4) as live:
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        message_buffer.add_message("System", f"Selected ticker: {ticker}")
        message_buffer.add_message("System", f"Analysis date: {analysis_date}")
        message_buffer.add_message(
            "System",
            f"Selected analysts: {', '.join(selected_analyst_keys)}",
        )
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        first_analyst_key = selected_analyst_keys[0]
        first_analyst = f"{first_analyst_key.capitalize()} Analyst"
        message_buffer.update_agent_status(first_analyst, "in_progress")
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        spinner_text = f"Analyzing {ticker} on {analysis_date}..."
        update_display(layout, spinner_text, stats_handler=stats_handler, start_time=start_time)

        init_agent_state = graph.propagator.create_initial_state(ticker, analysis_date)
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        trace = []
        for chunk in graph.graph.stream(init_agent_state, **args):
            for message in chunk.get("messages", []):
                msg_id = getattr(message, "id", None)
                if msg_id is not None:
                    if msg_id in message_buffer._processed_message_ids:
                        continue
                    message_buffer._processed_message_ids.add(msg_id)

                msg_type, content = classify_message_type(message)
                if content and content.strip():
                    message_buffer.add_message(msg_type, content)

                if hasattr(message, "tool_calls") and message.tool_calls:
                    for tool_call in message.tool_calls:
                        if isinstance(tool_call, dict):
                            message_buffer.add_tool_call(tool_call["name"], tool_call["args"])
                        else:
                            message_buffer.add_tool_call(tool_call.name, tool_call.args)

            update_analyst_statuses(message_buffer, chunk)

            if chunk.get("investment_debate_state"):
                debate_state = chunk["investment_debate_state"]
                bull_hist = debate_state.get("bull_history", "").strip()
                bear_hist = debate_state.get("bear_history", "").strip()
                judge = debate_state.get("judge_decision", "").strip()

                if bull_hist or bear_hist:
                    update_research_team_status("in_progress")
                if bull_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bull Researcher Analysis\n{bull_hist}"
                    )
                if bear_hist:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Bear Researcher Analysis\n{bear_hist}"
                    )
                if judge:
                    message_buffer.update_report_section(
                        "investment_plan", f"### Research Manager Decision\n{judge}"
                    )
                    update_research_team_status("completed")
                    message_buffer.update_agent_status("Trader", "in_progress")

            if chunk.get("trader_investment_plan"):
                message_buffer.update_report_section(
                    "trader_investment_plan", chunk["trader_investment_plan"]
                )
                if message_buffer.agent_status.get("Trader") != "completed":
                    message_buffer.update_agent_status("Trader", "completed")
                    message_buffer.update_agent_status("Aggressive Analyst", "in_progress")

            if chunk.get("risk_debate_state"):
                risk_state = chunk["risk_debate_state"]
                agg_hist = risk_state.get("aggressive_history", "").strip()
                con_hist = risk_state.get("conservative_history", "").strip()
                neu_hist = risk_state.get("neutral_history", "").strip()
                judge = risk_state.get("judge_decision", "").strip()

                if agg_hist:
                    if message_buffer.agent_status.get("Aggressive Analyst") != "completed":
                        message_buffer.update_agent_status("Aggressive Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Aggressive Analyst Analysis\n{agg_hist}"
                    )
                if con_hist:
                    if message_buffer.agent_status.get("Conservative Analyst") != "completed":
                        message_buffer.update_agent_status("Conservative Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Conservative Analyst Analysis\n{con_hist}"
                    )
                if neu_hist:
                    if message_buffer.agent_status.get("Neutral Analyst") != "completed":
                        message_buffer.update_agent_status("Neutral Analyst", "in_progress")
                    message_buffer.update_report_section(
                        "final_trade_decision", f"### Neutral Analyst Analysis\n{neu_hist}"
                    )
                if judge:
                    if message_buffer.agent_status.get("Portfolio Manager") != "completed":
                        message_buffer.update_agent_status("Portfolio Manager", "in_progress")
                        message_buffer.update_report_section(
                            "final_trade_decision", f"### Portfolio Manager Decision\n{judge}"
                        )
                        message_buffer.update_agent_status("Aggressive Analyst", "completed")
                        message_buffer.update_agent_status("Conservative Analyst", "completed")
                        message_buffer.update_agent_status("Neutral Analyst", "completed")
                        message_buffer.update_agent_status("Portfolio Manager", "completed")

            update_display(layout, stats_handler=stats_handler, start_time=start_time)

            trace.append(chunk)

        final_state = trace[-1]
        decision = graph.process_signal(final_state["final_trade_decision"])

        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "completed")

        message_buffer.add_message("System", f"Completed analysis for {analysis_date}")

        for section in message_buffer.report_sections.keys():
            if section in final_state:
                message_buffer.update_report_section(section, final_state[section])

        update_display(layout, stats_handler=stats_handler, start_time=start_time)

    elapsed = time.time() - start_time

    if output_dir is not None:
        save_path = Path(output_dir)
        save_report_to_disk(final_state, ticker, save_path)
        console.print(f"[green]Report saved to:[/green] {save_path.resolve()}")

    return {
        "final_state": final_state,
        "decision": decision,
        "elapsed": elapsed,
        "stats": stats_handler.get_stats(),
    }


def run_analysis(checkpoint: bool = False):
    selections = get_user_selections()
    config = _build_config(selections, checkpoint)

    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]

    stats_handler = StatsCallbackHandler()
    graph = TradingAgentsGraph(
        selected_analyst_keys,
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    result = _run_single_ticker(
        ticker=selections["ticker"],
        analysis_date=selections["analysis_date"],
        selected_analyst_keys=selected_analyst_keys,
        config=config,
        graph=graph,
    )
    final_state = result["final_state"]

    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")

    save_choice = typer.prompt("Save report?", default="Y").strip().upper()
    if save_choice in ("Y", "YES", ""):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = Path.cwd() / "reports" / f"{selections['ticker']}_{timestamp}"
        save_path_str = typer.prompt(
            "Save path (press Enter for default)",
            default=str(default_path)
        ).strip()
        save_path = Path(save_path_str)
        try:
            report_file = save_report_to_disk(final_state, selections["ticker"], save_path)
            console.print(f"\n[green]Report saved to:[/green] {save_path.resolve()}")
            console.print(f"  [dim]Complete report:[/dim] {report_file.name}")
        except Exception as e:
            console.print(f"[red]Error saving report: {e}[/red]")

    display_choice = typer.prompt("\nDisplay full report on screen?", default="Y").strip().upper()
    if display_choice in ("Y", "YES", ""):
        display_complete_report(final_state)


def _print_batch_summary(results, output_dir):
    table = Table(
        title="Batch Analysis Summary",
        show_header=True,
        header_style="bold magenta",
        box=box.ROUNDED,
    )
    table.add_column("Ticker", style="cyan", justify="center")
    table.add_column("Status", justify="center")
    table.add_column("Decision", justify="center")
    table.add_column("Time", justify="right")

    succeeded = 0
    for ticker, status, detail, elapsed in results:
        if status == "SUCCESS":
            succeeded += 1
            status_str = "[green]SUCCESS[/green]"
            time_str = f"{elapsed:.0f}s"
        else:
            status_str = "[red]FAILED[/red]"
            time_str = "-"
        table.add_row(ticker, status_str, str(detail), time_str)

    console.print()
    console.print(table)
    console.print(f"\n[bold]{succeeded}/{len(results)} tickers completed successfully[/bold]")
    console.print(f"Reports saved to: {Path(output_dir).resolve()}")


FULL_REPORT_TICKER_LIMIT = 10


def _build_ticker_section(ticker, decision, final_state, include_analyst_reports):
    parts = [f"#### {ticker} — Rating: {decision}"]

    if include_analyst_reports:
        for label, key in [
            ("Market Analysis", "market_report"),
            ("Social Sentiment", "sentiment_report"),
            ("News Analysis", "news_report"),
            ("Fundamentals", "fundamentals_report"),
        ]:
            report = final_state.get(key, "").strip()
            if report:
                parts.append(f"**{label}:**\n{report}")

    ftd = final_state.get("final_trade_decision", "(no decision available)")
    parts.append(f"**Portfolio Manager Decision:**\n{ftd}")

    return "\n\n".join(parts)


def _generate_merge_report(ticker_results, config, portfolio=None, strategy=None, tax_summaries=None):
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

    include_analyst_reports = len(ticker_results) <= FULL_REPORT_TICKER_LIMIT

    ticker_sections = [
        _build_ticker_section(ticker, decision, final_state, include_analyst_reports)
        for ticker, decision, final_state in ticker_results
    ]
    ticker_decisions = "\n\n---\n\n".join(ticker_sections)

    if portfolio:
        holdings_lines = []
        for sym, qty in portfolio["holdings"].items():
            line = f"  - {sym}: {qty} shares"
            if tax_summaries and sym in tax_summaries:
                line += f" ({tax_summaries[sym]})"
            holdings_lines.append(line)
        portfolio_context = (
            f"**Current Portfolio:**\n" + "\n".join(holdings_lines) + "\n"
            f"  - Cash available: ${portfolio['cash']:,.2f}\n"
        )
        allocation_instruction = (
            "Recommend concrete rebalancing actions given the current holdings and "
            "available cash. Specify which positions to increase, decrease, or exit, "
            "and how to deploy available cash."
        )
    else:
        portfolio_context = (
            "**Portfolio Context:** No existing holdings. "
            "Assume fresh capital to be allocated across these tickers.\n"
        )
        allocation_instruction = (
            "Recommend a percentage allocation of capital across these tickers. "
            "Allocations must sum to 100% (cash is a valid allocation). "
            "Justify the weighting relative to each ticker's rating and risk profile."
        )

    lang = config.get("output_language", "English")
    language_instruction = "" if lang.strip().lower() == "english" else f" Write your entire response in {lang}."

    strategy_instruction = ""
    if strategy:
        strategy_instruction = f"**Investment Strategy:** {strategy}\nApply this risk profile when ranking tickers and recommending capital allocation.\n"

    report_type = "full analysis reports" if include_analyst_reports else "Portfolio Manager decisions"

    prompt = f"""You are a Chief Investment Officer. You have received {report_type} for {len(ticker_results)} tickers. Each ticker was analyzed by a team of market, sentiment, news, and fundamentals analysts, followed by research debate, trading, and risk management — culminating in a Portfolio Manager decision.

**Rating Scale Reference:**
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Individual Ticker Analyses:**

{ticker_decisions}

---

{portfolio_context}
{strategy_instruction}
Produce a report with these sections:

### 1. Cross-Ticker Dependencies and Contradictions
Identify cases where one ticker's bull/bear thesis conflicts with or depends on another's. Examples: competing for the same market, inverse correlation, shared supply chain risks, mutually exclusive macro assumptions. This is the most important section — surface hidden conflicts the per-ticker analyses could not see in isolation.

### 2. Comparative Ranking
Rank all tickers from most to least attractive, accounting for the dependencies found above. One-sentence justification each.

### 3. Capital Allocation
{allocation_instruction}

### 4. Cross-Cutting Risks
Risks affecting multiple tickers simultaneously (macro correlation, sector concentration, geopolitical exposure, interest rate sensitivity). Note which tickers share each risk and how it changes the portfolio's overall risk profile.

### 5. Actionable Summary
3-5 bullet action plan: what to buy first, what to avoid, what conditions would change the ranking.

Be decisive. Ground every conclusion in specific evidence from the analyst reports and decisions above.{language_instruction}"""

    response = llm.invoke(prompt)
    return response.content


def _find_latest_report_dir(output_dir, ticker):
    """Find the most recent report directory for a ticker.

    Directories are named ``{TICKER}_{YYYY-MM-DD}`` and sorted lexicographically
    so the last match is the most recent date.
    """
    output_path = Path(output_dir)
    if not output_path.is_dir():
        return None
    candidates = sorted(
        d for d in output_path.iterdir()
        if d.is_dir() and d.name.startswith(f"{ticker}_")
    )
    return candidates[-1] if candidates else None


def _load_report_from_disk(report_dir):
    """Reconstruct a final_state dict from saved report files on disk."""
    report_dir = Path(report_dir)
    state = {}

    file_map = {
        "market_report": "1_analysts/market.md",
        "sentiment_report": "1_analysts/sentiment.md",
        "news_report": "1_analysts/news.md",
        "fundamentals_report": "1_analysts/fundamentals.md",
        "trader_investment_plan": "3_trading/trader.md",
    }
    for key, rel_path in file_map.items():
        path = report_dir / rel_path
        if path.is_file():
            state[key] = path.read_text(encoding="utf-8")

    decision_path = report_dir / "5_portfolio/decision.md"
    if decision_path.is_file():
        state["final_trade_decision"] = decision_path.read_text(encoding="utf-8")

    return state


def _save_merge_report(report, output_dir, tickers):
    merge_dir = Path(output_dir) / "_comparison"
    merge_dir.mkdir(parents=True, exist_ok=True)

    header = (
        f"# Cross-Ticker Comparison Report\n\n"
        f"**Tickers:** {', '.join(tickers)}\n"
        f"**Generated:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )

    report_path = merge_dir / "merge_report.md"
    report_path.write_text(header + report, encoding="utf-8")
    return report_path


@app.command()
def analyze(
    checkpoint: bool = typer.Option(
        False,
        "--checkpoint",
        help="Enable checkpoint/resume: save state after each node so a crashed run can resume.",
    ),
    clear_checkpoints: bool = typer.Option(
        False,
        "--clear-checkpoints",
        help="Delete all saved checkpoints before running (force fresh start).",
    ),
):
    if clear_checkpoints:
        from tradingagents.graph.checkpointer import clear_all_checkpoints
        n = clear_all_checkpoints(DEFAULT_CONFIG["data_cache_dir"])
        console.print(f"[yellow]Cleared {n} checkpoint(s).[/yellow]")
    run_analysis(checkpoint=checkpoint)


@app.command()
def batch(
    tickers: Optional[list[str]] = typer.Argument(
        None, help="Ticker symbols to analyze (inferred from --portfolio if omitted)"
    ),
    output_dir: Path = typer.Option(
        Path("./reports"), "--output-dir", "-o",
        help="Directory to save all reports",
    ),
    date: Optional[str] = typer.Option(
        None, "--date", "-d",
        help="Analysis date (YYYY-MM-DD). Defaults to today.",
    ),
    depth: str = typer.Option(
        "medium", "--depth",
        help="Research depth: shallow, medium, or deep",
    ),
    provider: str = typer.Option(
        "ollama", "--provider", "-p",
        help="LLM provider (e.g., ollama, openai, anthropic)",
    ),
    quick_model: str = typer.Option(
        "qwen3:8b", "--quick-model",
        help="Model for quick/shallow thinking",
    ),
    deep_model: str = typer.Option(
        "qwen3:32b", "--deep-model",
        help="Model for deep thinking",
    ),
    language: str = typer.Option(
        "English", "--language", "-l",
        help="Output language for reports",
    ),
    no_merge: bool = typer.Option(
        False, "--no-merge",
        help="Skip the cross-ticker merge report",
    ),
    merge_only: bool = typer.Option(
        False, "--merge-only",
        help="Skip analysis; merge the latest existing reports for the given tickers",
    ),
    portfolio_file: Optional[Path] = typer.Option(
        None, "--portfolio",
        help="CSV file with portfolio holdings (E*Trade export or generic format)",
    ),
    position: Optional[list[str]] = typer.Option(
        None, "--position",
        help="Inline position as TICKER:QUANTITY (repeatable)",
    ),
    cash: float = typer.Option(
        0.0, "--cash",
        help="Cash available for allocation",
    ),
    portfolio_format: Optional[str] = typer.Option(
        None, "--portfolio-format",
        help="Portfolio file format: etrade, generic (auto-detected if omitted)",
    ),
    strategy: str = typer.Option(
        "balanced", "--strategy", "-s",
        help="Investment risk strategy: conservative, balanced, aggressive",
    ),
):
    """Analyze multiple tickers sequentially with fixed parameters."""
    from cli.portfolio import load_portfolio

    portfolio = load_portfolio(
        path=portfolio_file,
        positions=position,
        cash=cash,
        format_override=portfolio_format,
    )

    if not tickers:
        if portfolio and portfolio.holdings:
            tickers = portfolio.ticker_symbols()
            console.print(f"[cyan]Inferred tickers from portfolio: {', '.join(tickers)}[/cyan]")
        else:
            console.print("[red]No tickers provided and no portfolio to infer from. Aborting.[/red]")
            raise typer.Exit(1)
    else:
        tickers = [normalize_ticker_symbol(t) for t in tickers]
        if portfolio and portfolio.holdings:
            for sym in portfolio.ticker_symbols():
                if sym not in tickers:
                    tickers.append(sym)

    analysis_date = date or datetime.datetime.now().strftime("%Y-%m-%d")
    depth_value = DEPTH_MAP.get(depth.lower(), 3)
    backend_url = PROVIDER_BACKEND_URLS.get(provider.lower())
    all_analyst_keys = ["market", "social", "news", "fundamentals"]

    selections = {
        "research_depth": depth_value,
        "llm_provider": provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": quick_model,
        "deep_thinker": deep_model,
        "google_thinking_level": None,
        "openai_reasoning_effort": None,
        "anthropic_effort": None,
        "output_language": language,
    }
    config = _build_config(selections)

    strategy_text = STRATEGY_PROFILES.get(strategy.lower(), STRATEGY_PROFILES["balanced"])

    portfolio_dict = portfolio.to_dict() if portfolio else None

    if merge_only:
        from tradingagents.agents.utils.rating import parse_rating

        merge_panel_lines = [
            "[bold]Merge-Only Mode[/bold]",
            f"Tickers: {', '.join(tickers)}",
            f"Looking for existing reports in: {output_dir.resolve()}",
        ]
        if portfolio:
            merge_panel_lines.append(
                f"Portfolio: {len(portfolio.holdings)} holdings, ${portfolio.cash:,.2f} cash"
            )
        console.print(Panel("\n".join(merge_panel_lines), border_style="yellow"))

        completed_states = []
        for ticker in tickers:
            report_dir = _find_latest_report_dir(output_dir, ticker)
            if report_dir is None:
                console.print(f"[yellow]No report found for {ticker} — skipping[/yellow]")
                continue

            final_state = _load_report_from_disk(report_dir)
            if not final_state.get("final_trade_decision"):
                console.print(f"[yellow]{ticker}: report in {report_dir.name} has no portfolio decision — skipping[/yellow]")
                continue

            decision = parse_rating(final_state["final_trade_decision"])
            completed_states.append((ticker, decision, final_state))
            console.print(f"[green]Loaded {ticker} from {report_dir.name} — {decision}[/green]")

        if len(completed_states) < 2:
            console.print("[red]Need at least 2 ticker reports to generate a merge. Aborting.[/red]")
            raise typer.Exit(1)

        console.print(f"\n[bold cyan]Generating cross-ticker comparison report for {len(completed_states)} tickers...[/bold cyan]")
        report = _generate_merge_report(completed_states, config, portfolio=portfolio_dict, strategy=strategy_text)
        report_path = _save_merge_report(report, output_dir, [t for t, _, _ in completed_states])
        console.print(f"[green]Merge report saved to:[/green] {report_path.resolve()}")
        console.print()
        console.print(Panel(Markdown(report), title="Cross-Ticker Comparison", border_style="green"))
        return

    batch_panel_lines = [
        "[bold]Batch Analysis[/bold]",
        f"Tickers: {', '.join(tickers)}",
        f"Date: {analysis_date} | Depth: {depth} | Provider: {provider}",
        f"Models: {quick_model} (quick) / {deep_model} (deep)",
        f"Output: {output_dir.resolve()}",
    ]
    if portfolio:
        batch_panel_lines.append(
            f"Portfolio: {len(portfolio.holdings)} holdings, ${portfolio.cash:,.2f} cash"
        )
    console.print(Panel("\n".join(batch_panel_lines), border_style="green"))

    graph = TradingAgentsGraph(
        all_analyst_keys,
        config=config,
        debug=True,
    )

    results = []
    completed_states = []

    for i, ticker in enumerate(tickers, 1):
        console.print(f"\n[bold cyan]{'=' * 60}[/bold cyan]")
        console.print(f"[bold cyan]Ticker {i}/{len(tickers)}: {ticker}[/bold cyan]")
        console.print(f"[bold cyan]{'=' * 60}[/bold cyan]\n")

        ticker_output = output_dir / f"{ticker}_{analysis_date}"

        try:
            result = _run_single_ticker(
                ticker=ticker,
                analysis_date=analysis_date,
                selected_analyst_keys=all_analyst_keys,
                config=config,
                graph=graph,
                output_dir=ticker_output,
            )
            results.append((ticker, "SUCCESS", result["decision"], result["elapsed"]))
            completed_states.append((ticker, result["decision"], result["final_state"]))
            console.print(f"\n[green]Completed {ticker} in {result['elapsed']:.0f}s[/green]")
        except Exception as e:
            results.append((ticker, "FAILED", str(e), 0))
            console.print(f"\n[red]Failed {ticker}: {e}[/red]")

    _print_batch_summary(results, output_dir)

    if not no_merge and len(completed_states) >= 2:
        console.print("\n[bold cyan]Generating cross-ticker comparison report...[/bold cyan]")
        try:
            report = _generate_merge_report(completed_states, config, portfolio=portfolio_dict, strategy=strategy_text)
            report_path = _save_merge_report(report, output_dir, [t for t, _, _ in completed_states])
            console.print(f"[green]Merge report saved to:[/green] {report_path.resolve()}")
            console.print()
            console.print(Panel(Markdown(report), title="Cross-Ticker Comparison", border_style="green"))
        except Exception as e:
            console.print(f"\n[red]Merge report generation failed: {e}[/red]")
    elif not no_merge and len(completed_states) < 2:
        console.print("[yellow]Skipping merge report: need at least 2 successful analyses.[/yellow]")


@app.command()
def paper(
    tickers: Optional[list[str]] = typer.Argument(
        None, help="Extra ticker symbols to consider buying (portfolio tickers are always included)",
    ),
    key: Optional[str] = typer.Option(
        None, "--key",
        help="Alpaca API key (or set ALPACA_API_KEY env var)",
    ),
    secret: Optional[str] = typer.Option(
        None, "--secret",
        help="Alpaca API secret (or set ALPACA_API_SECRET env var)",
    ),
    live: bool = typer.Option(
        False, "--live",
        help="Use live trading instead of paper trading. Real money!",
    ),
    output_dir: Path = typer.Option(
        Path("./reports"), "--output-dir", "-o",
        help="Directory to save all reports",
    ),
    depth: str = typer.Option(
        "medium", "--depth",
        help="Research depth: shallow, medium, or deep",
    ),
    provider: str = typer.Option(
        "ollama", "--provider", "-p",
        help="LLM provider (e.g., ollama, openai, anthropic)",
    ),
    quick_model: str = typer.Option(
        "qwen3:8b", "--quick-model",
        help="Model for quick/shallow thinking",
    ),
    deep_model: str = typer.Option(
        "qwen3:32b", "--deep-model",
        help="Model for deep thinking",
    ),
    language: str = typer.Option(
        "English", "--language", "-l",
        help="Output language for reports",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show proposed trades but do not submit orders",
    ),
    auto_execute: bool = typer.Option(
        False, "--auto-execute",
        help="Execute orders immediately without confirmation prompt",
    ),
    skip_analysis: bool = typer.Option(
        False, "--skip-analysis",
        help="Skip ticker analysis; use latest existing reports from output dir",
    ),
    strategy: str = typer.Option(
        "balanced", "--strategy", "-s",
        help="Investment risk strategy: conservative, balanced, aggressive",
    ),
    tax_bracket: str = typer.Option(
        "top", "--tax-bracket",
        help="Tax bracket for sell analysis: top, mid, low, none",
    ),
):
    """Connect to Alpaca, analyze portfolio + extra tickers, and auto-execute trades."""
    from cli.alpaca_client import (
        create_client, fetch_portfolio, fetch_quotes,
        submit_orders, resolve_credentials,
    )
    from cli.order_parser import parse_orders, format_pending_orders
    from cli.tax import compute_tax_context, format_tax_context_for_prompt, format_tax_context_for_portfolio

    api_key, api_secret = resolve_credentials(key, secret)

    if live:
        console.print(Panel(
            "[bold red]LIVE TRADING MODE[/bold red]\n"
            "[red]Real money will be used. Orders will be submitted to your live Alpaca account.[/red]",
            border_style="red",
        ))

    mode = "LIVE" if live else "PAPER"
    console.print(f"[cyan]Connecting to Alpaca ({mode})...[/cyan]")
    client = create_client(api_key, api_secret, paper=not live)

    console.print("[cyan]Fetching portfolio and pending orders...[/cyan]")
    portfolio, pending, position_prices, position_details = fetch_portfolio(client)

    console.print(Panel(
        f"[bold]Alpaca Portfolio ({mode})[/bold]\n"
        f"Holdings: {len(portfolio.holdings)} positions\n"
        f"Cash: ${portfolio.cash:,.2f}\n"
        + (format_pending_orders(pending) if pending else "Pending Orders: None"),
        border_style="green" if not live else "red",
    ))

    extra_tickers = [normalize_ticker_symbol(t) for t in tickers] if tickers else []
    all_tickers = list(portfolio.ticker_symbols())
    for o in pending:
        sym = o["symbol"]
        if sym not in all_tickers:
            all_tickers.append(sym)
    for t in extra_tickers:
        if t not in all_tickers:
            all_tickers.append(t)

    if not all_tickers:
        console.print("[red]No tickers to analyze (empty portfolio and no extra tickers). Aborting.[/red]")
        raise typer.Exit(1)

    new_tickers = [t for t in all_tickers if t not in portfolio.holdings]
    quotes = {}
    if new_tickers:
        console.print(f"[cyan]Fetching quotes for: {', '.join(new_tickers)}...[/cyan]")
        quotes = fetch_quotes(client, new_tickers)

    analysis_date = datetime.datetime.now().strftime("%Y-%m-%d")
    depth_value = DEPTH_MAP.get(depth.lower(), 3)
    backend_url = PROVIDER_BACKEND_URLS.get(provider.lower())
    all_analyst_keys = ["market", "social", "news", "fundamentals"]

    selections = {
        "research_depth": depth_value,
        "llm_provider": provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": quick_model,
        "deep_thinker": deep_model,
        "google_thinking_level": None,
        "openai_reasoning_effort": None,
        "anthropic_effort": None,
        "output_language": language,
    }
    config = _build_config(selections)

    strategy_text = STRATEGY_PROFILES.get(strategy.lower(), STRATEGY_PROFILES["balanced"])

    all_prices = {**position_prices, **quotes}
    tax_ctx = {}
    tax_prompt_str = ""
    if tax_bracket.lower() != "none" and position_details:
        tax_ctx = compute_tax_context(position_details, all_prices, bracket=tax_bracket.lower())
        tax_prompt_str = format_tax_context_for_prompt(tax_ctx)
        tax_portfolio_summaries = format_tax_context_for_portfolio(tax_ctx, position_details)
    else:
        tax_portfolio_summaries = {}

    console.print(Panel(
        f"[bold]Analysis Plan[/bold]\n"
        f"Tickers: {', '.join(all_tickers)}\n"
        f"Date: {analysis_date} | Depth: {depth} | Provider: {provider}\n"
        f"Models: {quick_model} (quick) / {deep_model} (deep)"
        + ("\n[yellow]Skip-analysis: using existing reports[/yellow]" if skip_analysis else "")
        + ("\n[yellow]Dry-run: orders will NOT be submitted[/yellow]" if dry_run else ""),
        border_style="cyan",
    ))

    results = []
    completed_states = []

    if skip_analysis:
        from tradingagents.agents.utils.rating import parse_rating

        missing = []
        for ticker in all_tickers:
            report_dir = _find_latest_report_dir(output_dir, ticker)
            if report_dir is None:
                missing.append(ticker)
                continue
            final_state = _load_report_from_disk(report_dir)
            if not final_state.get("final_trade_decision"):
                missing.append(ticker)
                console.print(f"[yellow]{ticker}: report in {report_dir.name} has no portfolio decision — skipping[/yellow]")
                continue
            decision = parse_rating(final_state["final_trade_decision"])
            completed_states.append((ticker, decision, final_state))
            console.print(f"[green]Loaded {ticker} from {report_dir.name} — {decision}[/green]")

        if missing:
            console.print(f"[yellow]No reports found for: {', '.join(missing)}[/yellow]")
    else:
        graph = TradingAgentsGraph(
            all_analyst_keys,
            config=config,
            debug=True,
        )

        for i, ticker in enumerate(all_tickers, 1):
            console.print(f"\n[bold cyan]{'=' * 60}[/bold cyan]")
            console.print(f"[bold cyan]Ticker {i}/{len(all_tickers)}: {ticker}[/bold cyan]")
            console.print(f"[bold cyan]{'=' * 60}[/bold cyan]\n")

            ticker_output = output_dir / f"{ticker}_{analysis_date}"

            try:
                result = _run_single_ticker(
                    ticker=ticker,
                    analysis_date=analysis_date,
                    selected_analyst_keys=all_analyst_keys,
                    config=config,
                    graph=graph,
                    output_dir=ticker_output,
                )
                results.append((ticker, "SUCCESS", result["decision"], result["elapsed"]))
                completed_states.append((ticker, result["decision"], result["final_state"]))
                console.print(f"\n[green]Completed {ticker} in {result['elapsed']:.0f}s[/green]")
            except Exception as e:
                results.append((ticker, "FAILED", str(e), 0))
                console.print(f"\n[red]Failed {ticker}: {e}[/red]")

        _print_batch_summary(results, output_dir)

    if len(completed_states) < 2:
        console.print("[yellow]Need at least 2 successful analyses for a merge report. Skipping trade execution.[/yellow]")
        return

    portfolio_dict = portfolio.to_dict()

    console.print("\n[bold cyan]Generating cross-ticker comparison report...[/bold cyan]")
    merge_report = _generate_merge_report(completed_states, config, portfolio=portfolio_dict, strategy=strategy_text, tax_summaries=tax_portfolio_summaries)
    report_path = _save_merge_report(merge_report, output_dir, [t for t, _, _ in completed_states])
    console.print(f"[green]Merge report saved to:[/green] {report_path.resolve()}")
    console.print()
    console.print(Panel(Markdown(merge_report), title="Cross-Ticker Comparison", border_style="green"))

    console.print("\n[bold cyan]Generating trade plan...[/bold cyan]")
    try:
        trade_plan = parse_orders(merge_report, portfolio_dict, {**position_prices, **quotes}, pending, config, strategy=strategy_text, tax_context_str=tax_prompt_str)
    except Exception as e:
        console.print(f"[red]Trade plan generation failed: {e}[/red]")
        return

    if not trade_plan.orders:
        console.print("[yellow]No trades recommended. Portfolio unchanged.[/yellow]")
        return

    order_table = Table(
        title="Trade Plan",
        show_header=True,
        header_style="bold magenta",
        box=box.ROUNDED,
    )
    order_table.add_column("Symbol", style="cyan", justify="center")
    order_table.add_column("Side", justify="center")
    order_table.add_column("Qty", justify="right")
    for order in trade_plan.orders:
        side_str = "[green]BUY[/green]" if order.side == "buy" else "[red]SELL[/red]"
        order_table.add_row(order.symbol, side_str, str(int(order.qty)))
    console.print(order_table)
    console.print(f"\n[dim]Reasoning: {trade_plan.reasoning}[/dim]")

    # Build projected portfolio after pending + new orders
    all_prices = {**position_prices, **quotes}
    projected_holdings = dict(portfolio.holdings)
    projected_cash = portfolio.cash

    for o in pending:
        sym = o["symbol"]
        qty = o.get("qty") or 0
        filled = o.get("filled_qty", 0)
        remaining = qty - filled
        if remaining <= 0:
            continue
        price = all_prices.get(sym, 0)
        if o["side"] == "buy":
            projected_holdings[sym] = projected_holdings.get(sym, 0) + remaining
            projected_cash -= remaining * price
        elif o["side"] == "sell":
            projected_holdings[sym] = projected_holdings.get(sym, 0) - remaining
            projected_cash += remaining * price

    for order in trade_plan.orders:
        sym = order.symbol
        qty = int(order.qty)
        price = all_prices.get(sym, 0)
        if order.side == "buy":
            projected_holdings[sym] = projected_holdings.get(sym, 0) + qty
            projected_cash -= qty * price
        elif order.side == "sell":
            projected_holdings[sym] = projected_holdings.get(sym, 0) - qty
            projected_cash += qty * price

    projected_holdings = {s: q for s, q in projected_holdings.items() if q > 0}

    proj_table = Table(
        title="Projected Portfolio (after pending + new orders)",
        show_header=True,
        header_style="bold magenta",
        box=box.ROUNDED,
    )
    proj_table.add_column("Symbol", style="cyan", justify="center")
    proj_table.add_column("Qty", justify="right")
    proj_table.add_column("Price", justify="right")
    proj_table.add_column("Value", justify="right")

    total_value = 0.0
    for sym in sorted(projected_holdings):
        qty = projected_holdings[sym]
        price = all_prices.get(sym)
        if price is not None:
            val = qty * price
            total_value += val
            proj_table.add_row(sym, f"{qty:,.2f}", f"${price:,.2f}", f"${val:,.2f}")
        else:
            proj_table.add_row(sym, f"{qty:,.2f}", "—", "—")

    proj_table.add_row("", "", "", "─" * 12, style="dim")
    proj_table.add_row("CASH", "", "", f"${projected_cash:,.2f}", style="bold")
    proj_table.add_row("TOTAL", "", "", f"${total_value + projected_cash:,.2f}", style="bold green")
    console.print(proj_table)

    if dry_run:
        console.print("\n[yellow]Dry-run mode — no orders submitted.[/yellow]")
        return

    if not auto_execute:
        confirm = typer.prompt(
            f"\nSubmit {len(trade_plan.orders)} order(s) to Alpaca ({mode})? [y/N]",
            default="N",
        ).strip().upper()
        if confirm not in ("Y", "YES"):
            console.print("[yellow]Aborted — no orders submitted.[/yellow]")
            return

    console.print(f"\n[bold cyan]Submitting {len(trade_plan.orders)} order(s) to Alpaca ({mode})...[/bold cyan]")
    order_dicts = [{"symbol": o.symbol, "side": o.side, "qty": int(o.qty)} for o in trade_plan.orders]
    exec_results = submit_orders(client, order_dicts)

    exec_table = Table(
        title="Execution Results",
        show_header=True,
        header_style="bold magenta",
        box=box.ROUNDED,
    )
    exec_table.add_column("Symbol", style="cyan", justify="center")
    exec_table.add_column("Side", justify="center")
    exec_table.add_column("Qty", justify="right")
    exec_table.add_column("Status", justify="center")
    exec_table.add_column("Order ID", style="dim")

    for r in exec_results:
        side_str = "[green]BUY[/green]" if r["side"] == "buy" else "[red]SELL[/red]"
        if r["error"]:
            status_str = f"[red]{r['error'][:40]}[/red]"
        else:
            status_str = f"[green]{r['status']}[/green]"
        exec_table.add_row(
            r["symbol"], side_str, str(r["qty"]),
            status_str, r.get("order_id", "-") or "-",
        )
    console.print(exec_table)

    trades_dir = output_dir / "_trades"
    trades_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    trade_log = trades_dir / f"trade_log_{timestamp}.md"

    log_lines = [
        f"# Trade Execution Log",
        f"**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Mode:** {mode}",
        f"",
        f"## Trade Plan",
        f"**Reasoning:** {trade_plan.reasoning}",
        f"",
    ]
    for o in trade_plan.orders:
        log_lines.append(f"- {o.side.upper()} {int(o.qty)} {o.symbol}")
    log_lines.append("")
    log_lines.append("## Execution Results")
    for r in exec_results:
        status = r["error"] or r["status"]
        log_lines.append(f"- {r['side'].upper()} {r['qty']} {r['symbol']}: {status} (order_id: {r.get('order_id', '-')})")
    trade_log.write_text("\n".join(log_lines), encoding="utf-8")
    console.print(f"\n[green]Trade log saved to:[/green] {trade_log.resolve()}")


if __name__ == "__main__":
    app()
