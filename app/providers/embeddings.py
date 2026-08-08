"""Embedding Provider：调用 OpenAI 兼容的在线 embeddings 接口（如阿里云 DashScope 千问）。

统一走 OpenAICompatEmbedder，不加载本地模型；通过 base_url / api_key / model 切换厂商。
"""
from typing import Protocol

from app.config import settings


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class OpenAICompatEmbedder:
    """调用 OpenAI 兼容的 embeddings 接口（/embeddings）。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            base_url=base_url or settings.embedding_base_url,
            api_key=api_key or settings.embedding_api_key,
        )
        self.model = model or settings.embedding_model_name
        self.dim = settings.embedding_dim

    def _embed(self, texts: list[str]) -> list[list[float]]:
        # 必须显式传 dimensions：千问 v3/v4 等在线模型默认输出 1024 维，
        # 而数据库 Chunk.embedding 是 Vector(EMBEDDING_DIM) 列。不传时维度
        # 与列不一致，写入 pgvector 会直接报错。设为 EMBEDDING_DIM 保证一致。
        resp = self._client.embeddings.create(
            model=self.model, input=texts, dimensions=self.dim
        )
        ordered = sorted(resp.data, key=lambda x: x.index)
        return [d.embedding for d in ordered]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]
