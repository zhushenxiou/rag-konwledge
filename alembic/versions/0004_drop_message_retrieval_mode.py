"""drop messages.retrieval_mode — 检索固定为混合（关键词 + 语义，RRF 融合），不再记录模式

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-09
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("messages", "retrieval_mode")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column(
        "messages",
        sa.Column(
            "retrieval_mode",
            sa.String(length=20),
            nullable=False,
            server_default="hybrid",
        ),
    )
