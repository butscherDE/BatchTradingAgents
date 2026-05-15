import datetime
import enum

from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON, Enum, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class InvestigationStatus(str, enum.Enum):
    queued = "queued"
    quick_screening = "quick_screening"
    quick_no_action = "quick_no_action"
    escalated = "escalated"
    deep_investigating = "deep_investigating"
    deep_no_action = "deep_no_action"
    report_generated = "report_generated"


class TaskStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alpaca_id = Column(String, unique=True, nullable=True)
    headline_hash = Column(String(64), unique=True, nullable=True, index=True)
    headline = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    symbols = Column(JSON, nullable=False, default=list)
    published_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    status = Column(Enum(InvestigationStatus), nullable=False, default=InvestigationStatus.queued)
    quick_result = Column(JSON, nullable=True)
    deep_result = Column(JSON, nullable=True)
    escalation_reason = Column(Text, nullable=True)


class GpuTask(Base):
    __tablename__ = "gpu_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, unique=True, nullable=False)
    model_tier = Column(String, nullable=False)  # "quick" or "deep"
    task_type = Column(String, nullable=False)
    ticker = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    priority = Column(Integer, nullable=False, default=1)
    status = Column(Enum(TaskStatus), nullable=False, default=TaskStatus.queued)
    payload = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)


class TradeAction(Base):
    __tablename__ = "trade_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, nullable=False, index=True)
    ticker = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)  # "buy", "sell", "sell_emergency"
    qty = Column(Float, nullable=True)
    notional = Column(Float, nullable=True)
    trigger = Column(String, nullable=False, default="manual")  # "proposal", "emergency_sell", "manual"
    trigger_reason = Column(Text, nullable=True)
    proposal_id = Column(Integer, nullable=True, index=True)
    report_id = Column(Integer, nullable=True)
    order_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    error = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    filled_qty = Column(Float, nullable=True)
    filled_avg_price = Column(Float, nullable=True)
    filled_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)


class WatchlistTicker(Base):
    __tablename__ = "watchlist_tickers"
    __table_args__ = (
        UniqueConstraint("account_id", "symbol", name="uq_watchlist_account_symbol"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False)
    added_by = Column(String, nullable=False, default="manual")
    added_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    removed_at = Column(DateTime, nullable=True)
    remove_reason = Column(Text, nullable=True)
    active = Column(Integer, nullable=False, default=1)


class WatchlistEvent(Base):
    __tablename__ = "watchlist_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False)
    action = Column(String, nullable=False)  # "added", "removed", "prune_kept"
    trigger = Column(String, nullable=False)  # "manual", "auto_discovery", "auto_prune", "config"
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


class ProposalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    superseded = "superseded"


class TradeProposal(Base):
    __tablename__ = "trade_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, nullable=False, index=True)
    strategy = Column(String, nullable=False)
    status = Column(Enum(ProposalStatus), nullable=False, default=ProposalStatus.pending, index=True)
    merge_report = Column(Text, nullable=False)
    tickers = Column(JSON, nullable=False)
    ticker_data = Column(JSON, nullable=False)
    allocation = Column(JSON, nullable=True)  # [{symbol, action, pct}]
    allocation_reasoning = Column(Text, nullable=True)
    cash_pct = Column(Float, nullable=True)
    proposed_orders = Column(JSON, nullable=True)  # [{ticker, side, qty}]
    portfolio_value = Column(Float, nullable=True)
    cash_after = Column(Float, nullable=True)
    source_task_id = Column(String, nullable=True)
    superseded_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)
    execution_results = Column(JSON, nullable=True)
