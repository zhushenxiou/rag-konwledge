"""Embedding Provider 抽象。

- local              : sentence-transformers 本地模型（默认，离线可用）
- openai_compatible  : OpenAI 兼容 embedding 接口（如 OpenAI / DashScope 等）

通过环境变量 EMBEDDING_PROVIDER 切换，业务代码无需改动。
"""
import os
from typing import Protocol

from app.config import settings

# BGE 系列检索查询的官方推荐前缀，可提升检索效果
_BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class LocalSentenceEmbedder:
    """基于 sentence-transformers 的本地模型（BGE 系列）。

    模型获取顺序：本地目录 -> ModelScope 下载（国内稳定）-> HF（HF_ENDPOINT 镜像）。
    """

    def __init__(
        self, model_name: str | None = None, cache_dir: str | None = None
    ) -> None:
        self.model_name = model_name or settings.embedding_model
        self.cache_dir = cache_dir or settings.model_cache_dir
        self.dim = settings.embedding_dim
        self._model = None

    def _resolve_model_path(self) -> str:
        # 1) 本地已存在的目录（例如 ModelScope 已下载的快照路径）
        if os.path.isdir(self.model_name):
            return self.model_name
        # 2) 通过 ModelScope 下载（国内访问稳定）
        if settings.hf_endpoint and not os.environ.get("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = settings.hf_endpoint
        try:
            from modelscope import snapshot_download

            local = snapshot_download(self.model_name, cache_dir=self.cache_dir)
            if os.path.isdir(local):
                return local
        except Exception:  # noqa: BLE001
            pass
        # 3) 回退给 sentence-transformers 默认（配合 HF_ENDPOINT 镜像）
        return self.model_name

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._resolve_model_path())
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        if not texts:
            return []
        vectors = model.encode(
            texts,
            batch_size=16,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        model = self._ensure_model()
        query = f"{_BGE_QUERY_PREFIX}{text}"
        vector = model.encode(
            query, normalize_embeddings=True, show_progress_bar=False
        )
        return vector.tolist()


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
        self.model = model or settings.embedding_model_name or "text-embedding-3-small"
        self.dim = settings.embedding_dim

    def _embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        ordered = sorted(resp.data, key=lambda x: x.index)
        return [d.embedding for d in ordered]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]
