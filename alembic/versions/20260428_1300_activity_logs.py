"""add activity_logs table for in-UI log filtering

Backs the ActivityLog model. Stores the same payload that ships to
stdout → CloudWatch, but in a queryable form so the in-UI logs tabs
(logs_app.html, logs_jobs.html, logs_sqs.html, logs_admin.html) can
paginate and filter without going to Logs Insights.

Revision ID: c8b3d57e1a40
Revises: 9f4a2b1c8e30
Create Date: 2026-04-28 13:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c8b3d57e1a40"
down_revision: Union[str, Sequence[str], None] = "9f4a2b1c8e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column(
            "level", sa.String(), server_default="info", nullable=False
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("related_id", sa.String(), nullable=True),
        sa.Column("related_type", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_activity_logs_category_created",
        "activity_logs",
        ["category", "created_at"],
    )
    op.create_index(
        "idx_activity_logs_user_created",
        "activity_logs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "idx_activity_logs_created_at",
        "activity_logs",
        ["created_at"],
    )
    op.create_index(
        op.f("ix_activity_logs_user_id"), "activity_logs", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_activity_logs_user_id"), table_name="activity_logs")
    op.drop_index("idx_activity_logs_created_at", table_name="activity_logs")
    op.drop_index("idx_activity_logs_user_created", table_name="activity_logs")
    op.drop_index("idx_activity_logs_category_created", table_name="activity_logs")
    op.drop_table("activity_logs")
