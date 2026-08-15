"""conversation memory — 对话记忆（滚动摘要 + 关键事实 + 消息折叠标记）

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 滚动摘要（压缩后的旧对话要点），仅后端 Prompt 使用
    op.add_column(
        "conversations",
        sa.Column("summary", sa.Text(), nullable=True),
    )
    # 关键事实（抽取出的长期信息），JSONB 数组
    op.add_column(
        "conversations",
        sa.Column(
            "key_facts",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # 已折叠进 summary 的消息标记（不参与 LLM Prompt，但仍展示）
    op.add_column(
        "messages",
        sa.Column(
            "is_folded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "is_folded")
    op.drop_column("conversations", "key_facts")
    op.drop_column("conversations", "summary")
