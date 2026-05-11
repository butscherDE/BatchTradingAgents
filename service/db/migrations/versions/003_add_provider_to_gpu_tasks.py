"""Add provider column to gpu_tasks.

Revision ID: 003
Revises: 002
Create Date: 2026-05-11
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "gpu_tasks" not in inspector.get_table_names():
        return

    columns = [c["name"] for c in inspector.get_columns("gpu_tasks")]
    if "provider" in columns:
        return

    op.add_column("gpu_tasks", sa.Column("provider", sa.String(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "gpu_tasks" not in inspector.get_table_names():
        return
    columns = [c["name"] for c in inspector.get_columns("gpu_tasks")]
    if "provider" not in columns:
        return
    with op.batch_alter_table("gpu_tasks") as batch_op:
        batch_op.drop_column("provider")
