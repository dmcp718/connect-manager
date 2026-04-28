"""add user_settings table for per-user key/value persistence

Backs the UserSetting model (db/models.py). Stores `api_host` and any
other per-user/global setting that doesn't warrant its own table. The
composite (user_id, key) PK with a nullable user_id mirrors the
single-user-mode SQLite contract on `main`.

Revision ID: 9f4a2b1c8e30
Revises: 138ac64ad825
Create Date: 2026-04-28 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9f4a2b1c8e30"
down_revision: Union[str, Sequence[str], None] = "138ac64ad825"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
