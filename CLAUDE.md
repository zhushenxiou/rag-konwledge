# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Enterprise knowledge-base Q&A system (企业知识库问答系统) at `webprogram/rag-konwledge`, built as an interview demo. FastAPI backend + RAG pipeline (PostgreSQL 18 + pgvector 0.8.6, online 千问 embedding `text-embedding-v4` via 阿里云 DashScope, DeepSeek LLM via OpenAI-compatible API) plus a Vue 3 frontend. Code comments and the README are in Chinese.

`README.md` is the spec of record (~27 KB): 技术栈 / 目录结构 / API 一览 / SSE 协议 / 系统设计 / **功能需求 F1–F5 与验收标准** / 面试讲解点. `setup.md` is the step-by-step setup tutorial. Read the relevant README section before adding a feature, and update it when behavior changes.

## Environment / setup

- Windows 11, conda env `langchain` (Python 3.13), PostgreSQL 18 on `localhost:5432` (user `postgres`).
- Copy `.env.example` → `.env`; at minimum set `LLM_API_KEY` and `EMBEDDING_API_KEY`. `.env` is gitignored.
- One-time pgvector install: `powershell -ExecutionPolicy Bypass -File scripts/install_pgvector.ps1` (prebuilt DLL, no compiler).

### Machine gotchas (this Windows box)
- `httpx` → `localhost` returns HTTP 502 — a machine quirk, not an app bug. Use `urllib` or `curl` for local API tests (`scripts/e2e_verify.py` does).
- `conda run` mangles UTF-8 output (GBK console → UnicodeEncodeError). Use the env python directly (`"C:/ProgramData/miniconda3/envs/langchain/python.exe"`) with `PYTHONIOENCODING=utf-8`.
- Chinese text in `curl -d` garbles on the GBK console → 400 "error parsing the body". Send JSON via Python or the `/docs` UI.
- `alembic.ini` must stay pure ASCII (configparser reads it with the GBK locale and crashes on non-ASCII).
- **Smart App Control (智能应用控制) intermittently blocks unsigned binaries in the conda env** (`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy` → `VerifiedAndReputablePolicyState = 1` = enforcement; it flipped from `2`/evaluation on its own). Symptom seen 2026-09-17: `import numpy` (`_umath_linalg`), `orjson`, `sqlalchemy.cyextension` fail with `ImportError: DLL load failed ... 应用程序控制策略已阻止此文件`, which cascades to **`fastapi`** → `uvicorn app.main:app` won't boot and `pytest` collects only 8 of 32 tests (`test_chunking` / `test_document_service` pass; the other four error via `retrieval` → `rank_bm25` → `numpy`). **Diagnose** (don't guess) with `Get-WinEvent -LogName 'Microsoft-Windows-CodeIntegrity/Operational'` → event 3077 names the blocked file and the policy; SAC's policy ID is `{0283ac0f-fff1-49ae-ada1-8a933130cad6}` (`VerifiedAndReputableDesktop`).
  **It self-heals**: the block is reputation-based (ISG), keyed on the file's hash/prevalence — the blocked binaries are `NotSigned` (both the conda and the PyPI builds, verified via `Get-AuthenticodeSignature`), so switching a package from conda to a PyPI wheel is **not** a remedy; it was tested and the hypothesis was falsified. Minutes after the block, the *same* files imported fine with no policy or env change — **first move is always to just retry**. Expect a possible one-off block right after installing/upgrading a package that ships new binary wheels; it clears on retry. The only durable fix is turning SAC off (Windows 安全中心 → 应用和浏览器控制) — **one-way, cannot be re-enabled without reinstalling Windows, so it needs the user's explicit consent; never do it unilaterally.** **Never "fix" this in app code**, and don't chase it as a code bug: the app, the DB (`postgres.exe` runs fine — its July block events are stale) and the deps are all correct.

## Commands

启动教程见根目录 `setup.md`（后端纯命令，不用 ps1 脚本）。

- 前端: `cd frontend && pnpm install && pnpm dev`（:5173，`/api` 代理到 :8000）· `pnpm build`（`vue-tsc -b && vite build`）
- 后端（先 `conda activate langchain`，设 `$env:PYTHONIOENCODING="utf-8"` 避开 conda run 的 GBK 崩溃）:
  - 初始化+迁移（幂等）: `python scripts/init_db.py` 再 `python -m alembic upgrade head`
  - 启动: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
  - 测试: `python -m pytest -q`（32 tests; 独立 `rag_kb_test` DB, 不触网/不调模型）
  - 单个测试: `python -m pytest tests/test_chat.py::test_chat_no_evidence_when_nothing_retrieved -q`
  - E2E 验收（服务须已启动; 真实模型调用）: `python scripts/e2e_verify.py`

## Architecture

### Backend layers
- `app/api/` — FastAPI routers (`/api` prefix): `documents` (upload/list/detail/rename/delete/retry), `chat` (`POST /api/chat`, SSE), `conversations`, `health`.
- `app/services/` — core business logic:
  - `document_service.ingest_document` — async pipeline parse → chunk → embed → write pgvector chunks. State machine `pending → processing → ready | failed`; failed docs retry via `POST /api/documents/{id}/retry`.
  - `chat_service.chat_events` — async generator yielding SSE event dicts (`chunk` / `sources` / `no_evidence` / `error` / `done`). Flow: get-or-create conversation → **对话记忆**（`memory_enabled` 时: 未折叠窗口超 `memory_recent_tokens` 预算 → `maintain_memory` 把最旧几轮折叠进 `conversations.summary` 并标 `is_folded=true`；`rewrite_enabled` 且已有历史 → `rewrite_question` 把追问改写为自包含问题，**只用于检索**）→ embed **改写后问题** → `search_chunks` (混合召回 + RRF) → **可选在线重排**（`settings.rerank_enabled` 开启时先召回 `rerank_candidates` 再 `_rerank_hits` 收窄到 `top_k`；重排**非致命**，失败回退原序）→ if no hits emit `no_evidence` (never let the LLM hallucinate) → stream LLM tokens（Prompt = 摘要 + 关键事实 + 最近原文窗口 + 检索片段 + **原始问题**）→ persist user+assistant messages with `sources` → **每轮抽取**（`memory_extract_every_turn` 时 `maintain_memory` 更新 `conversations.key_facts`）。签名可注入 `memory_enabled` / `rewrite_enabled` / `memory_extract` 覆盖 settings（测试用）。压缩/抽取/改写全部**非致命**——LLM 失败静默回退，绝不发 `error`；**折叠只在 `maintain_memory` 成功（`ok and summary`）时执行**，避免把历史折叠进空摘要。
  - `memory.estimate_tokens` — CJK 感知 token 估算（仓库无 tiktoken，纯启发式: CJK 每字 1 token、其余 4 字符 1 token），只用于压缩触发预算判断。
  - `memory.maintain_memory` — 一次 LLM 调用同时做**滚动摘要 + 关键事实抽取**，返回 `(ok, summary, facts)`；ok=False（调用失败或 JSON 不可解析）时 summary/facts 为原值，调用方据此决定是否折叠。输出格式约束见 `SYSTEM_MAINTAIN`。
  - `memory.rewrite_question` — 追问 → 自包含问题（指代消解），只用于检索；失败返回原问题。
  - `retrieval.search_chunks` — 混合检索统一入口: 两路各召回 `bm25_recall_k` 候选——语义 (pgvector cosine, `similarity = 1 - distance`, filter `distance <= 1 - threshold`) + 关键词 (jieba + BM25)，RRF `score = Σ 1/(k+rank)` 融合取 `top_k`。只保留混合一种方式，无模式参数。注意 **BM25 分数可为负**（词出现在所有文档时 idf<0），命中判据按"是否命中查询词"，不能用 `s > 0`。
  - `chunking.chunk_sections` — section-aware, splits at sentence boundaries (`chunk_size` / `overlap`), merges section metadata (pages, heading).
  - `parsing.parse_file` — txt/md/pdf/docx → `list[Section]` (md by heading, pdf by page).
- `app/providers/` — `Embedder` (OpenAI-compatible, 千问 DashScope), `LLM` (OpenAI-compatible) and `Reranker` (DashScope `qwen3-rerank`, httpx POST `{rerank_base_url}/reranks`) protocol abstractions. `get_embedder()` / `get_llm()` / `get_reranker()` factories (lru_cached); provider is switched via `EMBEDDING_BASE_URL`/`LLM_BASE_URL`/`RERANK_BASE_URL` env config with zero business-code changes. No local model code.
- `app/models.py` — `Document` / `Chunk` / `Conversation` / `Message`. `Chunk.embedding` is a pgvector `Vector(settings.embedding_dim)` column; `chunk_metadata` maps to DB column `metadata` (avoids a Declarative reserved name). `Conversation.summary` (Text) / `Conversation.key_facts` (JSONB) 与 `Message.is_folded` (Boolean) 是对话记忆列，仅后端 Prompt 使用、前端不展示。
- `app/database.py` — engine, `SessionLocal`, `Base`, `get_db`.
- `app/config.py` — pydantic-settings, all config from `.env` / env vars. 含 `# ---- 对话记忆 ----` 配置组 (`memory_enabled` / `memory_recent_tokens` / `memory_recent_rounds` / `memory_max_facts` / `memory_extract_every_turn` / `memory_rewrite_enabled`).
- `alembic/versions/` — `0001_initial.py` (4 tables, `Vector(512)`), `0002_hnsw_index.py` (HNSW cosine index on `chunks.embedding`), `0003_message_retrieval_mode.py` + `0004_drop_message_retrieval_mode.py` (曾记录 `messages.retrieval_mode`，检索固定混合后已删除该列), `0005_conversation_memory.py` (加 `conversations.summary` / `key_facts` / `messages.is_folded`).

### Backend invariants / gotchas
- **DB session lifetime**: async background work (document ingest, SSE chat streaming) must open its own `SessionLocal()`, because the request-scoped `get_db` session closes when the request returns. See `_run_ingest` in `documents.py` and `event_stream` in `chat.py`.
- **Embedding-dim coupling**: `EMBEDDING_DIM` (`.env`) must equal the model's dim and the `Vector(...)` column. `OpenAICompatEmbedder` explicitly passes `dimensions=EMBEDDING_DIM` because online models default higher (Qwen `text-embedding-v3/v4` default 1024) — forgetting this inserts 1024-dim vectors into the 512 column and fails. Changing the embedding model requires updating both and rebuilding tables (drop + `alembic upgrade head`); even with the same dim, **vectors from different models live in different spaces — re-ingest documents after switching models**.
- **Cascades are DB-level** (`ON DELETE CASCADE`); ORM relationships use `passive_deletes=True`.
- Tests inject `FakeEmbedder` (deterministic 8-dim char-hash vectors), `FakeLLM` and `FakeReranker` directly into service calls — no network, no model download. `tests/conftest.py` sets env vars (incl. `rag_kb_test` DB, `RERANK_ENABLED=true`, `MEMORY_ENABLED=false`) before any `app` import because `Settings` is a process-level cached singleton. Rerank is gated on `settings.rerank_enabled and settings.rerank_api_key`, so an empty key silently skips it. 记忆默认关闭让既有用例保持纯单轮（恰好 1 次 LLM 调用）；`tests/test_memory.py` 用 `_ScriptedLLM`（按调用序号返回预设 token、可指定某次抛异常、记录 messages/temperature）并**显式传 `memory_enabled=True`** 开启记忆链路。注意 `_load_unfolded` 排序键是 `(created_at, id)`，同一 commit 插入的消息 `created_at` 相同、次级键是随机 UUID → 顺序不定；测试里种子多条消息时要显式给递增 `created_at`。

### Frontend
- `src/router/` — `/` landing, `/chat` chat, `/documents` KB management.
- `src/api/` — `http.ts` axios wrapper (timeout 30s, response interceptor throws `ApiError` with backend `detail`, methods unwrap `r.data`); `chat.ts` streams SSE via `@microsoft/fetch-event-source` (native `EventSource` only supports GET, can't carry the POST body). No auto-reconnect — reconnecting would re-ask the model. REST wrappers: `documents.ts`, `conversations.ts`.
- **No global state store** (no Pinia / composables — explicit user preference). Page state lives directly in each page's `index.vue` as plain `ref`s; child components are presentational (props down, emits up). Chat state + conversation list live in `views/chat/index.vue` (`ConversationPanel.vue` takes `conversations`/`loading`/`activeId` props, emits `select`/`new`/`delete`); document list + polling in `views/documents/index.vue` (`DocumentDetailDialog.vue` calls `getDocument()` directly). Consequence: navigating between pages resets in-memory view state — conversations/documents persist server-side, re-select to reload.
- **`pushMessage` must return the reactive proxy** `messages.value[messages.value.length - 1]`, not the raw pushed object — mutating the raw object bypasses Vue's reactive `set` trap and streamed chunks never re-render. 检索固定混合模式，聊天页头部无模式切换控件。
- **The message list is virtualized** (`@tanstack/vue-virtual` in `views/chat/index.vue`) for long conversations. `useVirtualizer` gets `count: messages.length`, `getScrollElement: () => scrollbarRef.value?.wrapRef`, `estimateSize` by role (`USER_MSG_EST` / `ASSISTANT_MSG_EST`), `overscan: 5`, `getItemKey` by message id. Rows render absolutely positioned (`translateY(row.start)`) inside a spacer sized `virtualizer.getTotalSize()`, and each row is measured with `:ref="el => virtualizer.measureElement(el)"` — so rendering depends on row heights being measured, and **"scroll to bottom" must call `virtualizer.scrollToIndex(last, {align: 'end'})`**, not `scrollTop = scrollHeight`. Streaming only auto-follows while already near the bottom (翻上历史时不被拽回).
- **Element Plus on-demand rules**: `ElMessage` / `ElMessageBox` must be used *without* explicit import so `unplugin-auto-import` injects their style side-effects (explicit imports skip styles); `el-table` `v-loading` requires `ElementPlusResolver({ directives: true })`.
- **Page structure convention**: page = `views/<page>/index.vue`; page-private components in `views/<page>/components/`; only cross-page-shared components live in top-level `components/` (currently just `AppHeader`). Imports use the `@/` alias (`@` → `src`).

## SSE chat protocol (`POST /api/chat`)

Request `{question, conversation_id?}` → `text/event-stream`, one `data: {...}` per event (blank-line separated). Events: `chunk` (token increment), `sources` (`[{document_id, filename, chunk_id, chunk_index, similarity, snippet}]`), `no_evidence`, `error`, `done` (`{conversation_id}` — reuse it to continue the conversation). 检索固定混合模式（关键词 BM25 + 语义向量，RRF 融合），无模式参数。Omitting `conversation_id` auto-creates a conversation titled by the question's first 30 chars.

多轮追问时后端对检索问题做**改写**（指代消解，只影响检索、不影响生成）；长对话自动做**滚动压缩 + 每轮事实抽取**。记忆维护调用全部**非致命**——失败静默回退、不发 `error` 事件、不影响主回答。
