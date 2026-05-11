"""CLI tools for backfilling news and replaying the evaluation pipeline."""

import asyncio
import datetime
import sys
from pathlib import Path


def backfill_news(since_date: str | None = None):
    """Pull recent news from yfinance for all watchlist tickers and insert into DB.

    Usage:
        python -m service.tools backfill [--since 2026-05-07]
    """
    asyncio.run(_backfill(since_date))


async def _backfill(since_date: str | None):
    import yfinance as yf
    from service.config import load_config
    from service.db.engine import get_async_engine, get_async_session_factory, init_db
    from service.db.models import NewsArticle, InvestigationStatus, WatchlistTicker
    from sqlalchemy import select

    config = load_config()
    engine = get_async_engine(config.database_path)
    await init_db(engine)
    session_factory = get_async_session_factory(engine)

    # Get watchlist tickers
    async with session_factory() as session:
        result = await session.execute(
            select(WatchlistTicker.symbol).where(WatchlistTicker.active == 1)
        )
        tickers = [r for r in result.scalars().all()]

    if not tickers:
        print("No tickers on watchlist. Add tickers first.")
        return

    print(f"Backfilling news for {len(tickers)} tickers: {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}")

    total_inserted = 0
    total_skipped = 0

    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            news = stock.get_news(count=20)
            if not news:
                continue

            for article in news:
                # Extract article data
                if "content" in article:
                    content = article["content"]
                    headline = content.get("title", "")
                    summary = content.get("summary", "")
                    provider = content.get("provider", {})
                    source = provider.get("displayName", "yfinance")
                    pub_date_str = content.get("pubDate", "")
                    pub_date = None
                    if pub_date_str:
                        try:
                            pub_date = datetime.datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                        except (ValueError, AttributeError):
                            pass

                    # Get related tickers
                    symbols = [ticker]
                    for t in content.get("finance", {}).get("stockTickers", []):
                        sym = t.get("symbol", "")
                        if sym and sym not in symbols:
                            symbols.append(sym)
                else:
                    headline = article.get("title", "")
                    summary = article.get("summary", "")
                    source = article.get("publisher", "yfinance")
                    pub_date = None
                    symbols = [ticker]

                if not headline:
                    continue

                # Filter by date if specified
                if since_date and pub_date:
                    since_dt = datetime.datetime.fromisoformat(since_date).replace(tzinfo=datetime.timezone.utc)
                    if pub_date.replace(tzinfo=datetime.timezone.utc) < since_dt:
                        continue

                # Deduplicate: check by headline + first symbol
                async with session_factory() as session:
                    existing = await session.execute(
                        select(NewsArticle).where(
                            NewsArticle.headline == headline,
                            NewsArticle.symbols.contains(ticker),
                        )
                    )
                    if existing.scalar_one_or_none():
                        total_skipped += 1
                        continue

                    db_article = NewsArticle(
                        headline=headline,
                        summary=summary or None,
                        source=source,
                        symbols=symbols,
                        published_at=pub_date.replace(tzinfo=None) if pub_date else None,
                        received_at=datetime.datetime.utcnow(),
                        status=InvestigationStatus.queued,
                    )
                    session.add(db_article)
                    await session.commit()
                    total_inserted += 1

        except Exception as e:
            print(f"  {ticker}: error — {e}")

    print(f"Done. Inserted: {total_inserted}, Skipped (duplicates): {total_skipped}")
    await engine.dispose()


def replay(since: str):
    """Replay all queued news articles since a given datetime through the GPU pipeline.

    Usage:
        python -m service.tools replay --since "2026-05-08T10:00:00"

    This resubmits all articles with status 'queued' (created after `since`) to the GPU
    quick-screen queue, as if they had just arrived from the news stream.
    """
    asyncio.run(_replay(since))


async def _replay(since: str):
    import redis.asyncio as aioredis
    import json
    import uuid
    from service.config import load_config
    from service.db.engine import get_async_engine, get_async_session_factory, init_db
    from service.db.models import NewsArticle, GpuTask, InvestigationStatus, TaskStatus
    from service.core.gpu_scheduler import GpuScheduler, TaskSpec
    from sqlalchemy import select, update

    config = load_config()
    engine = get_async_engine(config.database_path)
    await init_db(engine)
    session_factory = get_async_session_factory(engine)

    since_dt = datetime.datetime.fromisoformat(since)

    # Find all articles to replay
    async with session_factory() as session:
        result = await session.execute(
            select(NewsArticle)
            .where(NewsArticle.received_at >= since_dt)
            .order_by(NewsArticle.received_at.asc())
        )
        articles = result.scalars().all()

    if not articles:
        print(f"No articles found since {since}")
        await engine.dispose()
        return

    print(f"Found {len(articles)} articles to replay since {since}")

    # Reset their status to queued
    async with session_factory() as session:
        article_ids = [a.id for a in articles]
        await session.execute(
            update(NewsArticle)
            .where(NewsArticle.id.in_(article_ids))
            .values(status=InvestigationStatus.queued, quick_result=None, deep_result=None, escalation_reason=None)
        )
        await session.commit()

    # Submit to GPU queue via Redis
    scheduler = GpuScheduler(config.redis_url)
    await scheduler.connect()

    submitted = 0
    for article in articles:
        ticker = article.symbols[0] if article.symbols else None
        task_id = await scheduler.submit(TaskSpec(
            model_tier="quick",
            task_type="news_screen",
            payload={
                "article_id": article.id,
                "headline": article.headline,
                "summary": article.summary or "",
                "symbols": article.symbols or [],
            },
            ticker=ticker,
        ))

        # Record task in DB
        async with session_factory() as session:
            db_task = GpuTask(
                task_id=task_id,
                model_tier="quick",
                task_type="news_screen",
                ticker=ticker,
                priority=1,
                status=TaskStatus.queued,
                payload={"article_id": article.id},
            )
            session.add(db_task)
            await session.commit()

        submitted += 1

    await scheduler.close()
    await engine.dispose()
    print(f"Submitted {submitted} articles to GPU queue for processing.")
    print("Make sure the service is running (python -m service.main) to process them.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Service tools")
    sub = parser.add_subparsers(dest="command")

    backfill_cmd = sub.add_parser("backfill", help="Pull news from yfinance for watchlist tickers")
    backfill_cmd.add_argument("--since", help="Only import news published after this date (YYYY-MM-DD)", default=None)

    replay_cmd = sub.add_parser("replay", help="Replay articles through GPU pipeline")
    replay_cmd.add_argument("--since", required=True, help="Replay articles received after this datetime (ISO format)")

    args = parser.parse_args()

    if args.command == "backfill":
        backfill_news(args.since)
    elif args.command == "replay":
        replay(args.since)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
