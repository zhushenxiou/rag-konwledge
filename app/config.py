"""全局配置：全部通过环境变量 / .env 提供。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- 应用 ----
    app_name: str = "企业知识库问答系统"

    # ---- 数据库 ----
    database_url: str = "postgresql+psycopg2://postgres:123456@localhost:5432/rag_kb"

    # ---- 上传 ----
    upload_dir: str = "./data/uploads"
    max_file_size_mb: int = 10

    # ---- 分块 / 检索 ----
    chunk_size: int = 500
    overlap: int = 100
    top_k: int = 4
    similarity_threshold: float = 0.5
    # 混合检索每路召回的候选数（需 > top_k，RRF 融合后再取 top_k）
    bm25_recall_k: int = 20
    # RRF 平滑常数：score = Σ 1/(k + rank)，k 越小越倾向靠前排名
    rrf_k: int = 60

    # ---- Embedding（在线 OpenAI 兼容接口，千问 DashScope）----
    embedding_dim: int = 512                    # 必须与 embedding 模型输出维度 / 数据库 Vector 列一致
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: str = ""
    embedding_model_name: str = "text-embedding-v4"

    # ---- LLM（OpenAI 兼容接口）----
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.3

    # ---- Rerank（DashScope 在线重排，qwen3-rerank）----
    rerank_enabled: bool = True                # 检索后在线重排（开启会增加一次在线调用）
    rerank_base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1"
    rerank_api_key: str = ""                   # 通常与 EMBEDDING_API_KEY 相同（DashScope）
    rerank_model_name: str = "qwen3-rerank"
    rerank_candidates: int = 10                # 参与重排的候选数（先召回 10，重排后取 top_k）

    # ---- 对话记忆（上下文窗口管理：历史压缩 + 关键信息抽取）----
    memory_enabled: bool = True               # 总开关；关掉则退化为纯单轮（不传历史）
    memory_recent_tokens: int = 1200          # 最近原文窗口估算 token 预算，超过触发滚动压缩
    memory_recent_rounds: int = 2             # 压缩时保留的最近完整对话轮数（不进摘要，保真）
    memory_max_facts: int = 20                # 关键事实条目上限（超限由 LLM 在更新时裁剪）
    memory_extract_every_turn: bool = True    # 每轮抽取关键事实（False → 仅压缩时抽取）
    memory_rewrite_enabled: bool = True       # 多轮时改写检索问题（指代消解）

    # ---- 测试 ----
    test_database_url: str = ""

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def allowed_extensions(self) -> set[str]:
        return {"txt", "md", "pdf", "docx"}


settings = Settings()
