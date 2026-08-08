"""文档管理 API：上传 / 列表 / 详情 / 删除 / 失败重试。"""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.models import Chunk, Document
from app.schemas import (
    ChunkOut,
    DocumentDetailOut,
    DocumentOut,
    RenameDocumentRequest,
)
from app.services.document_service import ingest_document

logger = logging.getLogger(__name__)
router = APIRouter()


def _source_type(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _run_ingest(doc_id: uuid.UUID) -> None:
    """后台任务包装：用独立会话执行入库（HTTP 请求结束后原会话已关闭）。"""
    db = SessionLocal()
    try:
        ingest_document(db, doc_id)
    finally:
        db.close()


@router.post("/upload", response_model=DocumentOut, status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    filename = file.filename or "unnamed"
    ext = _source_type(filename)
    if ext not in settings.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext or '未知'}，仅支持 {'/'.join(sorted(settings.allowed_extensions))}",
        )

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4()
    dest = upload_dir / f"{file_id}.{ext}"

    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_file_size_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件大小超过限制（最大 {settings.max_file_size_mb}MB）",
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    doc = Document(
        filename=filename,
        file_path=str(dest),
        file_size=size,
        source_type=ext,
        status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    background_tasks.add_task(_run_ingest, doc.id)
    logger.info("document uploaded: %s (%s), scheduled ingest", filename, doc.id)
    return doc


@router.get("", response_model=list[DocumentOut])
def list_documents(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Document).order_by(Document.created_at.desc())
    if status:
        stmt = stmt.where(Document.status == status)
    return db.execute(stmt).scalars().all()


@router.get("/{doc_id}", response_model=DocumentDetailOut)
def get_document(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks = (
        db.execute(
            select(Chunk)
            .where(Chunk.document_id == doc_id)
            .order_by(Chunk.chunk_index.asc())
        )
        .scalars()
        .all()
    )
    # 手动组装，避免把 Pydantic 对象塞进 ORM relationship
    return DocumentDetailOut(
        id=doc.id,
        filename=doc.filename,
        file_size=doc.file_size,
        source_type=doc.source_type,
        status=doc.status,
        chunk_count=doc.chunk_count,
        error_message=doc.error_message,
        created_at=doc.created_at,
        chunks=[
            ChunkOut(
                id=c.id,
                chunk_index=c.chunk_index,
                content=c.content,
                metadata=c.chunk_metadata,
                created_at=c.created_at,
            )
            for c in chunks
        ],
    )


@router.patch("/{doc_id}", response_model=DocumentOut)
def rename_document(
    doc_id: uuid.UUID,
    req: RenameDocumentRequest,
    db: Session = Depends(get_db),
):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    new_name = req.filename.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    doc.filename = new_name
    db.commit()
    db.refresh(doc)
    logger.info("document renamed: %s (%s)", req.filename, doc_id)
    return doc


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 数据库层 ON DELETE CASCADE 会级联删除 chunks 与向量
    db.delete(doc)
    db.commit()
    try:
        Path(doc.file_path).unlink(missing_ok=True)
    except OSError:  # noqa: BLE001
        logger.warning("delete file failed: %s", doc.file_path)
    logger.info("document deleted: %s (%s)", doc.filename, doc_id)


@router.post("/{doc_id}/retry", response_model=DocumentOut)
def retry_document(
    doc_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status not in ("failed", "ready"):
        raise HTTPException(status_code=409, detail=f"当前状态（{doc.status}）不可重试")

    # 重试前清理旧分块，避免重复
    db.execute(Chunk.__table__.delete().where(Chunk.document_id == doc_id))
    doc.status = "pending"
    doc.chunk_count = 0
    doc.error_message = ""
    db.commit()
    db.refresh(doc)

    background_tasks.add_task(_run_ingest, doc.id)
    return doc
