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
    with op.batch_alter_table("watchlist_tickers") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(), nullable=False, server_default="paper_main"))
        batch_op.drop_constraint("uq_watchlist_tickers_symbol", type_="unique")
        batch_op.create_unique_constraint("uq_watchlist_account_symbol", ["account_id", "symbol"])
        batch_op.create_index("ix_watchlist_tickers_account_id", ["account_id"])


def downgrade() -> None:
    with op.batch_alter_table("watchlist_tickers") as batch_op:
        batch_op.drop_index("ix_watchlist_tickers_account_id")
        batch_op.drop_constraint("uq_watchlist_account_symbol", type_="unique")
        batch_op.create_unique_constraint("uq_watchlist_tickers_symbol", ["symbol"])
        batch_op.drop_column("account_id")
