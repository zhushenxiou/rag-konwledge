"""入库链路测试：解析 -> 分块 -> 向量化 -> 状态机。"""
from sqlalchemy import select

from app.models import Chunk, Document
from app.services.document_service import ingest_document


def test_ingest_markdown_to_ready(tmp_path, db, fake_embedder):
    md = tmp_path / "a.md"
    md.write_text(
        "# 系统介绍\n这是第一段内容，介绍系统架构与选型。\n\n"
        "这是第二段，说明分块与检索策略。\n\n这是第三段，说明问答流程。",
        encoding="utf-8",
    )
    doc = Document(
        filename="a.md", file_path=str(md), file_size=md.stat().st_size, source_type="md"
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    ingest_document(db, doc.id, embedder=fake_embedder)

    assert doc.status == "ready"
    assert doc.chunk_count >= 1
    chunks = (
        db.execute(select(Chunk).where(Chunk.document_id == doc.id)).scalars().all()
    )
    assert len(chunks) == doc.chunk_count
    assert len(chunks[0].embedding) > 0  # 已写入向量


def test_ingest_corrupt_pdf_marks_failed(tmp_path, db, fake_embedder):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7 this is not a real pdf content")
    doc = Document(
        filename="bad.pdf", file_path=str(bad), file_size=bad.stat().st_size, source_type="pdf"
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    ingest_document(db, doc.id, embedder=fake_embedder)

    assert doc.status == "failed"
    assert doc.error_message
