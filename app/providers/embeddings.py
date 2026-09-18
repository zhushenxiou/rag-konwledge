"""Embedding Provider：调用 OpenAI 兼容的在线 embeddings 接口（如阿里云 DashScope 千问）。

统一走 OpenAICompatEmbedder，不加载本地模型；通过 base_url / api_key / model 切换厂商。
"""
from typing import Protocol

from app.config import settings


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


# 识别"单请求条数超限"这类拒绝。各厂商措辞不同（DashScope 是
# "batch size is invalid, it should not be larger than 10"；OpenAI 是
# "maximum batch size"；部分网关是 "too many inputs"），故按关键词族匹配而非精确串。
_BATCH_LIMIT_HINTS = (
    "batch size",
    "batch_size",
    "batch limit",
    "maximum batch",
    "too many inputs",
)


def _is_batch_limit_error(exc: Exception) -> bool:
    """这个异常是否表示"一次发的条数太多"。

    只认这一类错误：分半重试对它有效；对别的原因（维度不对、鉴权失败、
    网络超时）分半只会让失败来得更慢，所以识别不了就**原样抛出**。
    """
    message = str(exc).lower()
    return any(hint in message for hint in _BATCH_LIMIT_HINTS)


class OpenAICompatEmbedder:
    """调用 OpenAI 兼容的 embeddings 接口（/embeddings）。

    单请求的文本条数上限**不写死也不配置**：各厂商不一（DashScope v3/v4 是 10、
    v1/v2 是 25、OpenAI 是 2048），写死一个数就是在赌对端；而这个限制只会在
    长文档（PDF 动辄几十个 chunk）上暴露。改为**运行时探测 + 记忆**：
    在"已知可行"与"已知不可行"之间二分，收敛后按探测值切片，进程内只付一次探测成本。
    """

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

        # 探测到的服务端上限：_ok 是"发过且成功"的最大条数，_too_big 是"发过且被
        # 条数上限拒绝"的最小条数。真实上限落在 [_ok, _too_big) 区间内。
        # 两个值都单调（只增 / 只减），所以多线程并发下最坏只是多做一次探测，无需加锁。
        self._ok = 0
        self._too_big: int | None = None

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        # 必须显式传 dimensions：千问 v3/v4 等在线模型默认输出 1024 维，
        # 而数据库 Chunk.embedding 是 Vector(EMBEDDING_DIM) 列。不传时维度
        # 与列不一致，写入 pgvector 会直接报错。设为 EMBEDDING_DIM 保证一致。
        resp = self._client.embeddings.create(
            model=self.model, input=texts, dimensions=self.dim
        )
        ordered = sorted(resp.data, key=lambda x: x.index)
        return [d.embedding for d in ordered]

    def _next_size(self, remaining: int) -> int:
        """下一次请求该发多少条：在已知可行与已知不可行之间二分。

        - 还没被拒绝过 → 直接整批发。短文档一次成功，零探测开销。
        - 已有下界和上界 → 取中点逼近，最终正好收敛到真实上限。
        - 上下界贴合 → 返回已知可行的条数，此后都是它。
        """
        if self._too_big is None:
            return remaining
        if self._too_big <= self._ok + 1:
            return self._ok  # 区间收敛，不再试探
        return (self._ok + self._too_big) // 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """分批请求，返回顺序与入参严格一一对应。

        批内靠 `_embed_batch` 的 index 排序，批间靠拼接顺序 —— 调用方
        （`document_service`）是按位置 zip 向量与 chunk 的，顺序错了就是数据错乱。
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        start = 0
        while start < len(texts):
            size = max(1, self._next_size(len(texts) - start))
            chunk = texts[start : start + size]
            try:
                vectors.extend(self._embed_batch(chunk))
            except Exception as exc:  # noqa: BLE001
                # 单条都被拒 → 不是条数问题，或对端连 1 条都不收；原样抛出，
                # 这同时是下面的重试循环的终止条件（size 已无法再收缩）。
                if len(chunk) == 1 or not _is_batch_limit_error(exc):
                    raise
                # 记录上界并**不推进 start**，按收缩后的 size 重试同一段文本
                self._too_big = (
                    len(chunk) if self._too_big is None else min(self._too_big, len(chunk))
                )
                continue
            self._ok = max(self._ok, len(chunk))
            start += len(chunk)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]
