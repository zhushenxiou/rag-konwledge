# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Enterprise knowledge-base Q&A system (企业知识库问答系统) at `webprogram/rag-konwledge`, built as an interview demo. FastAPI backend + RAG pipeline (PostgreSQL 18 + pgvector 0.8.6, online 千问 embedding `text-embedding-v4` via 阿里云 DashScope, DeepSeek LLM via OpenAI-compatible API) plus a Vue 3 frontend. Code comments and the README are in Chinese.

## Environment / setup

- Windows 11, conda env `langchain` (Python 3.13), PostgreSQL 18 on `localhost:5432` (user `postgres`).
- Copy `.env.example` → `.env`; at minimum set `LLM_API_KEY` and `EMBEDDING_API_KEY`. `.env` is gitignored.
- One-time pgvector install: `powershell -ExecutionPolicy Bypass -File scripts/install_pgvector.ps1` (prebuilt DLL, no compiler).

### Machine gotchas (this Windows box)
- `httpx` → `localhost` returns HTTP 502 — a machine quirk, not an app bug. Use `urllib` or `curl` for local API tests (`scripts/e2e_verify.py` does).
- `conda run` mangles UTF-8 output (GBK console → UnicodeEncodeError). Use the env python directly (`"C:/ProgramData/miniconda3/envs/langchain/python.exe"`) with `PYTHONIOENCODING=utf-8`.
- Chinese text in `curl -d` garbles on the GBK console → 400 "error parsing the body". Send JSON via Python or the `/docs` UI.
- `alembic.ini` must stay pure ASCII (configparser reads it with the GBK locale and crashes on non-ASCII).

## Commands

Backend (run from repo root):
- Init DB: `conda run -n langchain python scripts/init_db.py` then `conda run -n langchain alembic upgrade head`
- Dev server: `powershell -ExecutionPolicy Bypass -File scripts/run_dev.ps1` (init + migrate + uvicorn on :8000), or `conda run -n langchain uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Tests: `conda run -n langchain python -m pytest -q` (26 tests; separate `rag_kb_test` DB, no network/model)
- Single test: `conda run -n langchain python -m pytest tests/test_chat.py::test_chat_no_evidence_when_nothing_retrieved -q`
- E2E acceptance (server must be running; real model calls): `conda run -n langchain python scripts/e2e_verify.py`

Frontend (`cd frontend`):
- `pnpm install` · `pnpm dev` (:5173, proxies `/api` → :8000) · `pnpm build` (runs `vue-tsc -b && vite build`)
- On a fresh clone run `pnpm build` once before `vue-tsc` — the Element Plus auto-import plugins generate `src/components.d.ts` / `src/auto-imports.d.ts` that type checking depends on.

## Architecture

### Backend layers
- `app/api/` — FastAPI routers (`/api` prefix): `documents` (upload/list/detail/rename/delete/retry), `chat` (`POST /api/chat`, SSE), `conversations`, `health`.
- `app/services/` — core business logic:
  - `document_service.ingest_document` — async pipeline parse → chunk → embed → write pgvector chunks. State machine `pending → processing → ready | failed`; failed docs retry via `POST /api/documents/{id}/retry`.
  - `chat_service.chat_events` — async generator yielding SSE event dicts (`chunk` / `sources` / `no_evidence` / `error` / `done`). Flow: get-or-create conversation → always embed question → `search_chunks` (混合召回 + RRF) → **可选在线重排**（`settings.rerank_enabled` 开启时先召回 `rerank_candidates` 再 `_rerank_hits` 收窄到 `top_k`；重排**非致命**，失败回退原序）→ if no hits emit `no_evidence` (never let the LLM hallucinate) → stream LLM tokens → persist user+assistant messages with `sources`.
  - `retrieval.search_chunks` — 混合检索统一入口: 两路各召回 `bm25_recall_k` 候选——语义 (pgvector cosine, `similarity = 1 - distance`, filter `distance <= 1 - threshold`) + 关键词 (jieba + BM25)，RRF `score = Σ 1/(k+rank)` 融合取 `top_k`。只保留混合一种方式，无模式参数。注意 **BM25 分数可为负**（词出现在所有文档时 idf<0），命中判据按"是否命中查询词"，不能用 `s > 0`。
  - `chunking.chunk_sections` — section-aware, splits at sentence boundaries (`chunk_size` / `overlap`), merges section metadata (pages, heading).
  - `parsing.parse_file` — txt/md/pdf/docx → `list[Section]` (md by heading, pdf by page).
- `app/providers/` — `Embedder` (OpenAI-compatible, 千问 DashScope), `LLM` (OpenAI-compatible) and `Reranker` (DashScope `qwen3-rerank`, httpx POST `{rerank_base_url}/reranks`) protocol abstractions. `get_embedder()` / `get_llm()` / `get_reranker()` factories (lru_cached); provider is switched via `EMBEDDING_BASE_URL`/`LLM_BASE_URL`/`RERANK_BASE_URL` env config with zero business-code changes. No local model code.
- `app/models.py` — `Document` / `Chunk` / `Conversation` / `Message`. `Chunk.embedding` is a pgvector `Vector(settings.embedding_dim)` column; `chunk_metadata` maps to DB column `metadata` (avoids a Declarative reserved name).
- `app/database.py` — engine, `SessionLocal`, `Base`, `get_db`.
- `app/config.py` — pydantic-settings, all config from `.env` / env vars.
- `alembic/versions/` — `0001_initial.py` (4 tables, `Vector(512)`), `0002_hnsw_index.py` (HNSW cosine index on `chunks.embedding`), `0003_message_retrieval_mode.py` + `0004_drop_message_retrieval_mode.py` (曾记录 `messages.retrieval_mode`，检索固定混合后已删除该列).

### Backend invariants / gotchas
- **DB session lifetime**: async background work (document ingest, SSE chat streaming) must open its own `SessionLocal()`, because the request-scoped `get_db` session closes when the request returns. See `_run_ingest` in `documents.py` and `event_stream` in `chat.py`.
- **Embedding-dim coupling**: `EMBEDDING_DIM` (`.env`) must equal the model's dim and the `Vector(...)` column. `OpenAICompatEmbedder` explicitly passes `dimensions=EMBEDDING_DIM` because online models default higher (Qwen `text-embedding-v3/v4` default 1024) — forgetting this inserts 1024-dim vectors into the 512 column and fails. Changing the embedding model requires updating both and rebuilding tables (drop + `alembic upgrade head`); even with the same dim, **vectors from different models live in different spaces — re-ingest documents after switching models**.
- **Cascades are DB-level** (`ON DELETE CASCADE`); ORM relationships use `passive_deletes=True`.
- Tests inject `FakeEmbedder` (deterministic 8-dim char-hash vectors), `FakeLLM` and `FakeReranker` directly into service calls — no network, no model download. `tests/conftest.py` sets env vars (incl. `rag_kb_test` DB, `RERANK_ENABLED=true`) before any `app` import because `Settings` is a process-level cached singleton. Rerank is gated on `settings.rerank_enabled and settings.rerank_api_key`, so an empty key silently skips it.

### Frontend
- `src/router/` — `/` landing, `/chat` chat, `/documents` KB management.
- `src/api/` — `http.ts` axios wrapper (timeout 30s, response interceptor throws `ApiError` with backend `detail`, methods unwrap `r.data`); `chat.ts` streams SSE via `@microsoft/fetch-event-source` (native `EventSource` only supports GET, can't carry the POST body). No auto-reconnect — reconnecting would re-ask the model.
- **No global state store** (no Pinia / composables — explicit user preference). Page state lives directly in each page's `index.vue` as plain `ref`s; child components are presentational (props down, emits up). Chat state + conversation list live in `views/chat/index.vue` (`ConversationPanel.vue` takes `conversations`/`loading`/`activeId` props, emits `select`/`new`/`delete`); document list + polling in `views/documents/index.vue` (`DocumentDetailDialog.vue` calls `getDocument()` directly). Consequence: navigating between pages resets in-memory view state — conversations/documents persist server-side, re-select to reload.
- **`pushMessage` must return the reactive proxy** `messages.value[messages.value.length - 1]`, not the raw pushed object — mutating the raw object bypasses Vue's reactive `set` trap and streamed chunks never re-render. 检索固定混合模式，聊天页头部无模式切换控件。
- **Element Plus on-demand rules**: `ElMessage` / `ElMessageBox` must be used *without* explicit import so `unplugin-auto-import` injects their style side-effects (explicit imports skip styles); `el-table` `v-loading` requires `ElementPlusResolver({ directives: true })`.
- **Page structure convention**: page = `views/<page>/index.vue`; page-private components in `views/<page>/components/`; only cross-page-shared components live in top-level `components/` (currently just `AppHeader`). Imports use the `@/` alias (`@` → `src`).

## SSE chat protocol (`POST /api/chat`)

Request `{question, conversation_id?}` → `text/event-stream`, one `data: {...}` per event (blank-line separated). Events: `chunk` (token increment), `sources` (`[{document_id, filename, chunk_id, chunk_index, similarity, snippet}]`), `no_evidence`, `error`, `done` (`{conversation_id}` — reuse it to continue the conversation). 检索固定混合模式（关键词 BM25 + 语义向量，RRF 融合），无模式参数。Omitting `conversation_id` auto-creates a conversation titled by the question's first 30 chars.
