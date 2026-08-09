"""pytest 全局配置。

- 必须在导入任何 app 模块之前设置环境变量（settings 为进程内缓存）。
- 测试使用独立的 rag_kb_test 库，不影响开发库。
- Embedding / LLM 全部注入 Fake 实现，不触网、不下载模型。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "postgresql+psycopg2://postgres:123456@localhost:5432/rag_kb_test"
os.environ["TEST_DATABASE_URL"] = os.environ["DATABASE_URL"]
os.environ["LLM_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://example.invalid/v1"
os.environ["EMBEDDING_DIM"] = "8"  # 与 FakeEmbedder 维度一致
# Rerank 默认开启，测试通过 fake_reranker 注入，不触网
os.environ["RERANK_ENABLED"] = "true"
os.environ["RERANK_API_KEY"] = "test-key"
os.environ["RERANK_BASE_URL"] = "https://example.invalid/v1"

import pytest  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base, engine  # noqa: E402
import app.models  # noqa: E402,F401  注册模型


@pytest.fixture()
def db():
    """每个测试使用干净的表结构。"""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


class FakeEmbedder:
    """确定性向量：把字符散列到维度桶，用于测试检索相似性。"""

    dim = 8

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for ch in text:
            v[ord(ch) % self.dim] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [round(x / norm, 6) for x in v]


class FakeLLM:
    """按 token 列表流式返回的假 LLM。"""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens

    async def stream_chat(self, messages, temperature=None):
        for token in self.tokens:
            yield token


class FakeReranker:
    """假重排：默认按输入顺序给递减分数（保持原序）；传 scores 可模拟指定重排。

    scores 与输入 texts 顺序对齐（即与召回顺序对齐），供重排用例断言排序变化。
    """

    def __init__(self, scores: list[float] | None = None) -> None:
        self._scores = scores

    def rerank(self, query: str, texts: list[str], top_n: int) -> list[float]:
        if self._scores is not None:
            return self._scores[: len(texts)]
        return [round(0.95 - i * 0.05, 4) for i in range(len(texts))]


@pytest.fixture()
def fake_embedder():
    return FakeEmbedder()


@pytest.fixture()
def fake_llm():
    return FakeLLM(["mock-answer-", "part2"])


@pytest.fixture()
def fake_reranker():
    return FakeReranker()
