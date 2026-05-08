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


def get_scheduler() -> GpuScheduler:
    assert _scheduler is not None
    return _scheduler


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    assert _session_factory is not None
    async with _session_factory() as session:
        yield session


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
    await init_db(engine)
    _session_factory = get_async_session_factory(engine)

    # Redis / GPU scheduler
    _scheduler = GpuScheduler(_config.redis_url)
    try:
        await _scheduler.connect()
    except Exception as e:
        logger.warning(f"Redis connection failed (tasks will not process): {e}")

    # News + price streams
    stream_manager = StreamManager(
        _config,
        on_news=_handle_news_article,
        on_price_bar=_handle_price_bar,
    )
    try:
        await stream_manager.start()
    except Exception as e:
        logger.warning(f"News stream failed to start: {e}")

    # Result listener
    result_task = None
    try:
        result_task = asyncio.create_task(_listen_for_results())
    except Exception as e:
        logger.warning(f"Result listener failed to start: {e}")

    logger.info(f"Service started on {_config.host}:{_config.port}")
    yield

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

    async with get_db_session() as session:
        db_article = NewsArticle(
            alpaca_id=article.get("alpaca_id"),
            headline=article["headline"],
            summary=article.get("summary"),
            source=article.get("source"),
            symbols=article.get("symbols", []),
            published_at=(
                datetime.datetime.fromisoformat(article["published_at"])
                if article.get("published_at") else None
            ),
            received_at=datetime.datetime.utcnow(),
            status=InvestigationStatus.queued,
        )
        session.add(db_article)
        await session.commit()
        await session.refresh(db_article)
        article_id = db_article.id

    # Submit quick-screen task
    task_id = await _scheduler.submit(TaskSpec(
        model_tier="quick",
        task_type="news_screen",
        payload={
            "article_id": article_id,
            "headline": article["headline"],
            "summary": article.get("summary", ""),
            "symbols": article.get("symbols", []),
        },
        ticker=article.get("symbols", [None])[0] if article.get("symbols") else None,
    ))

    # Record task in DB
    async with get_db_session() as session:
        db_task = GpuTask(
            task_id=task_id,
            model_tier="quick",
            task_type="news_screen",
            ticker=article.get("symbols", [None])[0] if article.get("symbols") else None,
            priority=1,
            status=TaskStatus.queued,
            payload={"article_id": article_id},
        )
        session.add(db_task)
        await session.commit()

    await broadcast("news_added", {
        "id": article_id,
        "headline": article["headline"],
        "symbols": article.get("symbols", []),
        "status": "queued",
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
    """Process a completed merge+allocation."""
    from service.api.ws import broadcast

    result = data.get("result", {})
    account_id = result.get("account_id")
    merge_report = result.get("merge_report", "")
    tickers = result.get("tickers", [])

    await broadcast("merge_complete", {
        "account_id": account_id,
        "tickers": tickers,
        "report_length": len(merge_report),
    })


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

    if not tickers_data:
        return

    acct = _config.accounts.get(account_id) if _config else None
    portfolio = None
    if acct:
        try:
            from cli.alpaca_client import create_client, fetch_portfolio
            client = create_client(acct.api_key, acct.api_secret, paper=acct.is_paper)
            port, _, _, _ = fetch_portfolio(client)
            portfolio = {"holdings": port.holdings, "cash": port.cash}
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
    from service.api.ws import router as ws_router

    app.include_router(news_router)
    app.include_router(tasks_router)
    app.include_router(holdings_router)
    app.include_router(ws_router)

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

    return app
