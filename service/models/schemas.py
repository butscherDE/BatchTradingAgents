"""Pydantic models for API request/response schemas."""

import datetime
from typing import Optional

from pydantic import BaseModel


class NewsArticleResponse(BaseModel):
    id: int
    alpaca_id: Optional[str] = None
    headline: str
    summary: Optional[str] = None
    source: Optional[str] = None
    symbols: list[str] = []
    published_at: Optional[datetime.datetime] = None
    received_at: datetime.datetime
    status: str
    quick_result: Optional[dict] = None
    deep_result: Optional[dict] = None
    escalation_reason: Optional[str] = None


class TaskResponse(BaseModel):
    id: int
    task_id: str
    model_tier: str
    task_type: str
    ticker: Optional[str] = None
    provider: Optional[str] = None
    priority: int
    status: str
    created_at: datetime.datetime
    started_at: Optional[datetime.datetime] = None
    completed_at: Optional[datetime.datetime] = None
    error: Optional[str] = None


class ProviderStatus(BaseModel):
    name: str
    state: Optional[str] = None
    queue_depth: int = 0
    quick_queued: int = 0
    deep_queued: int = 0
    active_tasks: int = 0
    max_concurrent: int = 1
    max_queue: int = -1
    current_model: Optional[str] = None
    completed: int = 0
    failed: int = 0


class TaskStats(BaseModel):
    queue_depth: int = 0
    total_completed: int = 0
    total_failed: int = 0
    current_model: Optional[str] = None
    worker_state: Optional[str] = None
    model_switches: int = 0
    tasks_per_minute: float = 0.0
    providers: list[ProviderStatus] = []


class HealthResponse(BaseModel):
    status: str = "ok"
    worker_state: Optional[str] = None
    queue_depths: dict[str, int] = {}
    uptime_seconds: float = 0.0
