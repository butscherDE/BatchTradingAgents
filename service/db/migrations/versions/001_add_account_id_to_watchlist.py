"""Add account_id to watchlist_tickers for per-account watchlists.

Revision ID: 001
Revises: None
Create Date: 2026-05-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # If table doesn't exist, skip — create_all will handle it
    if "watchlist_tickers" not in inspector.get_table_names():
        return

    # If account_id already exists, skip
    columns = [c["name"] for c in inspector.get_columns("watchlist_tickers")]
    if "account_id" in columns:
        return

    # Add column with ALTER TABLE (simpler, avoids batch recreate issues)
    op.add_column("watchlist_tickers", sa.Column("account_id", sa.String(), nullable=False, server_default="paper_main"))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "watchlist_tickers" not in inspector.get_table_names():
        return
    columns = [c["name"] for c in inspector.get_columns("watchlist_tickers")]
    if "account_id" not in columns:
        return
    with op.batch_alter_table("watchlist_tickers") as batch_op:
        batch_op.drop_column("account_id")
