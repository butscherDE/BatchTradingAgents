import datetime
import enum

from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON, Enum
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


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alpaca_id = Column(String, unique=True, nullable=True)
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
    account_id = Column(String, nullable=False)
    ticker = Column(String, nullable=False)
    action = Column(String, nullable=False)  # "sell_emergency", "buy", "sell"
    qty = Column(Float, nullable=True)
    trigger_reason = Column(Text, nullable=True)
    report_id = Column(Integer, nullable=True)
    order_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    submitted_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
