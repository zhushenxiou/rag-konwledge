"""Provider 工厂：根据配置返回 Embedder / LLM 实例。"""
from functools import lru_cache

from app.config import settings
from app.providers.embeddings import Embedder, LocalSentenceEmbedder, OpenAICompatEmbedder
from app.providers.llm import LLM, OpenAICompatLLM


@lru_cache
def get_embedder() -> Embedder:
    if settings.embedding_provider == "openai_compatible":
        return OpenAICompatEmbedder()
    return LocalSentenceEmbedder()


@lru_cache
def get_llm() -> LLM:
    # 目前仅实现 OpenAI 兼容接口（DeepSeek / 通义 / 本地 vLLM 均可通过 LLM_BASE_URL 切换）
    return OpenAICompatLLM()
