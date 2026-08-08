"""文档入库链路：解析 -> 分块 -> 向量化 -> 写 pgvector。

状态机：pending -> processing -> ready | failed
失败后可通过 POST /api/documents/{id}/retry 重新入库。
"""
import logging
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Chunk, Document
from app.providers import get_embedder
from app.providers.embeddings import Embedder
from app.services.chunking import chunk_sections
from app.services.parsing import parse_file

logger = logging.getLogger(__name__)


def ingest_document(db: Session, doc_id: uuid.UUID, embedder: Embedder | None = None) -> None:
    """在后台线程中执行完整入库流程。

    注意：调用方需传入独立于 HTTP 请求的数据库会话（请求结束后会话会关闭）。
    """
    embedder = embedder or get_embedder()
    doc = db.get(Document, doc_id)
    if doc is None:
        logger.warning("document %s not found, skip ingest", doc_id)
        return

    doc.status = "processing"
    doc.error_message = ""
    db.commit()

    try:
        path = Path(doc.file_path)
        sections = parse_file(path, doc.source_type)
        pieces = chunk_sections(sections, settings.chunk_size, settings.overlap)
        if not pieces:
            raise ValueError("解析后没有可用的文本内容")

        vectors = embedder.embed_documents([p.content for p in pieces])
        for idx, (piece, vec) in enumerate(zip(pieces, vectors)):
            db.add(
                Chunk(
                    document_id=doc.id,
                    content=piece.content,
                    chunk_index=idx,
                    chunk_metadata=piece.metadata,
                    embedding=vec,
                )
            )
        doc.chunk_count = len(pieces)
        doc.status = "ready"
        db.commit()
        logger.info("document %s ingested: %d chunks (%s)", doc.filename, len(pieces), doc.id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        doc.status = "failed"
        doc.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        db.commit()
        logger.exception("ingest document %s (%s) failed", doc.id, doc.filename)
