# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Enterprise knowledge-base Q&A system (企业知识库问答系统) at `webprogram/rag-konwledge`, built as an interview demo. FastAPI backend + RAG pipeline (PostgreSQL 18 + pgvector 0.8.6, online 千问 embedding `text-embedding-v4` via 阿里云 DashScope, DeepSeek LLM via OpenAI-compatible API) plus a Vue 3 frontend. 全部业务接口需登录（内存 token + Bearer；图片验证码）。Code comments and the README are in Chinese.

`README.md` is the spec of record (~27 KB): 技术栈 / 目录结构 / API 一览 / SSE 协议 / 系统设计 / **功能需求 F1–F6 与验收标准** / 面试讲解点. `setup.md` is the step-by-step setup tutorial. Read the relevant README section before adding a feature, and update it when behavior changes.

## Environment / setup

- Windows 11, conda env `langchain` (Python 3.13), PostgreSQL 18 on `localhost:5432` (user `postgres`).
- Copy `.env.example` → `.env`; at minimum set `LLM_API_KEY` and `EMBEDDING_API_KEY`. `.env` is gitignored.
- One-time pgvector install: `powershell -ExecutionPolicy Bypass -File scripts/install_pgvector.ps1` (prebuilt DLL, no compiler).

### Machine gotchas (this Windows box)
- `httpx` → `localhost` returns HTTP 502 — a machine quirk, not an app bug. Use `urllib` or `curl` for local API tests (`scripts/e2e_verify.py` does).
- `conda run` mangles UTF-8 output (GBK console → UnicodeEncodeError). Use the env python directly (`"C:/ProgramData/miniconda3/envs/langchain/python.exe"`) with `PYTHONIOENCODING=utf-8`.
- Chinese text in `curl -d` garbles on the GBK console → 400 "error parsing the body". Send JSON via Python or the `/docs` UI.
- `alembic.ini` must stay pure ASCII (configparser reads it with the GBK locale and crashes on non-ASCII).
- **Smart App Control (智能应用控制) intermittently blocks unsigned binaries in the conda env** (`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy` → `VerifiedAndReputablePolicyState = 1` = enforcement; it flipped from `2`/evaluation on its own). Symptom seen 2026-09-17: `import numpy` (`_umath_linalg`), `orjson`, `sqlalchemy.cyextension` fail with `ImportError: DLL load failed ... 应用程序控制策略已阻止此文件`, which cascades to **`fastapi`** → `uvicorn app.main:app` won't boot and `pytest` collects only 8 of 32 tests (`test_chunking` / `test_document_service` pass; the other four error via `retrieval` → `rank_bm25` → `numpy`) — 那个 32 是事发当天的用例数（套件规模后来已翻倍，别再对数字），症状表现为新用例文件同样整片 collect error 而非断言失败。 **Diagnose** (don't guess) with `Get-WinEvent -LogName 'Microsoft-Windows-CodeIntegrity/Operational'` → event 3077 names the blocked file and the policy; SAC's policy ID is `{0283ac0f-fff1-49ae-ada1-8a933130cad6}` (`VerifiedAndReputableDesktop`).
  **It self-heals**: the block is reputation-based (ISG), keyed on the file's hash/prevalence — the blocked binaries are `NotSigned` (both the conda and the PyPI builds, verified via `Get-AuthenticodeSignature`), so switching a package from conda to a PyPI wheel is **not** a remedy; it was tested and the hypothesis was falsified. Minutes after the block, the *same* files imported fine with no policy or env change — **first move is always to just retry**. Expect a possible one-off block right after installing/upgrading a package that ships new binary wheels; it clears on retry. The only durable fix is turning SAC off (Windows 安全中心 → 应用和浏览器控制) — **one-way, cannot be re-enabled without reinstalling Windows, so it needs the user's explicit consent; never do it unilaterally.** **Never "fix" this in app code**, and don't chase it as a code bug: the app, the DB (`postgres.exe` runs fine — its July block events are stale) and the deps are all correct.

## Commands

启动教程见根目录 `setup.md`（后端纯命令，不用 ps1 脚本）。

- 前端: `cd frontend && pnpm install && pnpm dev`（:5173，`/api` 代理到 :8000）· `pnpm build`（`vue-tsc -b && vite build`）
- 后端（先 `conda activate langchain`，设 `$env:PYTHONIOENCODING="utf-8"` 避开 conda run 的 GBK 崩溃）:
  - 初始化+迁移（幂等）: `python scripts/init_db.py` 再 `python -m alembic upgrade head`
  - 启动: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
  - 测试: `python -m pytest -q`（78 tests; 独立 `rag_kb_test` DB, 不触网/不调模型）
  - 单个测试: `python -m pytest tests/test_chat.py::test_chat_no_evidence_when_nothing_retrieved -q`
  - E2E 验收（服务须已启动; 真实模型调用）: `python scripts/e2e_verify.py`。这是跨进程黑盒脚本，读不出图片验证码，**服务须以 `AUTH_CAPTCHA_BYPASS=true` 启动**（此时 `/api/auth/captcha` 额外返回明文 `code`）；没开则脚本打印提示并以退出码 2 结束。

## Architecture

### Backend layers
- `app/api/` — FastAPI routers (`/api` prefix): `documents` (upload/list/detail/rename/delete/retry), `chat` (`POST /api/chat`, SSE), `conversations`, `health`, `auth` (`/api/auth/*`). `deps.py` 放 `require_auth`（见下方鉴权不变量）。
- `app/services/` — core business logic:
  - `document_service.ingest_document` — async pipeline parse → chunk → embed → write pgvector chunks. State machine `pending → processing → ready | failed`; failed docs retry via `POST /api/documents/{id}/retry`.
  - `chat_service.chat_events` — async generator yielding SSE event dicts (`chunk` / `sources` / `no_evidence` / `error` / `done`). Flow: get-or-create conversation → **对话记忆**（`memory_enabled` 时: 未折叠窗口超 `memory_recent_tokens` 预算 → `maintain_memory` 把最旧几轮折叠进 `conversations.summary` 并标 `is_folded=true`；`rewrite_enabled` 且已有历史 → `rewrite_question` 把追问改写为自包含问题，**只用于检索**）→ embed **改写后问题** → `search_chunks` (混合召回 + RRF) → **可选在线重排**（`settings.rerank_enabled` 开启时先召回 `rerank_candidates` 再 `_rerank_hits` 收窄到 `top_k`；重排**非致命**，失败回退原序）→ if no hits emit `no_evidence` (never let the LLM hallucinate) → stream LLM tokens（Prompt = 摘要 + 关键事实 + 最近原文窗口 + 检索片段 + **原始问题**）→ persist user+assistant messages with `sources` → **每轮抽取**（`memory_extract_every_turn` 时 `maintain_memory` 更新 `conversations.key_facts`）。签名可注入 `memory_enabled` / `rewrite_enabled` / `memory_extract` 覆盖 settings（测试用）。压缩/抽取/改写全部**非致命**——LLM 失败静默回退，绝不发 `error`；**折叠只在 `maintain_memory` 成功（`ok and summary`）时执行**，避免把历史折叠进空摘要。
  - `memory.estimate_tokens` — CJK 感知 token 估算（仓库无 tiktoken，纯启发式: CJK 每字 1 token、其余 4 字符 1 token），只用于压缩触发预算判断。
  - `memory.maintain_memory` — 一次 LLM 调用同时做**滚动摘要 + 关键事实抽取**，返回 `(ok, summary, facts)`；ok=False（调用失败或 JSON 不可解析）时 summary/facts 为原值，调用方据此决定是否折叠。输出格式约束见 `SYSTEM_MAINTAIN`。
  - `memory.rewrite_question` — 追问 → 自包含问题（指代消解），只用于检索；失败返回原问题。
  - `retrieval.search_chunks` — 混合检索统一入口: 两路各召回 `bm25_recall_k` 候选——语义 (pgvector cosine, `similarity = 1 - distance`, filter `distance <= 1 - threshold`) + 关键词 (jieba + BM25)，RRF `score = Σ 1/(k+rank)` 融合取 `top_k`。只保留混合一种方式，无模式参数。注意 **BM25 分数可为负**（词出现在所有文档时 idf<0），命中判据按"是否命中查询词"，不能用 `s > 0`。
  - `chunking.chunk_sections` — section-aware, splits at sentence boundaries (`chunk_size` / `overlap`), merges section metadata (pages, heading).
  - `parsing.parse_file` — txt/md/pdf/docx → `list[Section]` (md by heading, pdf by page).
  - `auth` — 登录会话与验证码，**纯进程内存**（两个模块级 dict：`_token_store: token -> 过期戳`、`_captcha_store: captcha_id -> (明文code, 过期戳)`）。`issue_token` / `verify_token` / `revoke_token` / `authenticate`（`hmac.compare_digest` 常量时间比较，防时序侧信道）/ `new_captcha` / `verify_captcha`。**前提与限制**：token 与验证码都不落库，故 ①**重启后端即全体登出**（前端表现为干净跳登录页）；②多 worker 不共享 —— 本 Demo 固定单 worker。过期项惰性清理（读时判定 + 签发时顺带扫一遍），不会无界增长。要持久化就把 `_token_store` 换成 DB 表。
  - `captcha.render` — Pillow 画 120×44 PNG 四位数字（随机字号/颜色/偏移 + 干扰线噪点），返回 `data:image/png;base64,...`。`generate_code` 用 `secrets.choice` 而非 `random`（后者是梅森旋转，可预测）。字体按 `C:\Windows\Fonts\{arial,consola,segoeui}.ttf` → `ImageFont.load_default(size=)` 回退，换机器不炸。**明文只存服务端**，响应体绝不含 code。
- `app/providers/` — `Embedder` (OpenAI-compatible, 千问 DashScope), `LLM` (OpenAI-compatible) and `Reranker` (DashScope `qwen3-rerank`, httpx POST `{rerank_base_url}/reranks`) protocol abstractions. `get_embedder()` / `get_llm()` / `get_reranker()` factories (lru_cached); provider is switched via `EMBEDDING_BASE_URL`/`LLM_BASE_URL`/`RERANK_BASE_URL` env config with zero business-code changes. No local model code.
- `app/models.py` — `Document` / `Chunk` / `Conversation` / `Message`. `Chunk.embedding` is a pgvector `Vector(settings.embedding_dim)` column; `chunk_metadata` maps to DB column `metadata` (avoids a Declarative reserved name). `Conversation.summary` (Text) / `Conversation.key_facts` (JSONB) 与 `Message.is_folded` (Boolean) 是对话记忆列，仅后端 Prompt 使用、前端不展示。
- `app/database.py` — engine, `SessionLocal`, `Base`, `get_db`.
- `app/config.py` — pydantic-settings, all config from `.env` / env vars. 含 `# ---- 对话记忆 ----` 配置组 (`memory_enabled` / `memory_recent_tokens` / `memory_recent_rounds` / `memory_max_facts` / `memory_extract_every_turn` / `memory_rewrite_enabled`) 与 `# ---- 登录鉴权 ----` 配置组 (`auth_username` / `auth_password` 默认即演示账号 `zhuliang`，`auth_token_ttl_minutes` / `captcha_ttl_seconds` / `auth_captcha_bypass`)。账号口令写在配置默认值里等价于"写死"，但**不散落在业务代码中**，`.env` 可覆盖。
- `alembic/versions/` — `0001_initial.py` (4 tables, `Vector(512)`), `0002_hnsw_index.py` (HNSW cosine index on `chunks.embedding`), `0003_message_retrieval_mode.py` + `0004_drop_message_retrieval_mode.py` (曾记录 `messages.retrieval_mode`，检索固定混合后已删除该列), `0005_conversation_memory.py` (加 `conversations.summary` / `key_facts` / `messages.is_folded`).

### Backend invariants / gotchas
- **鉴权挂载点（新增路由不会漏保护）**: 保护是**逐路由显式声明**的，不在中间件里做路径白名单——白名单要"排除"路径，新增开放路由时容易误伤。`app/main.py` 用 `protected = [Depends(require_auth)]`，只给 `documents` / `chat` / `conversations` 三个 `include_router` 挂 `dependencies=protected`；`health` 与 `auth` 不挂（开放面见 `tests/test_auth.py` 的 `PUBLIC_ENDPOINTS = {GET /api/health, GET /api/auth/captcha, POST /api/auth/login}`，`/docs` 与 `/openapi.json` 也开放）。**新增业务路由时挂到这三个 router 上即自动受保护**；要新开一个开放接口，得同时改 main.py 和那份白名单，两处不一致会被 `test_only_whitelisted_endpoints_are_reachable_without_login` 抓住。
- **router 级依赖在 body 校验与 `get_db` 之前解析**：裸请求打任何受保护接口都会**先**返回 401，不碰 DB、不校验 body——所以「未登录」和「参数错」不会混淆，前端只认 401 跳登录。
- **`require_auth` 依赖 `HTTPBearer(auto_error=False)`**：带回里缺头/格式错都统一 401，而不是 FastAPI 默认的 403（前端只认 401）。用 `HTTPBearer` 而非手撸 header 的额外好处是 `/docs` 会自动出现 Authorize 按钮。`require_auth` 返回的是**裸 token 串**（`logout` 要用它去撤销）。
- **DB session lifetime**: async background work (document ingest, SSE chat streaming) must open its own `SessionLocal()`, because the request-scoped `get_db` session closes when the request returns. See `_run_ingest` in `documents.py` and `event_stream` in `chat.py`.
- **Embedding 单请求条数上限是探测出来的，不是配置项（曾导致 PDF 入库必失败）**: DashScope `text-embedding-v3/v4` 对**单请求条数**有硬上限 **10**，与 token 多少无关，超了返回 `400 InternalError.Algo.InvalidParameter: Value error, batch size is invalid, it should not be larger than 10.`。`document_service.ingest_document` 一次性把全部 chunk 交给 `embed_documents`，所以**短文档（≤10 chunk）能过、PDF 必炸**——这个"小文件正常、大文件报错"的分裂现象是该 bug 的特征。**修法是运行时探测，不是加配置**：`OpenAICompatEmbedder` 维护 `_ok`（已知可行的最大条数）/ `_too_big`（已知被拒的最小条数），首次整批发，被拒就取中点逼近 —— 200 条文本的实测探测序列是 `200,100,50,25,12,6,9,10,11` 然后稳定在 10，共 6 次试探请求，**每个进程只付一次**，之后（含第二次入库）零探测开销。刻意**不做成 `EMBEDDING_BATCH_SIZE` 配置**：各厂商上限不同（v1/v2 是 25、OpenAI 是 2048），一个"多数人永远不该改、改大了复现同一个 bug"的旋钮不如不暴露。关键性质：① 只有被识别为条数超限的错误才重试（`_is_batch_limit_error` 按关键词族匹配，识别不了原样抛出，不吞异常）；② 单条仍被拒则直接抛，这是重试循环的终止条件；③ `_ok`/`_too_big` 单调，多线程并发最坏只是多做一次探测，无需加锁。回归用例 `tests/test_embedder_batching.py`（12 条，含"收敛到真实上限而非减半近似""第二次调用零探测开销""小文档先行不锁死批大小""无法识别的 400 不被误重试"）。
- **Embedding-dim coupling**: `EMBEDDING_DIM` (`.env`) must equal the model's dim and the `Vector(...)` column. `OpenAICompatEmbedder` explicitly passes `dimensions=EMBEDDING_DIM` because online models default higher (Qwen `text-embedding-v3/v4` default 1024) — forgetting this inserts 1024-dim vectors into the 512 column and fails. Changing the embedding model requires updating both and rebuilding tables (drop + `alembic upgrade head`); even with the same dim, **vectors from different models live in different spaces — re-ingest documents after switching models**.
- **Cascades are DB-level** (`ON DELETE CASCADE`); ORM relationships use `passive_deletes=True`.
- Tests inject `FakeEmbedder` (deterministic 8-dim char-hash vectors), `FakeLLM` and `FakeReranker` directly into service calls — no network, no model download. `tests/conftest.py` sets env vars (incl. `rag_kb_test` DB, `RERANK_ENABLED=true`, `MEMORY_ENABLED=false`, `AUTH_USERNAME`/`AUTH_PASSWORD`, `AUTH_CAPTCHA_BYPASS=false`) before any `app` import because `Settings` is a process-level cached singleton. Rerank is gated on `settings.rerank_enabled and settings.rerank_api_key`, so an empty key silently skips it. 记忆默认关闭让既有用例保持纯单轮（恰好 1 次 LLM 调用）；`tests/test_memory.py` 用 `_ScriptedLLM`（按调用序号返回预设 token、可指定某次抛异常、记录 messages/temperature）并**显式传 `memory_enabled=True`** 开启记忆链路。注意 `_load_unfolded` 排序键是 `(created_at, id)`，同一 commit 插入的消息 `created_at` 相同、次级键是随机 UUID → 顺序不定；测试里种子多条消息时要显式给递增 `created_at`。
- **鉴权测试的两个陷阱**：
  - `hmac.compare_digest` 传 `str` 时**只接受 ASCII**，含中文会抛 `TypeError` → 500。比较前必须 `.encode("utf-8")`；`test_login_non_ascii_credentials_return_401_not_500` 守着这条回归。两个字段都要比（`user_ok and password_ok`，不短路），否则用户名错得快、密码错得慢，等于把时序侧信道换个位置。
  - `verify_captcha` 必须**先 pop 再比对**（一次性、防重放）。若改成"比对了再删"，口令错时验证码不会被消费，同一张码就能拿来做口令爆破。
  - `tests/test_auth.py` 的 `_all_api_endpoints()` 取自 `app.openapi()["paths"]` 而**不是遍历 `app.routes`**：FastAPI 0.141 起 `include_router` 的结果被包成内部的 `_IncludedRouter`，子路由不再摊平进 `app.routes`，按路由树遍历会得到一个静默失效的空网。这个用例是行为式的（真发请求断言 401），比内省依赖树更强也更抗版本变化。
  - `anon_client` 与 `auth_client` 是**两个独立的 TestClient**：`auth_client` 不能复用 `anon_client` 再往它的 `headers` 里塞 token，否则同时请求两个 fixture 时「匿名」那个已经不匿名了。`test_documents_api.py` 的 3 个重命名用例用的是 `auth_client`。

### Frontend
- `src/router/` — `/` landing, `/chat` chat, `/documents` KB management, `/login` 登录（`meta: { public: true, hideHeader: true }`）。`beforeEach` 守卫：非 `public` 且未登录 → `/login?redirect=<fullPath>`；已登录访问 `/login` → 跳 `/`。`hideHeader` 这个 meta 约定由 `App.vue` 消费（`v-if="!route.meta.hideHeader"`）——登录页要整屏居中卡片，不需要 AppHeader。
- `src/api/` — `http.ts` axios wrapper (timeout 30s, response interceptor throws `ApiError` with backend `detail`, methods unwrap `r.data`); `chat.ts` streams SSE via `@microsoft/fetch-event-source` (native `EventSource` only supports GET, can't carry the POST body). No auto-reconnect — reconnecting would re-ask the model. REST wrappers: `documents.ts`, `conversations.ts`, `auth.ts`。
- `src/auth/` — 目前只有 `token.ts`（登录凭据的存取 + 401 失效跳转），**不依赖 api/ 或 router/**。它和 `views/`、`components/` 平级，是"传输层凭据"这一层，不是页面状态层。
- **鉴权的四条约定**（`token.ts` 已独立到 `src/auth/`，不再属于 api 层）：
  - `src/auth/token.ts` 是**无依赖的叶子模块**（`import` 列表为空：只有 localStorage 读写 + `redirectToLogin()`）。`api/http.ts` / `api/chat.ts` / `api/auth.ts` / `router/index.ts` / `views/login/` / `AppHeader.vue` 都从它取 token。这条依赖方向是刻意的：`http.ts → auth/token.ts`，而 `api/auth.ts → http.ts`；若把 token 存取写进 `api/auth.ts` 就会形成 `http ↔ auth` 循环导入。**它独立成 `auth/` 一层（而非留在 `api/`）的理由**：`api/` 下几个文件都是同构的"薄 http 包装"（documents / conversations / auth），而 token 管的是**凭据本身**且带导航副作用，不是同类东西。⚠️ 维护约束：该文件**不得 import 任何业务模块**，这条边一旦反向循环依赖立刻回来。
  - **只有两个请求注入点**：`http.ts` 的 request 拦截器（覆盖全部 axios 请求）和 `chat.ts`（全站唯一绕过 axios 的请求，headers 是写死的字面量，必须单独注入）。**新增绕过 axios 的请求时别忘了这一处**。
  - response 拦截器的 401 分支**排除 `/api/auth/login` 自身**，否则密码输错会触发跳转、还丢掉错误提示。跳转用 `window.location.href`（不走 router），避免 `http.ts → router → views → api` 的循环依赖，代价是整页刷新——会话过期场景可接受。`token.ts` 里有模块级 `redirecting` 标志去抖，避免多个并发 401 重复跳转。
  - `isUnauthorized(e)` 从 `http.ts` 导出：401 交给全局跳转，页面**不要**再弹提示，否则和跳转打架。
- **No global state store** (no Pinia / composables — explicit user preference). Page state lives directly in each page's `index.vue` as plain `ref`s; child components are presentational (props down, emits up). Chat state + conversation list live in `views/chat/index.vue` (`ConversationPanel.vue` takes `conversations`/`loading`/`activeId` props, emits `select`/`new`/`delete`); document list + polling in `views/documents/index.vue` (`DocumentDetailDialog.vue` calls `getDocument()` directly). Consequence: navigating between pages resets in-memory view state — conversations/documents persist server-side, re-select to reload.
- **`pushMessage` must return the reactive proxy** `messages.value[messages.value.length - 1]`, not the raw pushed object — mutating the raw object bypasses Vue's reactive `set` trap and streamed chunks never re-render. 检索固定混合模式，聊天页头部无模式切换控件。
- **The message list is virtualized** (`@tanstack/vue-virtual` in `views/chat/index.vue`) for long conversations. `useVirtualizer` gets `count: messages.length`, `getScrollElement: () => scrollbarRef.value?.wrapRef`, `estimateSize` by role (`USER_MSG_EST` / `ASSISTANT_MSG_EST`), `overscan: 5`, `getItemKey` by message id. Rows render absolutely positioned (`translateY(row.start)`) inside a spacer sized `virtualizer.getTotalSize()`, and each row is measured with `:ref="el => virtualizer.measureElement(el)"` — so rendering depends on row heights being measured, and **"scroll to bottom" must call `virtualizer.scrollToIndex(last, {align: 'end'})`**, not `scrollTop = scrollHeight`. Streaming only auto-follows while already near the bottom (翻上历史时不被拽回).
- **Element Plus on-demand rules**: `ElMessage` / `ElMessageBox` must be used *without* explicit import so `unplugin-auto-import` injects their style side-effects (explicit imports skip styles); `el-table` `v-loading` requires `ElementPlusResolver({ directives: true })`.
- **异步调用不许裸奔 / 静默吞异常**：加了 401 之后，任何没有 catch 的 await 在会话过期时会变成未处理的 promise rejection，用户看到的是"页面莫名不动"。已修的两处值得记住同类位置：`views/documents/index.vue` 的 `refresh()`（被 `onMounted` 和 1.5s 轮询调用，401 要 `stopAutoRefresh()`，否则变成 1.5 秒一次的错误风暴）、`views/chat/index.vue` 的 `refreshConversations()` 与 `loadConversation()`。原则：**401 交给全局跳转（不弹提示），其余错误才 `ElMessage`**。
- **Page structure convention**: page = `views/<page>/index.vue`; page-private components in `views/<page>/components/`; only cross-page-shared components live in top-level `components/` (currently just `AppHeader`, which `onMounted` 调 `fetchMe()` 既取用户名、也顺带校验存量 token 是否还有效，并渲染用户名 + 退出登录)。Imports use the `@/` alias (`@` → `src`).

## SSE chat protocol (`POST /api/chat`)

需要 `Authorization: Bearer <token>`（和其它业务接口一样）；前端 `chat.ts` 单独注入这个头。Request `{question, conversation_id?}` → `text/event-stream`, one `data: {...}` per event (blank-line separated). Events: `chunk` (token increment), `sources` (`[{document_id, filename, chunk_id, chunk_index, similarity, snippet}]`), `no_evidence`, `error`, `done` (`{conversation_id}` — reuse it to continue the conversation). 检索固定混合模式（关键词 BM25 + 语义向量，RRF 融合），无模式参数。Omitting `conversation_id` auto-creates a conversation titled by the question's first 30 chars.

多轮追问时后端对检索问题做**改写**（指代消解，只影响检索、不影响生成）；长对话自动做**滚动压缩 + 每轮事实抽取**。记忆维护调用全部**非致命**——失败静默回退、不发 `error` 事件、不影响主回答。
