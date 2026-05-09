"""Add headline_hash to news_articles for cross-source deduplication.

Revision ID: 002
Revises: 001
Create Date: 2026-05-09
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "news_articles" not in inspector.get_table_names():
        return

    columns = [c["name"] for c in inspector.get_columns("news_articles")]
    if "headline_hash" in columns:
        return

    op.add_column("news_articles", sa.Column("headline_hash", sa.String(64), nullable=True))
    op.create_index("ix_news_articles_headline_hash", "news_articles", ["headline_hash"], unique=True)

    # Backfill existing rows
    import hashlib
    import re
    import unicodedata

    result = conn.execute(sa.text("SELECT id, headline FROM news_articles WHERE headline_hash IS NULL"))
    for row in result:
        text = row[1].lower()
        text = unicodedata.normalize("NFKD", text)
        text = re.sub(r"[^a-z0-9 ]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        conn.execute(
            sa.text("UPDATE news_articles SET headline_hash = :hash WHERE id = :id"),
            {"hash": h, "id": row[0]},
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "news_articles" not in inspector.get_table_names():
        return
    columns = [c["name"] for c in inspector.get_columns("news_articles")]
    if "headline_hash" not in columns:
        return
    op.drop_index("ix_news_articles_headline_hash", "news_articles")
    with op.batch_alter_table("news_articles") as batch_op:
        batch_op.drop_column("headline_hash")
