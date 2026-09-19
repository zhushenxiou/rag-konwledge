# 环境搭建与启动教程

## 1. 前置条件

- Windows + **PostgreSQL 18**（`localhost:5432`，账号 `postgres`）
- **conda** 环境 `langchain`（Python 3.13）
- **Node.js + pnpm**

## 2. 安装 pgvector（仅一次）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_pgvector.ps1
```

## 3. 安装依赖

```powershell
conda run -n langchain pip install -r requirements.txt
cd frontend && pnpm install
```

## 4. 配置环境变量

```powershell
copy .env.example .env
# 编辑 .env，至少填入 LLM_API_KEY、EMBEDDING_API_KEY
```

> 关键项：`EMBEDDING_DIM` 必须与模型输出维度 / 数据库 `Vector(512)` 列一致；`RERANK_API_KEY` 留空则跳过在线重排。
>
> 登录相关（`AUTH_USERNAME` / `AUTH_PASSWORD` 等）**不用改**：用 `.env` 里已有的演示账号即可（不写也有默认值）。登录需要图片验证码，依赖 `pillow`（已列在 `requirements.txt`）。

## 5. 初始化数据库（幂等）

```powershell
conda activate langchain
$env:PYTHONIOENCODING = "utf-8"   # 避开 conda run 在 GBK 控制台输出中文崩溃
python scripts/init_db.py
python -m alembic upgrade head
```

## 6. 启动后端

```powershell
conda activate langchain
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 接口文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/health>

## 7. 启动前端

```powershell
cd frontend && pnpm dev
```

打开 <http://localhost:5173>（`/api` 自动代理到 :8000）。生产构建：`pnpm build`。

未登录会被重定向到 `/login`，用 `.env` 里 `AUTH_USERNAME` / `AUTH_PASSWORD` 的演示账号 + 页面上的四位数字验证码登录（点图片可换一张）。

## 8. 测试

```powershell
python -m pytest -q                       # 122 个用例，独立 rag_kb_test 库，不触网/不调模型
python scripts/e2e_verify.py              # 端到端验收（含真实模型调用，需后端已启动）
```

> `e2e_verify.py` 是跨进程黑盒脚本，读不出图片验证码，因此后端需以 `AUTH_CAPTCHA_BYPASS=true` 启动：

```powershell
$env:AUTH_CAPTCHA_BYPASS = "true"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

> 该开关默认关闭（开启时启动会打 warning），仅本地自动化验收使用，**生产环境绝不可开启**。
> 另外脚本第 1 步起依赖 `demo/sample.md`、`demo/sample.pdf`，这两个素材**仓库里没有**——鉴权与健康检查部分（步骤 0.1–0.5）可以正常跑完，后面的步骤跑不过去。
