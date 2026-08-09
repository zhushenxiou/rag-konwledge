"""add HNSW index on chunks.embedding for cosine search

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-08
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # HNSW 近似最近邻索引（余弦距离），替代全表线性扫描。
    # 向量列此前无索引，文档量上来后语义检索会退化为全表扫描。
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
        "ON chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
