"""Extend trade_actions with proposal_id, fill columns, and backfill from approved proposals.

Revision ID: 004
Revises: 003
Create Date: 2026-05-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import json
import datetime

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = [
    ("notional", sa.Column("notional", sa.Float(), nullable=True)),
    ("trigger", sa.Column("trigger", sa.String(), nullable=False, server_default="manual")),
    ("proposal_id", sa.Column("proposal_id", sa.Integer(), nullable=True)),
    ("error", sa.Column("error", sa.Text(), nullable=True)),
    ("filled_qty", sa.Column("filled_qty", sa.Float(), nullable=True)),
    ("filled_avg_price", sa.Column("filled_avg_price", sa.Float(), nullable=True)),
    ("filled_at", sa.Column("filled_at", sa.DateTime(), nullable=True)),
    ("last_synced_at", sa.Column("last_synced_at", sa.DateTime(), nullable=True)),
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "trade_actions" not in inspector.get_table_names():
        return

    existing = {c["name"] for c in inspector.get_columns("trade_actions")}
    for name, col in _NEW_COLUMNS:
        if name not in existing:
            op.add_column("trade_actions", col)

    indexes = {idx["name"] for idx in inspector.get_indexes("trade_actions")}
    if "ix_trade_actions_account_id" not in indexes:
        op.create_index("ix_trade_actions_account_id", "trade_actions", ["account_id"])
    if "ix_trade_actions_ticker" not in indexes:
        op.create_index("ix_trade_actions_ticker", "trade_actions", ["ticker"])
    if "ix_trade_actions_proposal_id" not in indexes:
        op.create_index("ix_trade_actions_proposal_id", "trade_actions", ["proposal_id"])

    # Backfill from approved proposals' execution_results
    if "trade_proposals" not in inspector.get_table_names():
        return

    rows = conn.execute(sa.text(
        "SELECT id, account_id, proposed_orders, execution_results, decided_at "
        "FROM trade_proposals "
        "WHERE status = 'approved' AND execution_results IS NOT NULL"
    )).fetchall()

    for row in rows:
        proposal_id, account_id, proposed_orders, execution_results, decided_at = row

        try:
            er = json.loads(execution_results) if isinstance(execution_results, str) else execution_results
        except Exception:
            continue
        try:
            po = json.loads(proposed_orders) if isinstance(proposed_orders, str) else (proposed_orders or [])
        except Exception:
            po = []

        if not er:
            continue

        side_by_ticker = {o["ticker"]: o.get("side") for o in (po or []) if o.get("ticker")}
        qty_by_ticker = {o["ticker"]: o.get("qty") for o in (po or []) if o.get("ticker")}
        notional_by_ticker = {o["ticker"]: o.get("notional") for o in (po or []) if o.get("ticker")}

        submitted_at = decided_at or datetime.datetime.utcnow()

        for entry in er:
            ticker = entry.get("ticker")
            if not ticker:
                continue
            side = entry.get("side") or side_by_ticker.get(ticker) or "buy"
            qty = entry.get("qty") if entry.get("qty") is not None else qty_by_ticker.get(ticker)
            notional = entry.get("notional") if entry.get("notional") is not None else notional_by_ticker.get(ticker)
            err = entry.get("error")
            status = entry.get("status") or ("failed" if err else "submitted")

            conn.execute(sa.text(
                "INSERT INTO trade_actions ("
                "  account_id, ticker, action, qty, notional, trigger, "
                "  trigger_reason, proposal_id, order_id, status, error, submitted_at"
                ") VALUES ("
                "  :account_id, :ticker, :action, :qty, :notional, :trigger, "
                "  :trigger_reason, :proposal_id, :order_id, :status, :error, :submitted_at"
                ")"
            ), {
                "account_id": account_id,
                "ticker": ticker,
                "action": side,
                "qty": qty,
                "notional": notional,
                "trigger": "proposal",
                "trigger_reason": None,
                "proposal_id": proposal_id,
                "order_id": entry.get("order_id"),
                "status": status,
                "error": err,
                "submitted_at": submitted_at,
            })


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "trade_actions" not in inspector.get_table_names():
        return

    columns = {c["name"] for c in inspector.get_columns("trade_actions")}
    indexes = {idx["name"] for idx in inspector.get_indexes("trade_actions")}

    for idx_name in ("ix_trade_actions_proposal_id", "ix_trade_actions_ticker", "ix_trade_actions_account_id"):
        if idx_name in indexes:
            op.drop_index(idx_name, table_name="trade_actions")

    with op.batch_alter_table("trade_actions") as batch_op:
        for col, _ in reversed(_NEW_COLUMNS):
            if col in columns:
                batch_op.drop_column(col)
