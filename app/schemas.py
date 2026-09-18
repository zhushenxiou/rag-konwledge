"""Pydantic v2 请求 / 响应模型。"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- 文档 ----
class DocumentOut(ORMModel):
    id: Any
    filename: str
    file_size: int
    source_type: str
    status: str
    chunk_count: int
    error_message: str
    created_at: datetime


class ChunkOut(ORMModel):
    id: Any
    chunk_index: int
    content: str
    metadata: dict[str, Any] | None = None
    created_at: datetime


class DocumentDetailOut(DocumentOut):
    chunks: list[ChunkOut] = []


# ---- 会话 / 消息 ----
class ConversationOut(ORMModel):
    id: Any
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(ORMModel):
    id: Any
    role: str
    content: str
    sources: list[dict[str, Any]] = []
    created_at: datetime


class RenameDocumentRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_id: Any | None = None


# ---- 登录鉴权 ----
class CaptchaOut(BaseModel):
    captcha_id: str
    image: str  # data:image/png;base64,...（明文 code 不下发）
    # 仅 AUTH_CAPTCHA_BYPASS=true 时返回；响应侧配了 exclude_none，关闭时该键整体不出现
    code: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    captcha_id: str = Field(min_length=1, max_length=128)
    captcha_code: str = Field(min_length=1, max_length=16)


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int


class MeOut(BaseModel):
    username: str
