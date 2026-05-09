"""Database-backed news data — reads from the service's SQLite news_articles table."""

import sqlite3
from datetime import datetime, timedelta

from .config import get_config


def _get_db_path() -> str:
    config = get_config()
    return config.get("database_path", "./data/service.db")


def get_database_news(ticker: str, start_date: str, end_date: str) -> str:
    """Fetch ticker-specific news from the service database."""
    db_path = _get_db_path()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_inclusive = end_dt.strftime("%Y-%m-%d")

        cursor.execute("""
            SELECT headline, summary, source, published_at, received_at
            FROM news_articles
            WHERE symbols LIKE ?
            AND (
                (published_at IS NOT NULL AND published_at >= ? AND published_at < ?)
                OR (published_at IS NULL AND received_at >= ? AND received_at < ?)
            )
            ORDER BY COALESCE(published_at, received_at) DESC
        """, (f'%"{ticker}"%', start_date, end_inclusive, start_date, end_inclusive))

        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        return f"Error fetching news for {ticker} from database: {e}"

    if not rows:
        return f"No news found for {ticker} between {start_date} and {end_date}"

    news_str = ""
    for headline, summary, source, published_at, received_at in rows:
        news_str += f"### {headline} (source: {source or 'unknown'})\n"
        if summary:
            news_str += f"{summary}\n"
        news_str += "\n"

    return f"## {ticker} News, from {start_date} to {end_date}:\n\n{news_str}"


def get_global_news_database(curr_date: str, look_back_days: int = 7, limit: int = 10) -> str:
    """Fetch global/market news from the service database."""
    db_path = _get_db_path()
    try:
        start_dt = datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)
        end_dt = datetime.strptime(curr_date, "%Y-%m-%d") + timedelta(days=1)
        start_date = start_dt.strftime("%Y-%m-%d")
        end_inclusive = end_dt.strftime("%Y-%m-%d")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT headline, summary, source, published_at, received_at
            FROM news_articles
            WHERE (
                (published_at IS NOT NULL AND published_at >= ? AND published_at < ?)
                OR (published_at IS NULL AND received_at >= ? AND received_at < ?)
            )
            ORDER BY COALESCE(published_at, received_at) DESC
            LIMIT ?
        """, (start_date, end_inclusive, start_date, end_inclusive, limit))

        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        return f"Error fetching global news from database: {e}"

    if not rows:
        return f"No global news found for {curr_date}"

    news_str = ""
    for headline, summary, source, published_at, received_at in rows:
        news_str += f"### {headline} (source: {source or 'unknown'})\n"
        if summary:
            news_str += f"{summary}\n"
        news_str += "\n"

    return f"## Global Market News, from {start_date} to {curr_date}:\n\n{news_str}"
