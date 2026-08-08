"""Provider 工厂：根据配置返回 Embedder / LLM 实例。"""
from functools import lru_cache

from app.providers.embeddings import Embedder, OpenAICompatEmbedder
from app.providers.llm import LLM, OpenAICompatLLM


@lru_cache
def get_embedder() -> Embedder:
    # 仅在线 OpenAI 兼容接口（千问 DashScope 等，通过 base_url / api_key / model 切换）
    return OpenAICompatEmbedder()


@lru_cache
def get_llm() -> LLM:
    # OpenAI 兼容接口（DeepSeek / 通义 / 本地 vLLM 均可通过 LLM_BASE_URL 切换）
    return OpenAICompatLLM()
