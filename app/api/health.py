"""健康检查。"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_status = "error"
    return {
        "status": "ok",
        "database": db_status,
        "app": settings.app_name,
        "embedding_model": settings.embedding_model_name,
        "embedding_base_url": settings.embedding_base_url,
        "llm_model": settings.llm_model,
    }
