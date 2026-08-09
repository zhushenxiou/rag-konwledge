# 企业知识库问答系统（RAG + FastAPI + Vue 3）

基于 **PostgreSQL + pgvector + FastAPI + 在线 Embedding（千问 text-embedding-v4）+ DeepSeek** 实现的企业知识库问答系统，带 **Vue 3 前端**。范围刻意收敛：不做权限、多租户、任务队列，重点是把「上传文档 → 提问 → 带出处回答」这条 RAG 链路完整打通，作为面试 Demo 展示全链路技术能力。

- 文档上传即异步入库：解析 → 分块 → 向量化 → 写入 pgvector，状态机 `pending → processing → ready / failed`，失败可手动重试
- 问答全链路：混合检索（关键词 BM25 + 语义向量，**RRF 融合**）→ **在线重排（qwen3-rerank，可配置）** → 拼 Prompt → 大模型 **SSE 流式**返回 → 带出处引用（文档名 + 相似度 + 片段）
- 检索无依据时**明确提示**，不编造答案，降低幻觉
- 会话与消息持久化，删除文档级联删除分块与向量
- Embedding / LLM / Rerank 均走在线 OpenAI 兼容接口，可通过环境变量切换服务商

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 前端 | Vue 3 + Vite + TypeScript + Vue Router + Element Plus + Tailwind v4 |
| 后端 | FastAPI + Pydantic v2（REST + SSE） |
| ORM / 迁移 | SQLAlchemy 2.0 + Alembic |
| 数据库 | PostgreSQL 18 + pgvector 0.8.6（业务表 + 向量同库） |
| 向量模型 | 千问 `text-embedding-v4`（512 维，DashScope 在线接口） |
| LLM | DeepSeek `deepseek-chat`（OpenAI 兼容接口，SSE 流式） |
| 检索 | jieba 分词 + BM25（关键词）+ pgvector 余弦（语义）两路召回 + **RRF 融合** + qwen3-rerank 在线重排 |
| 文档解析 | pypdf / python-docx / 内置 txt、md 解析 |
| 测试 | pytest + pytest-asyncio（Fake Embedder / Fake LLM / Fake Reranker 注入，不触网） |

## 目录结构

```
rag-konwledge/
├── README.md  .env.example  requirements.txt  alembic.ini  pyproject.toml
├── alembic/                  # 数据库迁移（0001：建 4 张表 + vector(512)；0002：embedding HNSW 索引；0003：messages.retrieval_mode；0004：删除 retrieval_mode）
├── app/
│   ├── main.py               # FastAPI 入口，挂载路由、CORS
│   ├── config.py             # pydantic-settings，全部配置从 .env 读取
│   ├── database.py           # engine / SessionLocal / Base / get_db
│   ├── models.py             # Document / Chunk(含 embedding) / Conversation / Message
│   ├── schemas.py            # Pydantic v2 请求/响应模型
│   ├── providers/            # Embedding + LLM + Rerank（均为在线 OpenAI 兼容/可切换抽象）
│   ├── services/             # 解析 / 分块 / 入库状态机 / 检索 / 问答(SSE)
│   └── api/                  # documents / chat / conversations / health
├── scripts/
│   ├── install_pgvector.ps1  # 安装 pgvector 预编译包到 PostgreSQL 18
│   ├── init_db.py            # 建库 + CREATE EXTENSION vector
│   ├── run_dev.ps1           # 一键启动开发服务
│   └── e2e_verify.py         # 端到端验收脚本（对照 README「Demo 验收清单」，需服务已启动）
├── frontend/                 # 前端（Vite + Vue3 + vue-router + Tailwind v4）
└── tests/                    # pytest：分块 / 检索 / 问答 全链路（Fake 注入）
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
| `EMBEDDING_BASE_URL` | OpenAI 兼容 embedding 接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `EMBEDDING_API_KEY` | **必填**，embedding 密钥（阿里云 DashScope） | 空 |
| `EMBEDDING_MODEL_NAME` | embedding 模型名 | `text-embedding-v4` |
| `EMBEDDING_DIM` | **须与模型输出维度 / 数据库 Vector 列一致**（代码会显式传 `dimensions`） | `512` |
| `CHUNK_SIZE` / `OVERLAP` | 分块大小 / 重叠 | `500` / `100` |
| `TOP_K` | 检索返回分块数 | `4` |
| `SIMILARITY_THRESHOLD` | 相似度阈值，低于则判为无依据 | `0.5` |
| `BM25_RECALL_K` | 混合检索每路召回候选数（需 > `TOP_K`） | `20` |
| `RRF_K` | RRF 平滑常数 `score = Σ 1/(k+rank)` | `60` |
| `RERANK_ENABLED` | 检索后在线重排（开启增加一次在线调用，失败自动回退） | `true` |
| `RERANK_BASE_URL` | 重排接口地址（DashScope OpenAI 兼容） | `https://dashscope.aliyuncs.com/compatible-api/v1` |
| `RERANK_API_KEY` | **重排密钥**（通常与 `EMBEDDING_API_KEY` 相同）；留空则跳过重排 | 空 |
| `RERANK_MODEL_NAME` | 重排模型 | `qwen3-rerank` |
| `RERANK_CANDIDATES` | 参与重排的候选数（先召回 N，重排后取 `TOP_K`） | `10` |

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
>
> 检索固定为**混合模式**（关键词 BM25 + 语义向量，RRF 融合排序），不做模式选择。

## 快速演示

```powershell
# 1. 上传示例文档（可用前端「知识库」页，或 /docs 上传任意 txt/md/pdf/docx）
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

26 个用例，覆盖：分块大小 / overlap / 句子边界、混合检索 Top-K 排序与阈值过滤、BM25 + 语义两路召回与 RRF 融合（含关键词通道挽救低相似度命中）、问答 SSE 事件与消息落库、无依据处理、在线重排（重排排序 / 失败回退）、文档入库状态机（成功与失败）、文档重命名（成功 / 空名 / 不存在）。测试使用独立的 `rag_kb_test` 库，Embedding / LLM / Rerank 均为 Fake 实现，**不触网、不下载模型**。

另有端到端验收脚本（对照下文「Demo 验收清单」逐项断言，含真实模型调用，需服务已启动）：

```powershell
conda run -n langchain python scripts/e2e_verify.py
```

## 系统设计

### 数据模型

**documents 文档表**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | 主键 |
| filename | varchar | 原始文件名 |
| file_path | text | 存储路径 |
| file_size | int | 文件大小 |
| source_type | varchar | pdf / txt / md / docx |
| status | varchar | pending / processing / ready / failed |
| chunk_count | int | 分块数量 |
| error_message | text | 失败原因 |
| created_at | timestamp | 创建时间 |

**chunks 分块表**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | 主键 |
| document_id | UUID | 外键，关联 documents |
| content | text | 分块文本 |
| chunk_index | int | 分块序号 |
| metadata | jsonb | 页码、标题等 |
| embedding | vector(512) | 向量，维度跟随模型（text-embedding-v4 输出 512 维） |
| created_at | timestamp | 创建时间 |

**conversations 会话表**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | 主键 |
| title | varchar | 会话标题 |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |

**messages 消息表**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | 主键 |
| conversation_id | UUID | 外键，关联 conversations |
| role | varchar | user / assistant |
| content | text | 消息内容 |
| sources | jsonb | 回答引用的来源列表 |
| created_at | timestamp | 创建时间 |

### RAG 主流程

```mermaid
flowchart LR
    A[上传文档] --> B[解析文本]
    B --> C[分块]
    C --> D[Embedding 向量化]
    D --> E[(PostgreSQL + pgvector)]

    F[用户提问] --> G[问题检索]
    G --> H[多路召回 + RRF 取 Top-K]
    H --> H2[在线重排 qwen3-rerank]
    H2 --> I[拼装 Prompt]
    I --> J[LLM 生成]
    J --> K[SSE 流式返回]
    K --> L[前端展示回答与来源]
    E -.提供上下文.-> H
```

**入库流程**

1. 上传文件到临时目录
2. 按类型解析为纯文本（txt/md/pdf/docx 走不同解析器）
3. 按 `chunk_size` / `overlap` 分块，在句子边界断开、保留段落语义
4. 调用 Embedding 接口为每块生成向量
5. 写入 `chunks` 表，更新文档状态为 `ready`（失败则 `failed`，可重试）

**问答流程**

1. 保存用户问题到 `messages`
2. 混合召回：关键词（jieba + BM25）与语义（pgvector 余弦）两路并发召回候选
3. 对两路候选做 RRF 融合排序，过滤低相关分块，取 Top-K
4. 可选：对候选做在线重排（qwen3-rerank），再收窄到 Top-K（调用失败回退原排序）
5. 将分块内容、用户问题组装成 Prompt
6. LLM 流式生成，通过 SSE 返回前端
7. 回答完成后保存消息与来源列表

### 关键设计决策

- **入库异步化**：上传接口立即返回，`BackgroundTasks` 后台解析入库，文档状态机可观测、可重试。
- **pgvector 选型**：业务数据 + 向量存在同一个 PostgreSQL，启动简单；中小规模知识库足够。若要海量向量，可替换为 Milvus / Qdrant，上层检索接口保持抽象。
- **混合检索 + RRF（唯一检索方式）**：固定两路并发召回——jieba 分词 + BM25 召回精确关键词命中，pgvector 余弦（`similarity = 1 - distance`，阈值过滤）召回语义近似；两路各取 `BM25_RECALL_K` 个候选，并集后按 RRF `score = Σ 1/(k + rank)` 融合排序再取 `TOP_K`，两路同时命中的分块天然靠前。关键词通道可挽救语义低相似度命中，反之亦然。只保留一种模式，前端不再有模式切换。
- **在线重排（可选，默认开）**：检索后对 `RERANK_CANDIDATES` 个候选用 DashScope `qwen3-rerank` 按 (问题, 分块) 相关性精排，再收窄到 `TOP_K` 进 Prompt；重排**非致命**——调用失败自动回退原召回顺序，问答不受影响。重排分数写入 `sources.similarity`，与最终排序口径一致。
- **防幻觉**：Prompt 只包含检索命中的分块并强制要求标注 `[1]`、`[2]` 编号；无依据时直接返回 `no_evidence`，不调用模型编造。
- **可追溯**：`messages.sources`(jsonb) 记录每个回答引用的文档 / 分块 / 相似度 / 片段。
- **Provider 抽象**：`EMBEDDING_BASE_URL` / `LLM_BASE_URL` / `RERANK_BASE_URL` 可切换不同在线服务商，业务代码零改动。

## 功能需求与验收标准

### F1 文档管理

#### F1.1 上传文档

用户选择本地文件上传，支持 `txt / md / pdf / docx`。

验收标准：
- 上传后前端显示文件名、大小、上传时间、处理状态
- 单文件大小限制为 10MB，超出时给出明确错误提示

#### F1.2 解析与分块

后端按文件类型解析文本，并按规则分块。

验收标准：
- 不同文件类型走不同解析器
- 默认 `chunk_size = 500` 字符、`overlap = 100` 字符，参数可配置
- 分块逻辑能保留段落语义，避免在句子中间硬切

#### F1.3 向量化与入库

每个分块生成向量，写入 `chunks.embedding`，并更新文档状态。

验收标准：
- 入库后文档状态变为 `ready`
- 可在 PostgreSQL 中查询到分块记录和向量
- 处理失败时文档状态为 `failed`，可手动重试

#### F1.4 文档列表与删除

展示已上传文档、分块数量、处理状态，支持删除。

验收标准：
- 删除文档时级联删除其分块和向量
- 删除后检索结果中不再出现该文档内容

### F2 RAG 问答

#### F2.1 提问与检索（混合召回 + RRF 融合）

用户输入问题，后端按**固定混合检索**在知识库中检索 Top-K 相关分块：关键词（jieba + BM25）与语义（问题向量化后在 pgvector 上做余弦相似度检索，阈值过滤）两路各召回候选，并集后按 **RRF**（`score = Σ 1/(k+rank)`）融合排序。

验收标准：
- 默认 `top_k = 4`，可配置
- 检索固定混合模式，不提供单选切换（关键词通道可挽救语义低相似度命中，反之亦然）
- 返回结果按相关度排序，并显示相似度分数
- 可选在线重排：检索后对 `rerank_candidates` 个候选按 (问题, 分块) 用 DashScope `qwen3-rerank` 精排，再取 `top_k`；失败自动回退原排序

#### F2.2 生成回答并引用来源

把检索到的分块作为上下文，构造 Prompt 调用 LLM 生成回答。

验收标准：
- 回答展示引用来源，例如分块摘要、文档名、页码或片段
- 引用与检索结果对应，而不是编造来源

#### F2.3 流式输出

后端通过 SSE 逐段返回生成内容，前端实时渲染。

验收标准：
- 回答过程中页面实时显示文字，不等待完整结果
- 流中断时前端给出错误提示，而不是一直转圈

#### F2.4 无依据处理

当检索相似度低于阈值或没有相关内容时，给出明确提示。

验收标准：
- 默认相似度阈值为 `0.5`，可配置
- 低相似度时回答："当前知识库中没有找到足够依据"
- 不强行编造答案

#### F2.5 新建与清空会话

支持新建会话、清空当前对话。

### F3 会话管理

- 展示历史会话标题与最近更新时间
- 点击会话后加载完整问答记录，包含问题和回答
- 支持删除单个会话，并同步删除其消息

### F4 参数配置（轻量）

通过后端环境变量提供，不单独做管理后台：

- Embedding 模型与 LLM Provider
- `chunk_size`、`overlap`
- `top_k`、相似度阈值
- 单文件大小限制

### F5 可选扩展（不进入 MVP）

- JWT 用户登录
- 多知识库 / 文档分类
- 文档内容在线编辑
- 后台任务队列 Celery / Redis

## 非功能需求

| 类别 | 要求 |
| --- | --- |
| 性能 | 单用户演示场景下，文档入库分钟级完成，问答首字响应 3 秒内 |
| 稳定性 | 上传失败、解析失败、模型调用失败都要有错误提示，不能导致服务崩溃 |
| 可维护性 | 使用 Alembic 管理表结构，配置集中在环境变量 |
| 可观测性 | 后端打印入库、检索、模型调用关键日志 |
| 测试 | 覆盖分块逻辑、检索接口、问答接口的 happy path（另有端到端验收脚本） |
| 文档 | README 包含启动步骤、环境变量说明、演示脚本 |

## 前端页面设计

- **文档管理页**：顶部上传 / 刷新按钮；主体文档表格（文件名、类型、大小、状态、分块数、上传时间、操作）；操作含查看分块、删除、失败重试。
- **问答页**：左侧会话列表（新建、切换、删除）；右侧消息流（用户问题与助手回答交替）；回答下方来源卡片（文档名 + 片段）；底部输入框 + 发送按钮，Enter 发送；回答过程中输入框禁用，展示"正在生成"状态。
- 页面状态直接放在组件内（无全局 store），导航切换会重置内存状态，数据以服务端为准。

## Demo 验收清单

1. 启动 PostgreSQL、后端、前端
2. 上传一份 PDF 和一份 Markdown，状态变为 `ready`
3. 在数据库中确认 `documents`、`chunks` 和向量已写入
4. 提问文档内的问题，得到带来源的流式回答
5. 提问无关内容，得到"没有足够依据"的提示
6. 新建、切换、删除会话，刷新页面后历史仍存在
7. 删除一个文档后，再提问相关内容，答案中不再引用它
8. 停止并重启服务，数据仍存在，证明 PostgreSQL 持久化
9. 打开 `/docs`，FastAPI 自动生成的接口文档可正常访问

## 面试讲解点

1. **为什么选 pgvector**：一个库同时解决业务数据和向量数据，适合中小规模；说明如何平滑迁移到独立向量库
2. **分块策略**：为什么设置 `chunk_size` 和 `overlap`，如何避免语义断裂
3. **Embedding / LLM Provider 抽象**：如何通过 base_url / api_key 切换不同在线服务商
4. **多路召回 + RRF + 重排**：关键词 BM25 与语义向量如何互补，混合检索固定用 `score = Σ 1/(k+rank)` 融合排序，为什么比单一余弦召回更稳；召回后接在线重排（qwen3-rerank）精排，形成「召回（广）→ RRF 融合 → 重排（准）→ 进 Prompt」的完整链路，并说明重排失败如何降级回退
5. **RAG 与微调的区别**：知识更新快、无需重新训练，以及各自的适用场景
6. **引用溯源**：如何让模型只依据检索结果回答，降低幻觉
7. **SSE 流式输出**：相比一次性返回的用户体验优势与实现方式
8. **异步与失败处理**：入库任务的状态机、失败重试
9. **数据一致性**：删除文档时级联删除向量，保证检索结果干净

## 建议开发顺序

1. 后端骨架：FastAPI + 数据库连接 + Alembic + `/api/health`
2. 文档上传与入库：解析、分块、Embedding、写 pgvector
3. RAG 问答接口：检索、Prompt、SSE 输出
4. 前端骨架：Vue 3 + 路由 + 状态管理 + 布局
5. 前端文档管理页
6. 前端问答页与会话管理
7. 联调、错误处理、README、演示脚本

## 风险与注意事项

- Embedding / LLM / Rerank 均为在线接口，依赖网络与 API 配额，Demo 前要确认网络可用
- PDF 解析质量取决于扫描件还是文本型 PDF，演示素材优先选文本型
- 大文件入库不要阻塞请求，入库放后台任务或线程池
- SSE 流中断要能结束前端 loading 状态
- 删除文档要同时清理向量，避免脏数据
- 不要把 API Key 写进前端代码

## 环境要求

- Windows 11，conda（`langchain` 环境），Python 3.13
- PostgreSQL 18 + pgvector（`scripts/install_pgvector.ps1` 提供预编译安装）
- 无需本地模型，配置 `EMBEDDING_API_KEY`（阿里云 DashScope）即可在线向量化
