# 企业知识库问答系统（后端 + RAG）

基于 **PostgreSQL + pgvector + FastAPI + 本地 Embedding（bge-small-zh-v1.5）+ DeepSeek** 实现的企业知识库问答系统。只包含**后端与 RAG 部分**，前端未实现，全部能力通过 REST + SSE 接口暴露，可直接作为面试 Demo 或后续前端对接的后端服务。

- 文档上传即异步入库：解析 → 分块 → 向量化 → 写入 pgvector，状态机 `pending → processing → ready / failed`，失败可手动重试
- 问答全链路：问题向量化 → 余弦相似度检索（Top-K + 阈值过滤）→ 拼 Prompt → 大模型 **SSE 流式**返回 → 带出处引用（文档名 + 相似度 + 片段）
- 检索无依据时**明确提示**，不编造答案，降低幻觉
- 会话与消息持久化，删除文档级联删除分块与向量
- Embedding / LLM 均支持 Provider 抽象，可通过环境变量切换本地 / 在线

## 技术栈

| 模块 | 技术 |
| --- | --- |
| Web 框架 | FastAPI + Uvicorn |
| 数据库 | PostgreSQL 18 + pgvector 0.8.6 |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic |
| 向量模型 | `BAAI/bge-small-zh-v1.5`（512 维，本地推理，ModelScope 下载） |
| LLM | DeepSeek `deepseek-chat`（OpenAI 兼容接口，SSE 流式） |
| 文档解析 | pypdf / python-docx / 内置 txt、md 解析 |
| 测试 | pytest + pytest-asyncio（Fake Embedder / Fake LLM 注入，不触网） |

## 目录结构

```
rag-konwledge/
├── README.md  .env.example  requirements.txt  alembic.ini  pyproject.toml
├── alembic/                  # 数据库迁移（versions/0001_initial.py：建 4 张表 + vector(512)）
├── app/
│   ├── main.py               # FastAPI 入口，挂载路由、CORS
│   ├── config.py             # pydantic-settings，全部配置从 .env 读取
│   ├── database.py           # engine / SessionLocal / Base / get_db
│   ├── models.py             # Document / Chunk(含 embedding) / Conversation / Message
│   ├── schemas.py            # Pydantic v2 请求/响应模型
│   ├── providers/            # Embedding(本地/在线) + LLM(OpenAI 兼容) 抽象
│   ├── services/             # 解析 / 分块 / 入库状态机 / 检索 / 问答(SSE)
│   └── api/                  # documents / chat / conversations / health
├── scripts/
│   ├── install_pgvector.ps1  # 安装 pgvector 预编译包到 PostgreSQL 18
│   ├── init_db.py            # 建库 + CREATE EXTENSION vector
│   ├── run_dev.ps1           # 一键启动开发服务
│   ├── make_demo_pdf.py      # 生成 demo/sample.pdf（演示用）
│   └── e2e_verify.py         # 端到端验收脚本（对照需求 §10 清单，需服务已启动）
├── frontend/                 # 前端（Vite + Vue3 + vue-router + Tailwind v4）
├── tests/                    # pytest：分块 / 检索 / 问答 全链路（Fake 注入）
└── demo/                     # 示例文档 sample.md / sample.pdf
```

前端目录结构：

```
frontend/
├── vite.config.ts            # tailwind 插件 + Element Plus 按需导入 + /api 代理到后端 :8000
└── src/
    ├── router/               # vue-router：/ 落地页，/chat 对话页，/documents 知识库页
    ├── api/                  # axios 封装(http.ts) + documents/conversations/chat(SSE 流式)
    ├── views/                # 页面目录：home/ chat/ documents/，各含 index.vue + 页面私有 components/
    │                          #   页面状态直接放 index.vue 组件内，子组件用 props/events 通信，无全局 store
    └── components/           # 公共组件 AppHeader；UI 组件全部用 Element Plus（el-menu / el-table / el-upload / el-dialog / ElMessage 等）
```

## 快速开始

### 1. 环境准备

- **PostgreSQL 18** 运行于 `localhost:5432`，账号 `postgres` / 密码在 `.env` 中配置
- **conda** 环境 `langchain`（Python 3.13），安装依赖：

```powershell
conda run -n langchain pip install -r requirements.txt
```

- 若 PostgreSQL 尚未安装 pgvector，执行（仅需一次）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_pgvector.ps1
```

### 2. 配置环境变量

```powershell
copy .env.example .env
# 编辑 .env，至少填入 LLM_API_KEY
```

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql+psycopg2://postgres:123456@localhost:5432/rag_kb` |
| `LLM_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | **必填**，大模型密钥 | 空 |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `EMBEDDING_PROVIDER` | `local` 本地模型 / `openai_compatible` 在线 | `local` |
| `EMBEDDING_MODEL` | 本地模型名 | `BAAI/bge-small-zh-v1.5` |
| `EMBEDDING_DIM` | **须与模型维度一致**（bge-small-zh=512） | `512` |
| `HF_ENDPOINT` | 模型下载镜像（本地未缓存时） | `https://hf-mirror.com` |
| `CHUNK_SIZE` / `OVERLAP` | 分块大小 / 重叠 | `500` / `100` |
| `TOP_K` | 检索返回分块数 | `4` |
| `SIMILARITY_THRESHOLD` | 相似度阈值，低于则判为无依据 | `0.5` |

> `EMBEDDING_DIM` 与数据库列维度强相关：换模型后需同时修改 `.env` 并重建表（删表后 `alembic upgrade head`）。

### 3. 初始化数据库

```powershell
conda run -n langchain python scripts/init_db.py
conda run -n langchain alembic upgrade head
```

### 4. 启动服务

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_dev.ps1
# 或直接：
conda run -n langchain uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动后：
- 接口文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/health>

### 5. 启动前端（可选，需先启动后端）

```powershell
cd frontend
pnpm install
pnpm dev
# 打开 http://localhost:5173（/api 自动代理到 :8000）
```

生产构建：`cd frontend && pnpm build`（产物在 `frontend/dist/`）。

> 对话流使用 **SSE**（`POST /api/chat` 的 `text/event-stream`）。前端使用 `@microsoft/fetch-event-source` 库解析（`src/api/chat.ts`），原生 `EventSource` 只支持 GET、无法携带 POST body。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（含 DB 连通性） |
| POST | `/api/documents/upload` | 上传文档（multipart，≤10MB，txt/md/pdf/docx），返回 202，异步入库 |
| GET | `/api/documents` | 文档列表，支持 `?status=ready` 过滤 |
| GET | `/api/documents/{id}` | 文档详情（含分块预览） |
| DELETE | `/api/documents/{id}` | 删除文档（级联删除分块与向量） |
| PATCH | `/api/documents/{id}` | 重命名文档（`body: {"filename": "新名称"}`） |
| POST | `/api/documents/{id}/retry` | 失败文档重新入库 |
| POST | `/api/chat` | 问答，SSE 流式返回（见下） |
| GET | `/api/conversations` | 会话列表（按更新时间倒序） |
| POST | `/api/conversations` | 新建会话（`body: {"title": "可选"}`） |
| GET | `/api/conversations/{id}/messages` | 会话消息（含引用来源） |
| DELETE | `/api/conversations/{id}` | 删除会话 |

### SSE 问答协议

`POST /api/chat`，`Content-Type: application/json`，返回 `text/event-stream`：

```json
{"question": "分块策略的默认参数是多少？", "conversation_id": null}
```

事件流（每行 `data: {...}`，事件之间空行分隔）：

| 事件类型 | 含义 |
| --- | --- |
| `{"type":"chunk","content":"..."}` | 大模型生成增量（多次） |
| `{"type":"sources","sources":[...]}` | 引用来源：`[{document_id, filename, chunk_id, chunk_index, similarity, snippet}]` |
| `{"type":"no_evidence","message":"..."}` | 检索无足够依据（低于相似度阈值） |
| `{"type":"error","message":"..."}` | LLM 调用 / 会话等错误 |
| `{"type":"done","conversation_id":"..."}` | 本次问答结束 |

> 不传 `conversation_id` 时自动新建会话并以问题前 30 字作标题；传既有 id 则沿用该会话。`conversation_id` 用返回的 `done` 事件值即可追问。

## 快速演示

```powershell
# 1. 上传示例文档
curl -F "file=@demo/sample.md" http://localhost:8000/api/documents/upload
# 2. 轮询状态到 ready（psql 确认 chunks.embedding 为 512 维向量）
curl "http://localhost:8000/api/documents?status=ready"
# 3. 提问（SSE 流式，末尾带引用）
curl -N -H "Content-Type: application/json" -d "{\"question\":\"分块策略的默认参数是多少？\"}" http://localhost:8000/api/chat
# 4. 无关问题 -> no_evidence
curl -N -H "Content-Type: application/json" -d "{\"question\":\"今天天气怎么样？\"}" http://localhost:8000/api/chat
# 5. 会话消息
curl http://localhost:8000/api/conversations
curl http://localhost:8000/api/conversations/{id}/messages
```

> Windows 控制台为 GBK 编码，`curl -d` 直接贴中文易乱码导致 400。建议用接口文档 `/docs` 或 Python 脚本发 JSON，避免编码问题。

## 测试

```powershell
conda run -n langchain python -m pytest -q
```

19 个用例，覆盖：分块大小 / overlap / 句子边界、检索 Top-K 排序与阈值过滤、问答 SSE 事件与消息落库、无依据处理、文档入库状态机（成功与失败）、文档重命名（成功 / 空名 / 不存在）。测试使用独立的 `rag_kb_test` 库，Embedding 与 LLM 均为 Fake 实现，**不触网、不下载模型**。

另有端到端验收脚本（对照需求 §10 清单逐项断言，含真实模型调用，需服务已启动）：

```powershell
conda run -n langchain python scripts/e2e_verify.py
```

## 主要设计说明

- **入库异步化**：上传接口立即返回，`BackgroundTasks` 后台解析入库，文档状态机可观测、可重试。
- **检索**：pgvector `<=>` 余弦距离，`similarity = 1 - distance`，过滤 `distance <= 1 - threshold`，按距离升序取 `TOP_K`。
- **防幻觉**：Prompt 只包含检索命中的分块并强制要求标注 `[1]`、`[2]` 编号；无依据时直接返回 `no_evidence`，不调用模型编造。
- **可追溯**：`messages.sources`(jsonb) 记录每个回答引用的文档 / 分块 / 相似度 / 片段。
- **Provider 抽象**：`EMBEDDING_PROVIDER` 与 `LLM_BASE_URL` 可在本地与在线之间切换，业务代码零改动。

## 环境要求

- Windows 11，conda（`langchain` 环境），Python 3.13
- PostgreSQL 18 + pgvector（`scripts/install_pgvector.ps1` 提供预编译安装）
- 首次启动会自动从 ModelScope 下载 bge-small-zh-v1.5（约 100MB）到 `./data/models`
