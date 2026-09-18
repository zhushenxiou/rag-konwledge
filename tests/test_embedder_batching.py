"""Embedding 自适应分批：单请求条数上限靠**运行时探测**，不写死也不配置。

回归背景：`embed_documents` 曾把所有 chunk 一次性塞进一个请求，DashScope 对
**单请求条数**有硬上限（v3/v4 都是 10，与 token 多少无关），超了直接 400：

    InternalError.Algo.InvalidParameter: Value error, batch size is invalid,
    it should not be larger than 10.: input.contents

于是短文档（≤10 chunk）正常、PDF（几十上百 chunk）必炸。
现在改成在"已知可行 / 已知不可行"之间二分，收敛后按探测值切片。

不触网：构造真实的 OpenAICompatEmbedder（构造函数不发请求），再把内部 `_client`
换成假 client —— 假 client 会在超限时抛出**真实的 400 原文**，所以测的是真语义。
"""
from types import SimpleNamespace

import pytest

from app.providers.embeddings import OpenAICompatEmbedder

# DashScope 的真实报错原文（截取），假 client 用它当越界时的异常
_BATCH_ERROR = (
    "Error code: 400 - {'error': {'message': '<400> InternalError.Algo.InvalidParameter: "
    "Value error, batch size is invalid, it should not be larger than 10.: input.contents'}}"
)
# 另一类 400：与条数无关，**不该**被当成条数超限去重试
_OTHER_ERROR = (
    "Error code: 400 - {'error': {'message': 'InvalidParameter: dimensions is invalid'}}"
)


class _FakeEmbeddings:
    """按 max_batch 模拟服务端条数上限。

    区分两类记录，因为它们的期望完全不同：
      - `attempts`：**全部**请求，含被拒的试探 —— 探测阶段必然有若干次越界，是预期行为
      - `served`：真正取到数的请求 —— 这些**必须**条条不越界，是硬不变量

    向量值取自文本编号，便于校验跨批顺序。
    """

    def __init__(self, max_batch: int, error: str = _BATCH_ERROR) -> None:
        self.max_batch = max_batch
        self.error = error
        self.attempts: list[list[str]] = []
        self.served: list[list[str]] = []

    def create(self, *, model, input, dimensions):
        self.attempts.append(list(input))
        if len(input) > self.max_batch:
            raise ValueError(self.error)  # 模拟服务端拒绝
        self.served.append(list(input))
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=[float(text[1:])] * dimensions)
                for i, text in enumerate(input)
            ]
        )


def _embedder(max_batch: int = 10, error: str = _BATCH_ERROR):
    emb = OpenAICompatEmbedder(
        base_url="http://example.invalid/v1", api_key="test", model="fake-embedding"
    )
    fake = _FakeEmbeddings(max_batch, error)
    emb._client = SimpleNamespace(embeddings=fake)
    return emb, fake


def _texts(n: int) -> list[str]:
    return [f"t{i}" for i in range(n)]


# ---- 核心不变量：取到数的请求一律不越界 ----
def test_large_document_is_split_within_provider_cap():
    emb, fake = _embedder(max_batch=10)
    vectors = emb.embed_documents(_texts(200))

    assert len(vectors) == 200
    assert all(len(c) <= 10 for c in fake.served), "实际取数的请求越过了服务端上限"


def test_result_order_matches_input_across_batches():
    """跨批拼接必须保持全局顺序 —— 调用方按位置 zip 向量与 chunk，错位即数据错乱。"""
    emb, _ = _embedder(max_batch=10)
    vectors = emb.embed_documents(_texts(57))

    assert [v[0] for v in vectors] == [float(i) for i in range(57)]


# ---- 关键：探测要收敛到真实上限，而不是"减半到差不多" ----
def test_converges_to_exact_cap_and_reuses_it():
    emb, fake = _embedder(max_batch=10)
    emb.embed_documents(_texts(200))

    assert emb._ok == 10, f"应收敛到真实上限 10，实际 {emb._ok}"
    # 收敛后每批都是 10 条（末尾那批是余数）
    assert [len(c) for c in fake.served[-6:-1]] == [10] * 5, "收敛后每批都该是 10 条"
    # 200 条文本 ≈ 20 批就能取完；远超这个数说明收敛后还在碎切
    assert len(fake.served) <= 22, f"取数批次过多（{len(fake.served)}），未按收敛值切片"


def test_probe_cost_is_logarithmic_not_linear():
    """探测成本必须是对数级的：200 条文本不该产生几十次失败重试。"""
    emb, fake = _embedder(max_batch=10)
    emb.embed_documents(_texts(200))

    rejected = [c for c in fake.attempts if len(c) > 10]
    assert len(rejected) <= 10, f"试探请求过多（{len(rejected)} 次），说明没在二分"


def test_second_call_pays_no_probe_cost():
    """探测结果被记住：第二次入库直接按收敛值切片，不该再有失败请求。"""
    emb, fake = _embedder(max_batch=10)
    emb.embed_documents(_texts(50))
    fake.attempts.clear()
    fake.served.clear()

    emb.embed_documents(_texts(50))

    assert [len(c) for c in fake.served] == [10] * 5
    assert fake.attempts == fake.served, "第二次调用不该再有被拒的试探"


# ---- 短文档：零探测开销 ----
def test_small_document_needs_exactly_one_request():
    emb, fake = _embedder(max_batch=10)
    emb.embed_documents(_texts(4))

    assert [len(c) for c in fake.attempts] == [4]


def test_small_document_first_does_not_cap_later_batches():
    """先入库一个小文档不该把批大小永久锁在小值上 —— 上界未知时仍按整批试探。"""
    emb, fake = _embedder(max_batch=10)
    emb.embed_documents(_texts(3))
    fake.attempts.clear()
    fake.served.clear()

    emb.embed_documents(_texts(100))

    assert emb._ok == 10
    assert all(len(c) <= 10 for c in fake.served)


# ---- 边界 ----
def test_empty_input_makes_no_request():
    emb, fake = _embedder()
    assert emb.embed_documents([]) == []
    assert fake.attempts == []


def test_embed_query_still_sends_one_text():
    emb, fake = _embedder()
    vec = emb.embed_query("t3")

    assert fake.attempts == [["t3"]]
    assert vec[0] == 3.0


def test_cap_of_one_terminates():
    """服务端只收 1 条时也必须收敛，不能死循环。"""
    emb, fake = _embedder(max_batch=1)
    vectors = emb.embed_documents(_texts(3))

    assert len(vectors) == 3
    assert emb._ok == 1
    assert [len(c) for c in fake.served] == [1, 1, 1]


def test_unrecognized_error_is_raised_untouched():
    """与条数无关的 400 必须原样抛出：分半重试对它没用，只会让失败来得更慢。"""
    emb, fake = _embedder(max_batch=10, error=_OTHER_ERROR)

    with pytest.raises(ValueError, match="dimensions is invalid"):
        emb.embed_documents(_texts(50))

    assert len(fake.attempts) == 1, "不该对无法识别的错误做重试"


def test_single_text_rejected_by_cap_still_raises():
    """连 1 条都被判超限：已无法再收缩，必须抛出而不是无限重试。"""
    emb, _ = _embedder(max_batch=0)

    with pytest.raises(ValueError, match="batch size is invalid"):
        emb.embed_documents(_texts(2))
