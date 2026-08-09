"""add messages.retrieval_mode to record which retrieval mode produced an answer

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "retrieval_mode",
            sa.String(length=20),
            nullable=False,
            server_default="hybrid",
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "retrieval_mode")
