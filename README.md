# 企业知识库问答系统（RAG + FastAPI + Vue 3）

基于 **PostgreSQL + pgvector + FastAPI + 在线 Embedding（千问 text-embedding-v4）+ DeepSeek** 实现的企业知识库问答系统，带 **Vue 3 前端**。范围刻意收敛：不做多租户、任务队列、多用户权限（鉴权做到单账号可用，见 F6），重点是把「上传文档 → 提问 → 带出处回答」这条 RAG 链路完整打通，作为面试 Demo 展示全链路技术能力。

- 文档上传即异步入库：解析 → 分块 → 向量化 → 写入 pgvector，状态机 `pending → processing → ready / failed`，失败可手动重试
- 问答全链路：混合检索（关键词 BM25 + 语义向量，**RRF 融合**）→ **在线重排（qwen3-rerank，可配置）** → 拼 Prompt → 大模型 **SSE 流式**返回 → 带出处引用（文档名 + 相似度 + 片段）
- **上下文窗口管理**：多轮对话自动滚动压缩（旧轮折叠进摘要）+ 每轮关键事实抽取 + 追问改写（指代消解，仅用于检索），长对话降低 Token 消耗且保留关键信息
- **登录鉴权**：登录页（账号 + 密码 + 4 位数字图片验证码）→ 内存 token；除 `/docs`、健康检查、登录相关外，所有业务接口都要 `Authorization: Bearer`，前端 401 自动跳登录
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
| 鉴权 | 单账号 + 内存 token（`secrets` / `hmac`，均 stdlib）+ Pillow 手绘图片验证码；无 JWT / Redis 依赖 |
| 测试 | pytest + pytest-asyncio（Fake Embedder / Fake LLM / Fake Reranker 注入，不触网） |

## 目录结构

```
rag-konwledge/
├── README.md  .env.example  requirements.txt  alembic.ini  pyproject.toml
├── alembic/                  # 数据库迁移（0001：建 4 张表 + vector(512)；0002：embedding HNSW 索引；0003：messages.retrieval_mode；0004：删除 retrieval_mode；0005：对话记忆列 summary / key_facts / is_folded）
├── app/
│   ├── main.py               # FastAPI 入口，挂载路由、CORS
│   ├── config.py             # pydantic-settings，全部配置从 .env 读取（含对话记忆组 MEMORY_*）
│   ├── database.py           # engine / SessionLocal / Base / get_db
│   ├── models.py             # Document / Chunk(含 embedding) / Conversation(含记忆列) / Message(含 is_folded)
│   ├── schemas.py            # Pydantic v2 请求/响应模型
│   ├── providers/            # Embedding + LLM + Rerank（均为在线 OpenAI 兼容/可切换抽象）
│   ├── services/             # 解析 / 分块 / 入库状态机 / 检索 / 问答(SSE) / 对话记忆(memory.py) / 鉴权(auth.py + captcha.py)
│   └── api/                  # documents / chat / conversations / health / auth；deps.py 提供 require_auth
├── scripts/
│   ├── install_pgvector.ps1  # 安装 pgvector 预编译包到 PostgreSQL 18
│   ├── init_db.py            # 建库 + CREATE EXTENSION vector
│   └── e2e_verify.py         # 端到端验收脚本（对照 README「Demo 验收清单」，需服务已启动）
├── setup.md                  # 环境搭建与启动教程
├── frontend/                 # 前端（Vite + Vue3 + vue-router + Tailwind v4）
└── tests/                    # pytest：分块 / 检索 / 问答 / 鉴权 全链路（Fake 注入）
```

前端目录结构：

```
frontend/
├── vite.config.ts            # tailwind 插件 + Element Plus 按需导入 + /api 代理到后端 :8000
└── src/
    ├── router/               # vue-router：/login 登录页、/ 落地页、/chat 对话页、/documents 知识库页 + 全局登录守卫
    ├── api/                  # 后端接口封装：axios(http.ts) + auth / documents / conversations / chat(SSE 流式)
    ├── auth/                 # 登录凭证 token.ts：localStorage 存取 + 401 失效跳转
    │                          #   独立成层是因为它管的是"凭据"而非"某个接口"，且不 import 任何业务模块
    ├── views/                # 页面目录：login/ home/ chat/ documents/，各含 index.vue + 页面私有 components/
    │                          #   页面状态直接放 index.vue 组件内，子组件用 props/events 通信，无全局 store
    │                          #   （唯一例外：登录凭证放 auth/token.ts —— 它是请求层凭据而非页面状态）
    └── components/           # 公共组件 AppHeader（含当前用户与退出登录）；UI 组件全部用 Element Plus（el-menu / el-table / el-upload / el-dialog / ElMessage 等）
```

## 快速开始

完整搭建与启动教程见 [setup.md](setup.md)：pgvector 安装 → 依赖 → `.env` 配置 → 初始化数据库 → 启动后端（纯命令）→ 启动前端（pnpm）→ 测试。

### 环境变量参考

```powershell
copy .env.example .env   # 编辑 .env，至少填入 LLM_API_KEY、EMBEDDING_API_KEY
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
| `MEMORY_ENABLED` | 对话记忆总开关；关掉退化为纯单轮（不传历史） | `true` |
| `MEMORY_RECENT_TOKENS` | 最近原文窗口估算 token 预算，超过触发滚动压缩 | `1200` |
| `MEMORY_RECENT_ROUNDS` | 压缩时保留的最近完整对话轮数（不进摘要，保真） | `2` |
| `MEMORY_MAX_FACTS` | 关键事实条目上限（超限由 LLM 在更新时裁剪） | `20` |
| `MEMORY_EXTRACT_EVERY_TURN` | 每轮抽取关键事实（`false` → 仅压缩时抽取） | `true` |
| `MEMORY_REWRITE_ENABLED` | 多轮时改写检索问题（指代消解；改写只用于检索） | `true` |
| `AUTH_USERNAME` | **演示固定账号**，公网部署前必须改 | `zhuliang` |
| `AUTH_PASSWORD` | **演示固定口令（明文）**，公网部署前必须改成哈希存储 | `zhuliang` |
| `AUTH_TOKEN_TTL_MINUTES` | 登录 token 有效期（分钟） | `720` |
| `CAPTCHA_TTL_SECONDS` | 验证码有效期（秒），且一次性 | `120` |
| `AUTH_CAPTCHA_BYPASS` | **仅本地自动化验收**：取验证码接口额外返回明文 `code`。生产必须保持 `false`，开启时后端启动会打 warning | `false` |

> `EMBEDDING_DIM` 与数据库列维度强相关：换模型后需同时修改 `.env` 并重建表（删表后 `alembic upgrade head`）。
>
> **单请求条数上限无需配置**。DashScope 的 `text-embedding-v3/v4` 硬性要求单次请求 ≤10 条（与 token 多少无关，超了直接 `400 InvalidParameter: batch size is invalid`），而 v1/v2 是 25、OpenAI 是 2048。`OpenAICompatEmbedder` 不写死这个值，改为**运行时探测**：先整批发，被拒就在"已知可行 / 已知不可行"之间二分，收敛后记住该值供后续复用 —— 换厂商不用改任何配置。短文档一次成功、零探测开销；探测成本每个进程只付一次（200 chunk 的文档约 6 次试探请求）。

前端启动：`cd frontend && pnpm install && pnpm dev`（:5173，`/api` 代理到 :8000）。

> 对话流使用 **SSE**（`POST /api/chat` 的 `text/event-stream`）。前端使用 `@microsoft/fetch-event-source` 库解析（`src/api/chat.ts`），原生 `EventSource` 只支持 GET、无法携带 POST body。

## 登录与鉴权

除下表「开放面」外，**所有 `/api/*` 接口都要求登录**，未带有效 token 一律返回 `401`。

流程：取图片验证码 → 登录换 token → 后续请求带 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/auth/captcha` | 取一张新验证码，返回 `{captcha_id, image}`（`image` 为 PNG 的 data URI，明文只存服务端） |
| POST | `/api/auth/login` | `{username, password, captcha_id, captcha_code}` → `{token, token_type, expires_in}`；验证码错/过期 `400`，账号密码错 `401` |
| POST | `/api/auth/logout` | 撤销当前 token → `204` |
| GET | `/api/auth/me` | 返回 `{username}`；前端启动时用它校验本地存量 token 是否仍有效 |

**开放面**（不鉴权）：`/docs`、`/openapi.json`、`/api/health`、`GET /api/auth/captcha`、`POST /api/auth/login`。
文档保持开放是为了方便演示与手工联调；`/docs` 右上角有 Authorize 按钮，贴上 token 即可直接调受保护接口。

**登录页**（`/login`）：账号 + 密码 + 4 位数字验证码。验证码可点击刷新，登录失败后自动换一张。

设计取舍：

- **验证码一次性**：校验时先删再比，无论对错都作废。因此每次猜想都要重新取图，4 位数字的在线爆破成本被显著抬高；代价是输错一次要重新看图（前端已自动刷新）。
- **验证码明文只存服务端内存**，响应体只给图片；`AUTH_CAPTCHA_BYPASS` 是给黑盒验收脚本留的口子，默认关闭。
- **token 是不透明随机串 + 服务端内存查表**，不用 JWT：仓库无 jwt 依赖，演示也不需要无状态校验。代价见「风险与注意事项」。
- **口令比较用 `hmac.compare_digest`**，且两侧先编码成 bytes（该函数对含非 ASCII 的 `str` 会抛 `TypeError`）。
- 登录顺序是**先校验验证码、再比对口令**，验证码不过就不进入口令比较。

## API 一览

> 除 `/api/health` 与 `/api/auth/{captcha,login}` 外，下表所有接口都需要 `Authorization: Bearer <token>`（见上一节）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（含 DB 连通性），**不需登录** |
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
>
> 多轮追问时，后端会对检索问题做**改写**（指代消解），改写只影响检索、不影响生成；长对话会自动做**滚动压缩 + 关键事实抽取**，这些记忆维护调用均**非致命**——失败静默回退，不产生 `error` 事件，也不影响主回答。

## 快速演示

```powershell
# 0. 先登录换 token（受保护接口都要带它）。先用 /docs 的 Authorize 按钮或前端登录页拿 token：
#    $TOKEN = "<从 /api/auth/login 响应里复制的 token>"
# 1. 上传示例文档（可用前端「知识库」页，或 /docs 上传任意 txt/md/pdf/docx）
# 2. 轮询状态到 ready（psql 确认 chunks.embedding 为 512 维向量）
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/documents?status=ready"
# 3. 提问（SSE 流式，末尾带引用）
curl -N -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"分块策略的默认参数是多少？\"}" http://localhost:8000/api/chat
# 4. 无关问题 -> no_evidence
curl -N -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"今天天气怎么样？\"}" http://localhost:8000/api/chat
# 5. 会话消息
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/conversations
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/conversations/{id}/messages
```

> Windows 控制台为 GBK 编码，`curl -d` 直接贴中文易乱码导致 400。建议用接口文档 `/docs` 或 Python 脚本发 JSON，避免编码问题。
>
> 中文问题 + 手工取验证码都比较折腾，直接跑 `scripts/e2e_verify.py` 更省事（它走 `AUTH_CAPTCHA_BYPASS` 自动登录）。

## 测试

```powershell
"C:/ProgramData/miniconda3/envs/langchain/python.exe" -m pytest -q
```

78 个用例，覆盖：分块大小 / overlap / 句子边界、混合检索 Top-K 排序与阈值过滤、BM25 + 语义两路召回与 RRF 融合（含关键词通道挽救低相似度命中）、问答 SSE 事件与消息落库、无依据处理、在线重排（重排排序 / 失败回退）、文档入库状态机（成功与失败）、文档重命名（成功 / 空名 / 不存在）、**对话记忆**（每轮事实抽取 / 超预算滚动压缩 / 追问改写驱动检索 / 记忆调用失败回退 / 关闭时零额外调用 / token 估算）、**鉴权**（图片验证码格式 / 明文不外泄 / 一次性 / 过期 / 未知 id、登录成功 / 口令错 / 未知账号 / 非 ASCII 账号不 500、token 生命周期与登出撤销、**全部业务端点的未登录 401 回归网**）、**Embedding 自适应分批**（取数请求不越服务端上限 / 跨批顺序不乱 / **收敛到真实上限而非减半近似** / 探测成本是对数级 / 第二次调用零探测开销 / 短文档一次成功 / 小文档先行不会锁死批大小 / 上限为 1 能收敛 / 无法识别的 400 原样抛出不被误重试）。测试使用独立的 `rag_kb_test` 库，Embedding / LLM / Rerank 均为 Fake 实现，**不触网、不下载模型**。

> 鉴权用例不去 OCR 图片：直接调 `services.auth.new_captcha()` 拿明文再走真实登录接口，签发 / 一次性 / 过期链路仍被真实覆盖。
> 其中「未登录 401」的端点清单取自 OpenAPI，所以**新增接口会自动被纳入**——只要它忘了挂 `require_auth`，用例立刻变红。

另有端到端验收脚本（对照下文「Demo 验收清单」逐项断言，含真实模型调用，需服务已启动）：

```powershell
# 服务须以 AUTH_CAPTCHA_BYPASS=true 启动（脚本是跨进程黑盒客户端，读不出图片验证码）
AUTH_CAPTCHA_BYPASS=true python -m uvicorn app.main:app --port 8000
"C:/ProgramData/miniconda3/envs/langchain/python.exe" scripts/e2e_verify.py
```

> 该脚本依赖 `demo/sample.md` 与 `demo/sample.pdf`，这两个素材当前不在仓库里，脚本跑到第 1 步会 `FileNotFoundError`。放入任意同名文件即可跑完。

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
| summary | text | 滚动压缩后的对话摘要（仅后端 Prompt 使用，前端不展示） |
| key_facts | jsonb | 抽取出的长期关键事实数组（用户偏好 / 确认参数 / 结论等） |

**messages 消息表**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | 主键 |
| conversation_id | UUID | 外键，关联 conversations |
| role | varchar | user / assistant |
| content | text | 消息内容 |
| sources | jsonb | 回答引用的来源列表 |
| is_folded | boolean | 已被滚动压缩折叠进摘要的消息（前端仍展示，不进 LLM Prompt） |
| created_at | timestamp | 创建时间 |

### RAG 主流程

```mermaid
flowchart LR
    A[上传文档] --> B[解析文本]
    B --> C[分块]
    C --> D[Embedding 向量化]
    D --> E[(PostgreSQL + pgvector)]

    F[用户提问] --> F1[对话记忆：改写检索问题 + 滚动压缩旧轮]
    F1 --> G[问题检索]
    G --> H[多路召回 + RRF 取 Top-K]
    H --> H2[在线重排 qwen3-rerank]
    H2 --> I[拼装 Prompt（摘要 + 关键事实 + 最近原文 + 检索片段）]
    I --> J[LLM 生成]
    J --> K[SSE 流式返回]
    K --> L[前端展示回答与来源]
    K --> M[每轮抽取关键事实，持久化到会话]
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
2. **对话记忆维护（非致命）**：
   - 若未折叠原文窗口的估算 token 超过 `memory_recent_tokens`，把最旧几轮折叠进 `conversations.summary`（`maintain_memory` 一次调用产出新摘要 + 新事实），被折叠消息标 `is_folded=true`
   - 若会话已有历史，把当前追问改写为自包含问题（`rewrite_question`），改写结果**只用于检索**
3. 混合召回：关键词（jieba + BM25）与语义（pgvector 余弦）两路并发召回候选（对改写后的问题）
4. 对两路候选做 RRF 融合排序，过滤低相关分块，取 Top-K
5. 可选：对候选做在线重排（qwen3-rerank），再收窄到 Top-K（调用失败回退原排序）
6. 将「对话摘要 + 关键事实 + 最近原文窗口 + 检索片段 + 原始问题」组装成 Prompt
7. LLM 流式生成，通过 SSE 返回前端
8. 回答完成后保存消息与来源列表；**每轮抽取关键事实**更新 `conversations.key_facts`（非致命，失败保持原记忆）

### 关键设计决策

- **入库异步化**：上传接口立即返回，`BackgroundTasks` 后台解析入库，文档状态机可观测、可重试。
- **pgvector 选型**：业务数据 + 向量存在同一个 PostgreSQL，启动简单；中小规模知识库足够。若要海量向量，可替换为 Milvus / Qdrant，上层检索接口保持抽象。
- **混合检索 + RRF（唯一检索方式）**：固定两路并发召回——jieba 分词 + BM25 召回精确关键词命中，pgvector 余弦（`similarity = 1 - distance`，阈值过滤）召回语义近似；两路各取 `BM25_RECALL_K` 个候选，并集后按 RRF `score = Σ 1/(k + rank)` 融合排序再取 `TOP_K`，两路同时命中的分块天然靠前。关键词通道可挽救语义低相似度命中，反之亦然。只保留一种模式，前端不再有模式切换。
- **在线重排（可选，默认开）**：检索后对 `RERANK_CANDIDATES` 个候选用 DashScope `qwen3-rerank` 按 (问题, 分块) 相关性精排，再收窄到 `TOP_K` 进 Prompt；重排**非致命**——调用失败自动回退原召回顺序，问答不受影响。重排分数写入 `sources.similarity`，与最终排序口径一致。
- **上下文窗口管理（对话历史压缩 + 关键信息抽取）**：多轮对话不再把全量原文塞进 Prompt，而是维护 `conversations.summary`（滚动摘要）+ `conversations.key_facts`（长期关键事实）+ 最近 `memory_recent_rounds` 轮原文窗口，长对话下历史 Token 从「随轮数线性增长」压成「有界」；每轮用同一次 `maintain_memory` 调用同时滚动摘要与抽取事实（用户偏好 / 确认参数 / 结论）。压缩、抽取、改写全部复用 `LLM.stream_chat` 收集文本，**非致命**——失败静默回退，主回答不受影响。
- **查询改写（指代消解，仅用于检索）**：多轮追问（如"那 overlap 呢？"）先改写为自包含问题再去 embed / 检索 / 重排，让指代能命中正确分块；生成 Prompt 仍用原始问题 + 记忆块，由模型结合上下文理解，不改写结果。
- **折叠不丢历史**：只有记忆维护成功（LLM 返回可解析的摘要）才把消息标 `is_folded=true` 并折叠，绝不把历史折叠进空摘要；被折叠消息前端照常展示，只是不再进 LLM Prompt。
- **防幻觉**：Prompt 只包含检索命中的分块并强制要求标注 `[1]`、`[2]` 编号；无依据时直接返回 `no_evidence`，不调用模型编造。
- **可追溯**：`messages.sources`(jsonb) 记录每个回答引用的文档 / 分块 / 相似度 / 片段。
- **Provider 抽象**：`EMBEDDING_BASE_URL` / `LLM_BASE_URL` / `RERANK_BASE_URL` 可切换不同在线服务商，业务代码零改动。
- **鉴权挂载点**：鉴权作为 FastAPI 依赖挂在 `include_router(..., dependencies=[Depends(require_auth)])` 上，而非全局中间件——中间件得靠"排除"路径白名单来放行，新增开放接口时容易漏改；挂依赖则是"默认全保护、开放要显式写"，且 `/docs` 会自动出现 Authorize 按钮。配套一条**从 OpenAPI 取端点清单**的用例，任何新增接口只要忘了挂保护就会立刻变红。
- **验证码一次性 + 明文不出服务端**：校验时"先删再比"，无论对错都作废，重放与慢速爆破都失效；明文只存进程内存（120s TTL），下发的是 PNG 图片，响应体里不含答案。
- **不透明 token 而非 JWT**：随机串 + 服务端查表，可即时撤销、无签名依赖；代价是内存态——重启即全体登出、多 worker 不共享，本项目按单 worker 运行。

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

#### F2.6 上下文窗口管理（对话记忆）

多轮对话下自动压缩历史、抽取关键信息，控制进入大模型的历史 Token。

验收标准：
- 默认开启（`MEMORY_ENABLED=true`），可整开关闭，关闭后行为与纯单轮一致
- 每轮回答后抽取关键事实，写入 `conversations.key_facts`（可通过 psql 确认）
- 未折叠原文窗口估算 token 超过 `memory_recent_tokens`（默认 1200）时，触发滚动压缩：最旧几轮折叠进 `conversations.summary`，对应消息 `is_folded=true`，最近 `memory_recent_rounds`（默认 2）轮原文保留
- 多轮追问自动改写为自包含问题再检索（指代消解），改写结果不影响生成
- 压缩 / 抽取 / 改写任一失败均静默回退，不产生 `error` 事件、不影响主回答

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

- 多用户与角色权限（当前已实现单账号 + 内存 token 版本，见 F6）
- 无状态 JWT / Redis 会话存储（让 token 跨进程共享、支持多 worker）
- 登录失败次数限制与账号锁定
- 多知识库 / 文档分类
- 文档内容在线编辑
- 后台任务队列 Celery / Redis

### F6 登录与鉴权

> 原 F5 把「JWT 用户登录」列为可选扩展；本次已把它拉进范围，实现为**单账号 + 内存 token**（非 JWT），多用户/无状态会话仍留在 F5。

#### F6.1 登录页

- 账号、密码、4 位数字图片验证码三个输入项
- 验证码以图片形式展示，**点击图片可刷新**；登录失败后自动换一张并清空输入
- 登录页不显示顶部导航，整屏居中
- 验收：错误口令 / 错误验证码都有明确提示；验证码输入框回车可直接提交

#### F6.2 全站鉴权

- 除开放面（`/docs`、`/openapi.json`、`/api/health`、取验证码、登录）外，所有 `/api/*` 均需 `Authorization: Bearer <token>`
- 前端未登录访问任意业务页 → 自动重定向到登录页，并在登录后回跳原页面
- token 失效（过期 / 后端重启）时，任意请求的 401 都会清凭证并跳回登录页；**不出现"页面卡住无提示"**
- 验收：未登录直接访问 `/chat`、`/documents` 被拦到登录页；手工往 localStorage 塞假 token 也访问不了接口

#### F6.3 退出登录

- 顶部导航显示当前用户名与「退出登录」，二次确认后撤销 token、清除本地凭证并回到登录页
- 退出后浏览器后退无法绕过（守卫会再次拦下）

## 非功能需求

| 类别 | 要求 |
| --- | --- |
| 性能 | 单用户演示场景下，文档入库分钟级完成，问答首字响应 3 秒内 |
| 稳定性 | 上传失败、解析失败、模型调用失败都要有错误提示，不能导致服务崩溃 |
| 可维护性 | 使用 Alembic 管理表结构，配置集中在环境变量 |
| 可观测性 | 后端打印入库、检索、模型调用关键日志 |
| 安全 | 所有业务接口需登录（Bearer token）；登录需通过图片验证码；口令比较用常量时间函数。演示级实现，已知限制见「风险与注意事项」 |
| 测试 | 覆盖分块逻辑、检索接口、问答接口的 happy path（另有端到端验收脚本） |
| 文档 | README 包含启动步骤、环境变量说明、演示脚本 |

## 前端页面设计

- **登录页**：整屏居中卡片，账号 / 密码 / 验证码 + 登录按钮；验证码图片点击刷新；无顶部导航。
- **文档管理页**：顶部上传 / 刷新按钮；主体文档表格（文件名、类型、大小、状态、分块数、上传时间、操作）；操作含查看分块、删除、失败重试。
- **问答页**：左侧会话列表（新建、切换、删除）；右侧消息流（用户问题与助手回答交替）；回答下方来源卡片（文档名 + 片段）；底部输入框 + 发送按钮，Enter 发送；回答过程中输入框禁用，展示"正在生成"状态。
- 页面状态直接放在组件内（无全局 store），导航切换会重置内存状态，数据以服务端为准。唯一例外是**登录凭证**，放 `src/auth/token.ts` 模块级 —— 它是所有请求共享的传输层凭据，不是响应式 UI 状态。

## Demo 验收清单

1. 启动 PostgreSQL、后端、前端
2. 未登录直接访问 `/chat` 或 `/documents`，被重定向到登录页
3. 在登录页输入 `zhuliang / zhuliang` + 图中验证码，登录成功并回跳原页面；点验证码图片能换一张
4. 登录后刷新页面仍是登录态；退出登录后回到登录页，且浏览器后退无法绕过
5. 上传一份 PDF 和一份 Markdown，状态变为 `ready`
6. 在数据库中确认 `documents`、`chunks` 和向量已写入
7. 提问文档内的问题，得到带来源的流式回答
8. 提问无关内容，得到"没有足够依据"的提示
9. 新建、切换、删除会话，刷新页面后历史仍存在
10. 删除一个文档后，再提问相关内容，答案中不再引用它
11. 停止并重启服务，数据仍存在，证明 PostgreSQL 持久化
12. 重启后端后（token 存在内存、随重启失效），前端任意操作干净地跳回登录页，**不出现"页面卡住无提示"**
13. 打开 `/docs`，FastAPI 自动生成的接口文档可正常访问（未登录也应能打开）
14. 连续追问（"分块策略默认参数？" → "那 overlap 呢？"），第二条能正确检索并回答（改写生效）
15. 长对话后 psql 确认 `conversations.key_facts` 有事实、早期 `messages.is_folded=true`、`conversations.summary` 非空

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
10. **上下文窗口管理**：长对话为什么不能把全量原文都塞进 Prompt——Token 随轮数线性增长且有效上下文有限；如何用「滚动摘要 + 关键事实 + 最近原文窗口」把历史压成有界，用改写解决追问指代消解，并解释为什么压缩/抽取/改写都设计成非致命降级、以及「折叠绝不丢历史」的取舍
11. **鉴权设计**：为什么用「不透明随机 token + 服务端查表」而不是 JWT（无签名依赖、可即时撤销，代价是内存态、多 worker 不共享）；验证码为什么**一次性**且明文只存服务端；口令比较为什么用 `hmac.compare_digest` 且要先编码成 bytes；为什么把鉴权挂在 `include_router` 上而不是全局中间件（避免"排除式"白名单漏改），以及怎么用一条**从 OpenAPI 取端点清单的用例**把"新增路由忘了挂保护"变成必现失败；最后讲 Bearer 放 header 而非 Cookie 的安全含义——天然免疫 CSRF，代价是 XSS 可窃取 token

## 建议开发顺序

1. 后端骨架：FastAPI + 数据库连接 + Alembic + `/api/health`
2. 文档上传与入库：解析、分块、Embedding、写 pgvector
3. RAG 问答接口：检索、Prompt、SSE 输出
4. 前端骨架：Vue 3 + 路由 + 状态管理 + 布局
5. 前端文档管理页
6. 前端问答页与会话管理
7. 登录页 + 全站鉴权（后端 `require_auth`、前端请求注入与 401 跳转）
8. 联调、错误处理、README、演示脚本

## 风险与注意事项

- Embedding / LLM / Rerank 均为在线接口，依赖网络与 API 配额，Demo 前要确认网络可用
- PDF 解析质量取决于扫描件还是文本型 PDF，演示素材优先选文本型
- 大文件入库不要阻塞请求，入库放后台任务或线程池
- SSE 流中断要能结束前端 loading 状态
- 删除文档要同时清理向量，避免脏数据
- 不要把 API Key 写进前端代码

**鉴权相关的已知限制**（演示定位下有意接受，真实场景必须处理）：

- **账号口令硬编码且明文比对**：`zhuliang / zhuliang` 写在 `config.py` 默认值里。任何拿到源码的人都能登录；真实场景应改为用户表 + 哈希存储（bcrypt / argon2）。
- **明文 HTTP**：本地演示走 localhost 无妨；对外部署必须加 TLS，否则口令与 token 会被嗅探。
- **token 存内存**：**后端一重启全体登出**，且 `uvicorn --workers > 1` 时各 worker 不共享（A 签发的 token 到 B 校验必然 401）。本项目按单 worker 运行（启动命令不带 `--workers`）。要持久化/共享应换 Redis 或 DB 表。
- **无登录失败锁定**：验证码把单次猜测的成本抬到"必须重新取一张图"，但没有 IP 维度的失败计数与锁定，弱口令仍可被慢速爆破。
- **`AUTH_CAPTCHA_BYPASS=true` 是后门**：开启后取验证码接口会回显明文，验证码形同虚设。默认关闭，仅本地自动化验收使用，后端启动会打 warning。
- **token 存 localStorage**：XSS 可窃取。本仓库无 `v-html`、无第三方脚本，XSS 面基本为零；若要进一步收紧可换 httpOnly Cookie，但需配套 SameSite / CSRF token。

## 环境要求

- Windows 11，conda（`langchain` 环境），Python 3.13
- PostgreSQL 18 + pgvector（`scripts/install_pgvector.ps1` 提供预编译安装）
- 无需本地模型，配置 `EMBEDDING_API_KEY`（阿里云 DashScope）即可在线向量化
