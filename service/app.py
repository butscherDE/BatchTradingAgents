"""FastAPI application factory and lifecycle management."""

import asyncio
import datetime
import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from service.config import ServiceConfig, load_config
from service.core.gpu_scheduler import GpuScheduler, TaskSpec
from service.core.debounce import MergeDebouncer
from service.db.engine import get_async_engine, get_async_session_factory, init_db
from service.db.models import NewsArticle, GpuTask, InvestigationStatus, TaskStatus
from service.streams.manager import StreamManager

logger = logging.getLogger(__name__)

_config: ServiceConfig | None = None
_scheduler: GpuScheduler | None = None
_session_factory = None
_start_time: float = 0
_debouncer: MergeDebouncer | None = None
_latest_reports: dict[str, dict] = {}  # ticker -> latest analysis result
_orphaned_tasks: list = []  # tasks recovered from crash


def get_scheduler() -> GpuScheduler:
    assert _scheduler is not None
    return _scheduler


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


async def _seed_watchlist(config_tickers: list[str]):
    """Seed per-account watchlists from TOML on first run. Respects removals."""
    from service.db.models import WatchlistTicker
    from sqlalchemy import select

    if not _config:
        return

    async with get_db_session() as session:
        for account_name, account_config in _config.accounts.items():
            # Load tickers for this account's named watchlist
            tickers = set()
            try:
                from cli.watchlist import load_watchlist
                if account_config.watchlist:
                    wl = load_watchlist(account_config.watchlist)
                    tickers.update(t.upper() for t in wl)
            except Exception:
                pass

            # Also add any explicit config tickers
            tickers.update(t.upper().strip() for t in config_tickers if t.strip())

            for sym in sorted(tickers):
                existing = await session.execute(
                    select(WatchlistTicker).where(
                        WatchlistTicker.account_id == account_name,
                        WatchlistTicker.symbol == sym,
                    )
                )
                if existing.scalar_one_or_none() is None:
                    session.add(WatchlistTicker(
                        account_id=account_name,
                        symbol=sym,
                        added_by="config",
                        active=1,
                    ))

        await session.commit()


async def _recover_orphaned_tasks():
    """Re-queue tasks that were 'running' when the service last crashed."""
    from sqlalchemy import select, update

    async with get_db_session() as session:
        result = await session.execute(
            select(GpuTask).where(GpuTask.status == TaskStatus.running)
        )
        orphaned = result.scalars().all()

        if not orphaned:
            return

        logger.info(f"Recovering {len(orphaned)} orphaned tasks from previous crash")

        for task in orphaned:
            # Reset to queued — the worker will re-process them
            task.status = TaskStatus.queued
            task.started_at = None

        await session.commit()

    # Re-submit to Redis queues (scheduler may not be connected yet, defer to after connect)
    global _orphaned_tasks
    _orphaned_tasks = orphaned


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config, _scheduler, _session_factory, _start_time, _debouncer
    import time

    _start_time = time.time()
    _config = load_config()

    # Merge debouncer (fixed-window batch)
    _debouncer = MergeDebouncer(
        debounce_seconds=_config.evaluation.debounce_seconds,
        on_fire=_on_debounce_fire,
    )

    # Database (always works — it's local SQLite)
    engine = get_async_engine(_config.database_path)
    await init_db(engine, _config.database_path)
    _session_factory = get_async_session_factory(engine)

    # Seed watchlist from config if DB is empty
    await _seed_watchlist(_config.watchlist.tickers)

    # Recover orphaned tasks from previous crash
    await _recover_orphaned_tasks()

    # Redis / GPU scheduler
    _scheduler = GpuScheduler(_config.redis_url)
    try:
        await _scheduler.connect()

        # Start result listener FIRST — before resubmitting tasks,
        # so we don't miss "running" events from the GPU worker
        result_task = asyncio.create_task(_listen_for_results())

        # Re-queue orphaned tasks now that Redis is available
        if _orphaned_tasks:
            for task in _orphaned_tasks:
                await _scheduler.submit(TaskSpec(
                    model_tier=task.model_tier,
                    task_type=task.task_type,
                    payload=task.payload or {},
                    ticker=task.ticker,
                    priority=task.priority,
                ))
            logger.info(f"Re-submitted {len(_orphaned_tasks)} orphaned tasks to Redis")
            _orphaned_tasks.clear()
    except Exception as e:
        logger.warning(f"Redis connection failed (tasks will not process): {e}")
        result_task = None

    # News stream (single connection — Alpaca connection limit)
    stream_manager = StreamManager(_config, on_news=_handle_news_article)
    try:
        await stream_manager.start()
    except Exception as e:
        logger.warning(f"News stream failed to start: {e}")

    # Daily prune scheduler
    prune_task = asyncio.create_task(_prune_scheduler())

    logger.info(f"Service started on {_config.host}:{_config.port}")
    yield

    # Shutdown
    prune_task.cancel()

    # Shutdown
    if result_task:
        result_task.cancel()
    if _debouncer:
        await _debouncer.cancel_all()
    await stream_manager.stop()
    await _scheduler.close()
    await engine.dispose()
    logger.info("Service shut down")


async def _handle_news_article(article: dict):
    """Called by stream manager when a new news article arrives."""
    from service.api.ws import broadcast
    from service.db.models import WatchlistTicker
    from sqlalchemy import select

    symbols = article.get("symbols", [])

    # Skip excluded tickers entirely
    if _config and _config.watchlist.exclude:
        exclude_set = {s.upper() for s in _config.watchlist.exclude}
        symbols = [s for s in symbols if s.upper() not in exclude_set]
        if not symbols:
            return

    # Check which accounts actively watch any of the article's symbols
    affected_accounts = []
    if symbols:
        async with get_db_session() as session:
            result = await session.execute(
                select(WatchlistTicker.account_id).distinct().where(
                    WatchlistTicker.symbol.in_([s.upper() for s in symbols]),
                    WatchlistTicker.active == 1,
                )
            )
            affected_accounts = list(result.scalars().all())

    on_watchlist = len(affected_accounts) > 0

    # If not on watchlist, check if any account has dynamic_discovery enabled
    discovery_accounts = []
    if not on_watchlist and _config:
        discovery_accounts = [
            name for name, acct in _config.accounts.items()
            if acct.dynamic_discovery
        ]
        if not discovery_accounts:
            return  # No one cares about unwatched tickers

    # Determine task type based on watchlist membership
    task_type = "news_screen" if on_watchlist else "watchlist_discovery"

    async with get_db_session() as session:
        db_article = NewsArticle(
            alpaca_id=article.get("alpaca_id"),
            headline=article["headline"],
            summary=article.get("summary"),
            source=article.get("source"),
            symbols=symbols,
            published_at=(
                datetime.datetime.fromisoformat(article["published_at"])
                if article.get("published_at") else None
            ),
            received_at=datetime.datetime.utcnow(),
            status=InvestigationStatus.queued,
        )
        session.add(db_article)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            return  # Duplicate article, skip
        await session.refresh(db_article)
        article_id = db_article.id

    ticker = symbols[0].upper() if symbols else None

    if on_watchlist:
        # Normal news screening for watched tickers
        task_id = await _scheduler.submit(TaskSpec(
            model_tier="quick",
            task_type="news_screen",
            payload={
                "article_id": article_id,
                "headline": article["headline"],
                "summary": article.get("summary", ""),
                "symbols": symbols,
            },
            ticker=ticker,
        ))
        async with get_db_session() as session:
            db_task = GpuTask(
                task_id=task_id,
                model_tier="quick",
                task_type="news_screen",
                ticker=ticker,
                priority=1,
                status=TaskStatus.queued,
                payload={"article_id": article_id},
            )
            session.add(db_task)
            await session.commit()
    else:
        # Discovery: submit per account with their strategy
        for acct_name in discovery_accounts:
            acct = _config.accounts[acct_name]
            task_id = await _scheduler.submit(TaskSpec(
                model_tier="quick",
                task_type="watchlist_discovery",
                payload={
                    "article_id": article_id,
                    "headline": article["headline"],
                    "summary": article.get("summary", ""),
                    "symbols": symbols,
                    "account_id": acct_name,
                    "strategy": acct.strategy,
                },
                ticker=ticker,
            ))
            async with get_db_session() as session:
                db_task = GpuTask(
                    task_id=task_id,
                    model_tier="quick",
                    task_type="watchlist_discovery",
                    ticker=ticker,
                    priority=1,
                    status=TaskStatus.queued,
                    payload={"article_id": article_id, "account_id": acct_name},
                )
                session.add(db_task)
                await session.commit()

    await broadcast("news_added", {
        "id": article_id,
        "headline": article["headline"],
        "symbols": symbols,
        "status": "queued",
        "on_watchlist": on_watchlist,
    })


async def _handle_price_bar(bar: dict):
    """Called by stream manager on each price bar update."""
    from service.api.ws import broadcast

    await broadcast("price_update", bar)


async def _listen_for_results():
    """Subscribe to GPU worker results via Redis pub/sub."""
    from service.api.ws import broadcast

    pubsub = await _scheduler.subscribe_results()

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            data = json.loads(message["data"])
            task_id = data.get("task_id")
            task_type = data.get("task_type")
            status = data.get("status")

            # Update task in DB
            async with get_db_session() as session:
                from sqlalchemy import update
                if status == "running":
                    await session.execute(
                        update(GpuTask)
                        .where(GpuTask.task_id == task_id)
                        .values(
                            status=TaskStatus.running,
                            started_at=datetime.datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
                        )
                    )
                else:
                    await session.execute(
                        update(GpuTask)
                        .where(GpuTask.task_id == task_id)
                        .values(
                            status=TaskStatus.completed if status == "completed" else TaskStatus.failed,
                            result=data.get("result"),
                            error=data.get("error"),
                            started_at=datetime.datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
                            completed_at=datetime.datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
                        )
                    )
                await session.commit()

            # Handle result based on task type
            if task_type == "news_screen" and status == "completed":
                await _handle_screen_result(data)
            elif task_type == "investigation" and status == "completed":
                await _handle_investigation_result(data)
            elif task_type == "full_analysis" and status == "completed":
                await _handle_analysis_result(data)
            elif task_type == "merge_and_allocate" and status == "completed":
                await _handle_merge_result(data)
            elif task_type == "watchlist_discovery" and status == "completed":
                await _handle_discovery_result(data)
            elif task_type == "watchlist_prune" and status == "completed":
                await _handle_prune_result(data)

            await broadcast("task_update", {
                "task_id": task_id,
                "task_type": task_type,
                "status": status,
                "ticker": data.get("ticker"),
            })

    except asyncio.CancelledError:
        await pubsub.unsubscribe()


async def _handle_screen_result(data: dict):
    """Process a completed news screening result."""
    from service.api.ws import broadcast

    result = data.get("result", {})
    payload = data.get("payload", {})
    article_id = payload.get("article_id") if payload else None

    # Try to get article_id from the task's stored payload
    if article_id is None:
        async with get_db_session() as session:
            from sqlalchemy import select
            task_result = await session.execute(
                select(GpuTask).where(GpuTask.task_id == data["task_id"])
            )
            task = task_result.scalar_one_or_none()
            if task and task.payload:
                article_id = task.payload.get("article_id")

    if article_id is None:
        return

    score = result.get("score", 0.0)
    threshold = _config.evaluation.news_relevance_min_score if _config else 0.6

    if score >= threshold:
        new_status = InvestigationStatus.escalated
        # Submit deep investigation
        async with get_db_session() as session:
            from sqlalchemy import select
            art_result = await session.execute(
                select(NewsArticle).where(NewsArticle.id == article_id)
            )
            article = art_result.scalar_one_or_none()

        if article:
            affected_ticker = result.get("affected_ticker") or (
                article.symbols[0] if article.symbols else None
            )
            if affected_ticker:
                task_id = await _scheduler.submit(TaskSpec(
                    model_tier="deep",
                    task_type="investigation",
                    payload={
                        "article_id": article_id,
                        "headline": article.headline,
                        "summary": article.summary or "",
                        "symbols": article.symbols or [],
                        "ticker": affected_ticker,
                    },
                    ticker=affected_ticker,
                ))

                async with get_db_session() as session:
                    db_task = GpuTask(
                        task_id=task_id,
                        model_tier="deep",
                        task_type="investigation",
                        ticker=affected_ticker,
                        priority=1,
                        status=TaskStatus.queued,
                        payload={"article_id": article_id},
                    )
                    session.add(db_task)
                    await session.commit()
    else:
        new_status = InvestigationStatus.quick_no_action

    # Update article status
    async with get_db_session() as session:
        from sqlalchemy import update
        await session.execute(
            update(NewsArticle)
            .where(NewsArticle.id == article_id)
            .values(
                status=new_status,
                quick_result=result,
                escalation_reason=result.get("reasoning") if score >= threshold else None,
            )
        )
        await session.commit()

    await broadcast("news_status_changed", {
        "id": article_id,
        "status": new_status.value,
        "score": score,
    })


async def _handle_investigation_result(data: dict):
    """Process a completed deep investigation — may trigger full analysis or emergency sell."""
    from service.api.ws import broadcast
    from service.db.models import TradeAction

    result = data.get("result", {})
    ticker = data.get("ticker")
    verdict = result.get("verdict", "noise")
    direction = result.get("direction")
    should_regenerate = result.get("should_regenerate_report", False)

    # Update news article status
    task_id = data.get("task_id")
    async with get_db_session() as session:
        from sqlalchemy import select
        task_result = await session.execute(
            select(GpuTask).where(GpuTask.task_id == task_id)
        )
        task = task_result.scalar_one_or_none()
        article_id = task.payload.get("article_id") if task and task.payload else None

    if article_id:
        new_status = (
            InvestigationStatus.report_generated if should_regenerate
            else InvestigationStatus.deep_no_action
        )
        async with get_db_session() as session:
            from sqlalchemy import update
            await session.execute(
                update(NewsArticle)
                .where(NewsArticle.id == article_id)
                .values(status=new_status, deep_result=result)
            )
            await session.commit()

    # If material change with sell direction → emergency sell for all accounts holding this ticker
    if verdict == "material_change" and direction == "sell" and ticker:
        for acct_name, acct in (_config.accounts.items() if _config else []):
            sell_result = await asyncio.to_thread(
                _execute_emergency_sell, acct, ticker, result.get("reasoning", "")
            )
            if sell_result and sell_result.get("qty_sold", 0) > 0:
                async with get_db_session() as session:
                    action = TradeAction(
                        account_id=acct_name,
                        ticker=ticker,
                        action="sell_emergency",
                        qty=sell_result["qty_sold"],
                        trigger_reason=result.get("reasoning", ""),
                        order_id=sell_result.get("order_id"),
                        status=sell_result.get("status", "submitted"),
                    )
                    session.add(action)
                    await session.commit()

                await broadcast("trade_executed", {
                    "account_id": acct_name,
                    "ticker": ticker,
                    "action": "sell_emergency",
                    "qty": sell_result["qty_sold"],
                    "reason": result.get("reasoning", ""),
                })

    # If should regenerate → submit full analysis task
    if should_regenerate and ticker:
        new_task_id = await _scheduler.submit(TaskSpec(
            model_tier="deep",
            task_type="full_analysis",
            payload={"ticker": ticker},
            ticker=ticker,
            priority=0 if direction == "sell" else 1,
        ))
        async with get_db_session() as session:
            db_task = GpuTask(
                task_id=new_task_id,
                model_tier="deep",
                task_type="full_analysis",
                ticker=ticker,
                priority=0 if direction == "sell" else 1,
                status=TaskStatus.queued,
                payload={"ticker": ticker},
            )
            session.add(db_task)
            await session.commit()

    await broadcast("investigation_complete", {
        "ticker": ticker,
        "verdict": verdict,
        "direction": direction,
        "should_regenerate": should_regenerate,
    })


async def _handle_analysis_result(data: dict):
    """Process a completed full analysis — triggers merge debounce."""
    from service.api.ws import broadcast

    result = data.get("result", {})
    ticker = result.get("ticker")
    decision = result.get("decision", "")

    if not ticker:
        return

    # Store the report state for later merge
    _latest_reports[ticker] = result

    # Trigger debounce for all accounts that have this ticker in their watchlist
    for acct_name in (_config.accounts if _config else {}):
        await _debouncer.ticker_updated(acct_name, ticker)

    await broadcast("report_generated", {
        "ticker": ticker,
        "decision": decision,
    })


async def _handle_merge_result(data: dict):
    """Process a completed merge+allocation — create a trade proposal."""
    from service.api.ws import broadcast
    from service.db.models import TradeProposal, ProposalStatus
    from sqlalchemy import select, update

    result = data.get("result", {})
    account_id = result.get("account_id")
    merge_report = result.get("merge_report", "")
    tickers = result.get("tickers", [])
    ticker_data = result.get("ticker_data", [])
    proposed_orders = result.get("proposed_orders", [])
    strategy = result.get("strategy", "balanced")
    task_id = data.get("task_id")

    if not account_id or not merge_report:
        return

    async with get_db_session() as session:
        # Supersede all pending proposals for this account
        pending_result = await session.execute(
            select(TradeProposal).where(
                TradeProposal.account_id == account_id,
                TradeProposal.status == ProposalStatus.pending,
            )
        )
        old_proposals = pending_result.scalars().all()
        old_ids = [p.id for p in old_proposals]

        # Create new proposal
        new_proposal = TradeProposal(
            account_id=account_id,
            strategy=strategy,
            status=ProposalStatus.pending,
            merge_report=merge_report,
            tickers=tickers,
            ticker_data=ticker_data,
            allocation=result.get("allocation", []),
            allocation_reasoning=result.get("allocation_reasoning", ""),
            cash_pct=result.get("cash_pct"),
            portfolio_value=result.get("portfolio_value"),
            cash_after=result.get("cash_after"),
            proposed_orders=proposed_orders,
            source_task_id=task_id,
        )
        session.add(new_proposal)
        await session.flush()

        # Mark old proposals as superseded
        if old_ids:
            await session.execute(
                update(TradeProposal)
                .where(TradeProposal.id.in_(old_ids))
                .values(
                    status=ProposalStatus.superseded,
                    superseded_by=new_proposal.id,
                )
            )

        await session.commit()
        proposal_id = new_proposal.id

    await broadcast("proposal_created", {
        "proposal_id": proposal_id,
        "account_id": account_id,
        "strategy": strategy,
        "tickers": tickers,
        "superseded_ids": old_ids,
    })


async def _handle_discovery_result(data: dict):
    """Process a watchlist discovery result — maybe add ticker to specific account."""
    from service.api.ws import broadcast
    from service.db.models import WatchlistTicker, WatchlistEvent

    result = data.get("result", {})
    ticker = data.get("ticker")
    should_add = result.get("add", False)
    reasoning = result.get("reasoning", "")

    # Get account_id from the task payload
    task_id = data.get("task_id")
    account_id = None
    article_id = None
    async with get_db_session() as session:
        from sqlalchemy import select
        task_result = await session.execute(
            select(GpuTask).where(GpuTask.task_id == task_id)
        )
        task = task_result.scalar_one_or_none()
        if task and task.payload:
            account_id = task.payload.get("account_id")
            article_id = task.payload.get("article_id")

    if should_add and ticker and account_id:
        async with get_db_session() as session:
            from sqlalchemy import select
            existing = await session.execute(
                select(WatchlistTicker).where(
                    WatchlistTicker.account_id == account_id,
                    WatchlistTicker.symbol == ticker.upper(),
                )
            )
            if not existing.scalar_one_or_none():
                session.add(WatchlistTicker(
                    account_id=account_id,
                    symbol=ticker.upper(),
                    added_by="auto_discovery",
                    added_at=datetime.datetime.utcnow(),
                    active=1,
                ))
                session.add(WatchlistEvent(
                    account_id=account_id,
                    symbol=ticker.upper(),
                    action="added",
                    trigger="auto_discovery",
                    reasoning=reasoning,
                ))
                await session.commit()
                logger.info(f"Auto-added {ticker} to {account_id} watchlist: {reasoning}")

                await broadcast("watchlist_changed", {
                    "action": "added",
                    "symbol": ticker.upper(),
                    "account_id": account_id,
                    "reason": reasoning,
                })

                # Trigger full analysis for the newly discovered ticker
                from pathlib import Path
                state_file = Path("reports") / "_states" / f"{ticker.upper()}.json"
                if not state_file.exists():
                    task_id = await _scheduler.submit(TaskSpec(
                        model_tier="deep",
                        task_type="full_analysis",
                        payload={"ticker": ticker.upper()},
                        ticker=ticker.upper(),
                    ))
                    async with get_db_session() as session:
                        session.add(GpuTask(
                            task_id=task_id, model_tier="deep", task_type="full_analysis",
                            ticker=ticker.upper(), priority=1, status=TaskStatus.queued,
                            payload={"ticker": ticker.upper()},
                        ))
                        await session.commit()

    if article_id:
        new_status = InvestigationStatus.escalated if should_add else InvestigationStatus.quick_no_action
        async with get_db_session() as session:
            from sqlalchemy import update
            await session.execute(
                update(NewsArticle)
                .where(NewsArticle.id == article_id)
                .values(status=new_status, quick_result=result)
            )
            await session.commit()


async def _handle_prune_result(data: dict):
    """Process a prune evaluation result."""
    from service.api.ws import broadcast
    from service.db.models import WatchlistTicker, WatchlistEvent
    from sqlalchemy import select, update

    result = data.get("result", {})
    ticker = data.get("ticker")
    task_id = data.get("task_id")

    # Get account_id and stage from task payload
    async with get_db_session() as session:
        task_result = await session.execute(
            select(GpuTask).where(GpuTask.task_id == task_id)
        )
        task = task_result.scalar_one_or_none()

    if not task or not task.payload:
        return

    account_id = task.payload.get("account_id")
    stage = task.payload.get("stage", "quick")
    strategy = task.payload.get("strategy", "balanced")

    if stage == "quick":
        # Quick screening: if NOT "no" → escalate to deep
        remove_answer = result.get("remove", "no").lower()
        reasoning = result.get("reasoning", "")

        if remove_answer == "no":
            # Keep — log it
            async with get_db_session() as session:
                session.add(WatchlistEvent(
                    account_id=account_id,
                    symbol=ticker,
                    action="prune_kept",
                    trigger="auto_prune",
                    reasoning=reasoning,
                ))
                await session.commit()
        else:
            # yes or maybe → submit deep confirmation
            new_task_id = await _scheduler.submit(TaskSpec(
                model_tier="deep",
                task_type="watchlist_prune",
                payload={
                    "symbol": ticker,
                    "account_id": account_id,
                    "strategy": strategy,
                    "stage": "deep",
                    "quick_reasoning": reasoning,
                    "recent_headlines": task.payload.get("recent_headlines", []),
                },
                ticker=ticker,
                priority=2,
            ))
            async with get_db_session() as session:
                db_task = GpuTask(
                    task_id=new_task_id,
                    model_tier="deep",
                    task_type="watchlist_prune",
                    ticker=ticker,
                    priority=2,
                    status=TaskStatus.queued,
                    payload={"symbol": ticker, "account_id": account_id, "stage": "deep"},
                )
                session.add(db_task)
                await session.commit()

    elif stage == "deep":
        # Deep confirmation
        should_remove = result.get("remove", False)
        reasoning = result.get("reasoning", "")

        async with get_db_session() as session:
            if should_remove:
                # Remove from watchlist
                await session.execute(
                    update(WatchlistTicker)
                    .where(
                        WatchlistTicker.account_id == account_id,
                        WatchlistTicker.symbol == ticker,
                    )
                    .values(
                        active=0,
                        removed_at=datetime.datetime.utcnow(),
                        remove_reason=reasoning,
                    )
                )
                session.add(WatchlistEvent(
                    account_id=account_id,
                    symbol=ticker,
                    action="removed",
                    trigger="auto_prune",
                    reasoning=reasoning,
                ))
                logger.info(f"Auto-pruned {ticker} from {account_id}: {reasoning}")
                await broadcast("watchlist_changed", {
                    "action": "removed",
                    "symbol": ticker,
                    "account_id": account_id,
                    "reason": reasoning,
                })
            else:
                session.add(WatchlistEvent(
                    account_id=account_id,
                    symbol=ticker,
                    action="prune_kept",
                    trigger="auto_prune",
                    reasoning=reasoning,
                ))
            await session.commit()


def _execute_emergency_sell(acct, ticker: str, reason: str) -> dict | None:
    from service.core.trade_executor import execute_emergency_sell
    sell_fraction = _config.evaluation.sell_fraction if _config else 0.5
    return execute_emergency_sell(
        api_key=acct.api_key,
        api_secret=acct.api_secret,
        is_paper=acct.is_paper,
        ticker=ticker,
        sell_fraction=sell_fraction,
        reason=reason,
    )


async def _load_report_from_disk(ticker: str) -> dict | None:
    """Load a saved analysis state from reports/_states/{ticker}.json."""
    import asyncio
    from pathlib import Path
    import json

    state_file = Path("reports") / "_states" / f"{ticker}.json"
    if not state_file.exists():
        return None
    try:
        text = await asyncio.to_thread(state_file.read_text, "utf-8")
        return json.loads(text)
    except Exception:
        return None


def _extract_decision(final_trade_decision: str) -> str:
    """Extract a Buy/Sell/Hold decision from the PM text."""
    text = final_trade_decision.lower()
    if "strong buy" in text or "rating: buy" in text:
        return "Buy"
    elif "sell" in text or "exit" in text:
        return "Sell"
    elif "overweight" in text:
        return "Overweight"
    elif "underweight" in text:
        return "Underweight"
    elif "hold" in text:
        return "Hold"
    return "Hold"


async def _on_debounce_fire(account_id: str, tickers: list[str]):
    """Called when the merge debounce timer fires for an account."""
    tickers_data = []
    for ticker in tickers:
        report = _latest_reports.get(ticker)
        if report:
            tickers_data.append({
                "ticker": ticker,
                "decision": report.get("decision", ""),
                "final_state": report.get("final_state", {}),
            })
        else:
            # Fallback: load from disk
            state = await _load_report_from_disk(ticker)
            if state:
                from tradingagents.graph.signal_processing import process_signal_stub
                decision = _extract_decision(state.get("final_trade_decision", ""))
                tickers_data.append({
                    "ticker": ticker,
                    "decision": decision,
                    "final_state": state,
                })

    if not tickers_data:
        return

    acct = _config.accounts.get(account_id) if _config else None
    portfolio = None
    if acct:
        try:
            from cli.alpaca_client import create_client, fetch_portfolio
            client = create_client(acct.api_key, acct.api_secret, paper=acct.is_paper)
            port, _, prices, _ = fetch_portfolio(client)
            portfolio = {"holdings": port.holdings, "cash": port.cash, "prices": prices}
        except Exception:
            pass

    task_id = await _scheduler.submit(TaskSpec(
        model_tier="deep",
        task_type="merge_and_allocate",
        payload={
            "account_id": account_id,
            "tickers_data": tickers_data,
            "strategy": acct.strategy if acct else "balanced",
            "portfolio": portfolio,
        },
    ))

    async with get_db_session() as session:
        db_task = GpuTask(
            task_id=task_id,
            model_tier="deep",
            task_type="merge_and_allocate",
            priority=1,
            status=TaskStatus.queued,
            payload={"account_id": account_id, "tickers": tickers},
        )
        session.add(db_task)
        await session.commit()


async def _prune_scheduler():
    """Run daily prune at 04:00 UTC for all accounts with auto_prune enabled."""
    while True:
        now = datetime.datetime.utcnow()
        target = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        logger.info(f"Prune scheduler: next run in {sleep_seconds/3600:.1f}h at {target.isoformat()}")

        await asyncio.sleep(sleep_seconds)

        if not _config:
            continue

        for account_name, acct in _config.accounts.items():
            if not acct.auto_prune:
                continue

            try:
                await _run_prune_for_account(account_name, acct)
            except Exception as e:
                logger.error(f"Prune failed for {account_name}: {e}")


async def _run_prune_for_account(account_name: str, acct):
    """Submit prune tasks for all active tickers in an account (excluding held positions)."""
    from service.db.models import WatchlistTicker
    from sqlalchemy import select

    # Get active tickers
    async with get_db_session() as session:
        result = await session.execute(
            select(WatchlistTicker.symbol).where(
                WatchlistTicker.account_id == account_name,
                WatchlistTicker.active == 1,
            )
        )
        tickers = list(result.scalars().all())

    if not tickers:
        return

    # Exclude held positions
    held_symbols = set()
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(acct.api_key, acct.api_secret, paper=acct.is_paper)
        positions = client.get_all_positions()
        held_symbols = {p.symbol for p in positions}

        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        open_orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        held_symbols.update(o.symbol for o in open_orders)
    except Exception:
        pass

    prunable = [t for t in tickers if t not in held_symbols]
    if not prunable:
        return

    logger.info(f"Prune: evaluating {len(prunable)} tickers for {account_name} (skipping {len(held_symbols)} held)")

    for symbol in prunable:
        task_id = await _scheduler.submit(TaskSpec(
            model_tier="quick",
            task_type="watchlist_prune",
            payload={
                "symbol": symbol,
                "account_id": account_name,
                "strategy": acct.strategy,
                "stage": "quick",
                "recent_headlines": [],  # TODO: fetch recent headlines
            },
            ticker=symbol,
        ))
        async with get_db_session() as session:
            db_task = GpuTask(
                task_id=task_id,
                model_tier="quick",
                task_type="watchlist_prune",
                ticker=symbol,
                priority=2,  # low priority
                status=TaskStatus.queued,
                payload={"symbol": symbol, "account_id": account_name, "stage": "quick"},
            )
            session.add(db_task)
            await session.commit()


def create_app() -> FastAPI:
    app = FastAPI(
        title="TradingAgents Continuous Evaluation",
        description="Real-time news evaluation and automated trading service",
        version="0.1.0",
        lifespan=lifespan,
    )

    from service.api.news import router as news_router
    from service.api.tasks import router as tasks_router
    from service.api.holdings import router as holdings_router
    from service.api.watchlist import router as watchlist_router
    from service.api.proposals import router as proposals_router
    from service.api.ws import router as ws_router

    app.include_router(news_router)
    app.include_router(tasks_router)
    app.include_router(holdings_router)
    app.include_router(watchlist_router)
    app.include_router(proposals_router)
    app.include_router(ws_router)

    @app.get("/")
    async def root():
        from pathlib import Path
        frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
        if frontend_dist.exists():
            from fastapi.responses import FileResponse
            return FileResponse(frontend_dist / "index.html")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/docs")

    @app.get("/api/health")
    async def health():
        import time
        from service.models.schemas import HealthResponse

        depths = await _scheduler.get_queue_depths() if _scheduler else {}
        worker = await _scheduler.get_worker_status() if _scheduler else None

        return HealthResponse(
            status="ok",
            worker_state=worker.get("state") if worker else None,
            queue_depths=depths,
            uptime_seconds=time.time() - _start_time,
        )

    @app.get("/api/logs")
    async def logs(limit: int = 1000):
        from service.log_buffer import ring_handler
        lines = ring_handler.get_lines()
        return {"lines": lines[-limit:], "total": len(lines)}

    # Serve frontend static build if it exists
    from pathlib import Path
    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        from fastapi.responses import FileResponse

        @app.get("/{path:path}", include_in_schema=False)
        async def serve_frontend(path: str):
            if path.startswith("api/") or path.startswith("docs") or path.startswith("redoc") or path.startswith("openapi") or path == "ws":
                from fastapi import HTTPException
                raise HTTPException(status_code=404)
            file = frontend_dist / path
            if file.exists() and file.is_file():
                return FileResponse(file)
            return FileResponse(frontend_dist / "index.html")

    return app
