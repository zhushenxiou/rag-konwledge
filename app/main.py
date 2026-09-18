"""FastAPI 应用入口。

启动前先执行数据库迁移：
    alembic upgrade head
启动：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chat, conversations, documents, health
from app.api.deps import require_auth
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# 表结构由 Alembic 管理，启动时无需建表
app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 鉴权：逐路由显式声明，而不是全局中间件 + 路径白名单——中间件要靠"排除"哪些
# 路径才开放，日后新增开放接口容易漏改而误伤；显式挂载更直观，并由 tests/test_auth.py
# 的路由清单用例兜住"新增路由忘了挂保护"。
protected = [Depends(require_auth)]

app.include_router(auth.router, prefix="/api/auth", tags=["登录鉴权"])  # 开放：登录入口
app.include_router(health.router, prefix="/api", tags=["系统"])  # 开放：探活
app.include_router(documents.router, prefix="/api/documents", tags=["文档管理"], dependencies=protected)
app.include_router(chat.router, prefix="/api", tags=["问答"], dependencies=protected)
app.include_router(conversations.router, prefix="/api/conversations", tags=["会话管理"], dependencies=protected)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "app": settings.app_name,
        "docs": "/docs",
        "health": "/api/health",
    }
