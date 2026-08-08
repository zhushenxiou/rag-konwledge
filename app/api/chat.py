"""问答 API：POST /api/chat，SSE 流式返回回答与来源。"""
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.database import SessionLocal
from app.schemas import ChatRequest
from app.services.chat_service import chat_events

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat")
async def chat(req: ChatRequest):
    # 会话由生成器内部管理：StreamingResponse 的 body 在依赖关闭后才消费，
    # 不能复用请求级 get_db 会话，否则会提前关闭。
    async def event_stream():
        db = SessionLocal()
        try:
            async for ev in chat_events(db, req.question, req.conversation_id):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat stream error")
            err = {"type": "error", "message": f"服务异常: {exc}"}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
