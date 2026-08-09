"""在线重排（Rerank）Provider：阿里云 DashScope qwen3-rerank。

与 Embedding / LLM 一样是可配置的 Provider 抽象，业务代码零感知。
端点：POST {rerank_base_url}/reranks（OpenAI 兼容风格，Bearer 鉴权）。
请求 {model, query, documents[], top_n, return_documents}，
响应 {results: [{index, relevance_score}]}，relevance_score 为 0~1 的请求内相对分。
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class Reranker:
    """重排抽象：对 texts 中的候选按与 query 的相关性打分。

    rerank 返回与输入 texts 顺序对齐的分数列表（0~1），分数越高越相关。
    """

    def rerank(self, query: str, texts: list[str], top_n: int) -> list[float]:
        ...


class DashScopeReranker:
    """DashScope qwen3-rerank 在线重排实现（httpx 直连，同步调用）。"""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = base_url or settings.rerank_base_url
        self._api_key = api_key or settings.rerank_api_key
        self.model = model or settings.rerank_model_name

    def rerank(self, query: str, texts: list[str], top_n: int) -> list[float]:
        if not texts:
            return []
        try:
            resp = httpx.post(
                f"{self._base_url}/reranks",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": texts,
                    "top_n": top_n,
                    "return_documents": False,
                },
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=30.0,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("rerank api call failed: %s", exc)
            raise

        data = resp.json()
        # results 只包含 top_n 个条目；未命中 index 的分块记 0.0（排到最后被截掉）
        scores = [0.0] * len(texts)
        for item in data.get("results", []):
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(scores):
                scores[idx] = float(item.get("relevance_score", 0.0))
        return scores
