# 部署到 Ubuntu 云服务器（Docker Compose）

面向一台全新的 Ubuntu 服务器（已装 Docker 与 Docker Compose）。全过程约 10 分钟，
其中构建前端镜像最慢（几分钟，取决于机器）。

本机 Windows 上的开发方式见 `setup.md`，两者互不影响。**这份文档里的 `docker compose`
命令只适用于服务器** —— 注意它在开发机上**也是跑得起来的**（`.env.example` 里带了
`POSTGRES_*`），所以靠自觉别在开发机上用它，理由见文末「本地开发为什么不用 compose」。

## 架构

```
浏览器 ── :82 ──> [web]  nginx:alpine        ← 宿主机端口，由 HTTP_PORT 决定（默认 82）
                          │  容器内仍监听 80
                          ├─ /       → dist 静态文件（SPA 回退到 index.html）
                          └─ /api/   → http://backend:8000   （关闭缓冲，供 SSE）
                                              │
                                [backend] python:3.13-slim + uvicorn（**单 worker**）
                                  启动时：等 DB → 建库/建扩展 → 迁移 → 起服务
                                  容器内恒为 8000，不发布到宿主机
                                              │
                                [db] pgvector/pgvector:0.8.6-pg18-trixie
                                  命名卷持久化，不对宿主机发布端口
```

## 0. 先解决构建期联网（国内服务器必做）

`docker compose build` 要联网拉**三个不同来源**，它们互不相干，**任何一个不通都会
让构建卡住**。别只测一个就以为没事 —— 下面三类在国内服务器上都要过一遍。

| 来源 | 谁在用 | 失败/慢的表现 |
|---|---|---|
| **Docker Hub** | 四个基础镜像（`python:3.13-slim`、`node:22-alpine`、`nginx:alpine`、`pgvector/pgvector`） | 直接报 `dialing registry-1.docker.io ... A connection attempt failed` |
| **PyPI** | 后端镜像里 `pip install -r requirements.txt` | **不报错，只是极慢**：官方 CDN 实测 ~34 KB/s，一个 19MB 的包要下 9 分钟 |
| **npm registry** | 前端镜像里 `npm i -g pnpm` 与 `pnpm install` | 同样**不报错、只是慢**：官方实测 ~227 KB/s |

> 后两者的迷惑性在于**它们看起来是通的**：`curl https://pypi.org/` 会返回 200，
> 让人以为没问题。但真正下载文件的是 `files.pythonhosted.org` / `registry.npmjs.org`，
> 吞吐完全是另一回事。**判断标准是吞吐，不是连通性**。

### 0.1 Docker Hub

先测通不通：

```bash
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 https://registry-1.docker.io/v2/
# 401 = 通（Registry v2 未认证时的标准响应）；000 = 不通，需要配镜像源
```

不通就加镜像加速器：

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://docker.m.daocloud.io"]
}
EOF
sudo systemctl restart docker
docker info | grep -A2 "Registry Mirrors"     # 确认已生效
```

> 若这台机器是**腾讯云**的 CVM，`daemon.json` 里通常已经预置了
> `https://mirror.ccs.tencentyun.com`（走内网，最快），那就什么都不用做。
>
> 注意 `docker manifest inspect` **不走镜像源**，它直连 Docker Hub，所以加速器配好
> 之后这条命令仍然会失败 —— 别拿它当判断依据，用 `docker pull` 实测。

### 0.2 PyPI 与 npm

这两项**不需要改 Dockerfile**，改 `.env` 里的两个变量即可（`docker-compose.yml`
会通过 `build.args` 传进镜像构建）：

```bash
PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/
NPM_REGISTRY=https://registry.npmmirror.com
```

实测对比（同一台腾讯云机器，下 numpy 的 wheel）：

| 源 | 用时 |
|---|---|
| 官方 PyPI | **> 90s 超时** |
| 腾讯云 | 3s |
| 清华 | 4s |
| 阿里云 | 8s |

npm 方面：官方 registry 227 KB/s vs npmmirror 2.4 MB/s。

> `.env.example` 里这两项的默认值就是上面那对国内镜像（本项目的部署目标是国内服务器）。
> 要改回官方源，把值写成 `https://pypi.org/simple/` 和 `https://registry.npmjs.org` 即可。
> **验收标准就是 `docker compose build` 能在几分钟内跑完**，不必纠结用哪一个。

## 1. 前置检查

```bash
docker --version && docker compose version      # 需要 compose v2+
ss -ltnp | grep ':82 '                          # 应无输出；被占用就改 HTTP_PORT
free -h                                         # 构建镜像建议 ≥ 2G 内存
```

**云控制台的安全组必须放行 82 端口**（即 `.env` 里的 `HTTP_PORT`）。
「服务器上 `curl http://127.0.0.1:82/` 通、外网打不开」几乎总是安全组没放行。

## 2. 获取代码

推荐 `git clone`：

```bash
git clone <仓库地址> rag-kb && cd rag-kb
```

若用 rsync/scp 从 Windows 传，注意两件事：

- 排除 `.env`、`data/`、`frontend/node_modules`、`frontend/dist`。
  `node_modules` 里的 Windows 原生二进制传过去只会捣乱。
- 确认 `deploy/backend-entrypoint.sh` 是 **LF** 行尾（`file deploy/backend-entrypoint.sh`
  应显示 `POSIX shell script ... executable`，不能出现 `CRLF`）。
  仓库里的 `.gitattributes` 只在 git 传输时生效，rsync/scp 不走它。

## 3. 配置 .env

```bash
cp .env.example .env
vim .env          # 改下面标 ★ 的项
chmod 600 .env
```

**只有四项必须改**（模板里已经写好了本地开发的取值，容器需要的差异项由
`docker-compose.yml` 的 `environment:` 段覆盖，不用你动）：

| 项 | 说明 |
|---|---|
| ★ `POSTGRES_PASSWORD` | 换成强口令，**只用字母数字**（会被拼进连接串 URL，含 `@ : /` 会破坏解析） |
| ★ `LLM_API_KEY` / `EMBEDDING_API_KEY` / `RERANK_API_KEY` | 真实密钥 |

不用改的（列出来是为了让你放心跳过）：

| 项 | 为什么不用改 |
|---|---|
| `DATABASE_URL` | 模板里那份指向 `localhost:5432`，是给本机开发用的；容器里由 compose 用 `POSTGRES_*` 拼成 `db:5432` 并**覆盖**它。改口令请改 `POSTGRES_PASSWORD`，改这里在容器里不生效 |
| `UPLOAD_DIR` | 模板里是 `./data/uploads`（本机用）；容器里被强制覆盖成 `/data/uploads`。它同时是 `documents.file_path` 落库的内容，必须是绝对路径 |
| `TEST_DATABASE_URL` | 模板里指向本机测试库；容器里被**强制置空**（不置空会让 `init_db.py` 去连容器的 `localhost` → 崩溃循环）。这条以前靠文档提醒，现在是 compose 兜住的 |
| `AUTH_USERNAME` / `AUTH_PASSWORD` | 演示定位，**保持模板里已有的值**（所有环境统一）。想换也行，但改完要去日志核对，见下 |
| `AUTH_CAPTCHA_BYPASS` | **保持 `false`**。给 `true` 后端会拒绝启动 |

> ⚠️ `pydantic-settings` 配的是 `extra="ignore"`：**键名拼错的配置项会被静默忽略**，
> 程序不报错、直接回落默认值。所以「改了没生效」时第一件事是核对拼写 ——
> 后端启动日志里那行脱敏配置摘要是用来对照的。

## 4. 构建

```bash
docker compose build
```

前端镜像里会跑 `vue-tsc -b`（项目的类型检查）再打包，**类型错误会让构建失败** ——
这是刻意保留的质量闸门，不要用 `vite build` 绕过它。
构建失败不会影响已在运行的旧容器，compose 会直接中止。

失败时先在开发机上 `pnpm build` 复现，那里有完整的报错上下文。
紧急情况下需要跳过类型检查的话，可以在 `frontend/Dockerfile` 里临时把 `RUN pnpm build`
换成 `RUN pnpm exec vite build` —— **仅用于救急，事后必须补正**。

## 5. 按顺序启动

```bash
docker compose up -d db && docker compose logs -f db
# 看到 "database system is ready to accept connections"，Ctrl+C 退出日志

docker compose up -d backend && docker compose logs -f backend
```

后端日志里应当**依次**出现：

```
[entrypoint] 数据库可达
[entrypoint] 初始化数据库与 pgvector 扩展
[init_db] pgvector ready on rag_kb: v0.8.x
[entrypoint] 执行数据库迁移
... alembic 0001 → 0005 ...
[entrypoint] 配置: data_source=/data/uploads auth_user=<你配的账号> captcha_bypass=false ...
INFO:     Uvicorn running on http://0.0.0.0:8000
```

核对那行 `配置:` 摘要 —— 这是「配置是否真的生效」最直接的证据。
然后起前端：

```bash
docker compose up -d web
```

## 6. 验证

```bash
docker compose ps            # 三个服务都应是 running，db/backend 是 healthy
curl -s http://127.0.0.1:82/api/health
```

`health` 返回里 **`database` 必须是 `"ok"`**。注意这个接口即使数据库挂了也返回
HTTP 200（`status` 字段恒为 `"ok"`），所以**只看状态码会误判**。

### 验证持久化（务必做一次）

这条专门验 PostgreSQL 18 那个「挂错数据目录会静默丢数据」的坑：

```bash
docker compose exec db printenv PGDATA
# 期望 /var/lib/postgresql/18/docker

docker compose exec db psql -U postgres -d rag_kb -c "CREATE TABLE _probe(x int);"
docker compose down
docker compose up -d
docker compose exec db psql -U postgres -d rag_kb -c "SELECT * FROM _probe;"
# 表还在 → 数据目录挂对了；报 "relation does not exist" → 卷挂错，见「排查」
docker compose exec db psql -U postgres -d rag_kb -c "DROP TABLE _probe;"
```

### 浏览器端验收

打开 `http://<服务器IP>:82/`：

1. 用 `.env` 中 `AUTH_USERNAME` / `AUTH_PASSWORD` 的账号登录（各环境统一）。
   若你改过 `AUTH_*` 却仍能用改前的账号登进去，说明 `.env` 里键名拼错了，回第 3 步。
2. 上传一个文档 → 状态应从 `pending` 变 `processing` 再到 `ready`。
3. 针对该文档提问，确认回答是**逐字流式**出现的。若整段一次性蹦出来，
   说明 SSE 被某层缓冲了（见「排查」）。

### 关于 `scripts/e2e_verify.py`

这个验收脚本读不出图片验证码，因此需要后端以 `AUTH_CAPTCHA_BYPASS=true` 启动。
但**容器里的 entrypoint 会拒绝以该开关启动**（见下），所以**脚本无法针对容器化部署运行**。

这是刻意的取舍，不是疏漏：容器就是生产路径，而那个开关会让验证码形同虚设。
要跑端到端验收，请在开发机上按 `setup.md` 用 conda 方式起后端，再对该后端运行脚本。

### 关于演示账号

演示账号在**所有环境（本机 conda、容器、服务器）统一**，值就是 `.env` 里
`AUTH_USERNAME` / `AUTH_PASSWORD` 的那两个（也等于 `app/config.py` 的默认值）。
所以不设 `AUTH_*` 也能登进去 —— 这是有意的。

**代价要说清楚**：`pydantic-settings` 配了 `extra="ignore"`，`.env` 里键名拼错的项
（比如把 `AUTH_PASSWORD` 少写一个 `O`）**不会报任何错**，只会静默回落到默认值。
也就是说，如果你哪天想把账号换掉，「我改过口令了」这个判断本身是不可靠的 ——
拼错了照样用默认那对账号放行，而你从 `.env` 上看不出来。

所以想改口令时，**唯一的核对手段是后端启动日志里那行脱敏摘要**：

```
[entrypoint] 配置: data_source=/data/uploads auth_user=<你实际配的值> ...
```

`auth_user` 显示什么，进程就认什么。别只看 `.env`。

### entrypoint 还剩哪些启动闸

`deploy/backend-entrypoint.sh` 目前只拒绝一种情况：`AUTH_CAPTCHA_BYPASS=true`。
那个开关会让取验证码接口额外返回明文 `code`，验证码形同虚设，所以容器（= 生产路径）
禁止以它启动。另外若 `AUTH_PASSWORD` 被**显式设成空串**（不是不设，是设成空），
entrypoint 会打一条 `WARN` 但不拦 —— 空口令能登进去，那是很可能是笔误，但决定权留给你。

## 7. 日常运维

```bash
docker compose logs -f backend        # 看日志
docker compose restart backend        # 重启（会让全体用户登出，见下）
docker compose down                   # 停止（**绝不加 -v**，-v 会连数据卷一起删）
docker compose up -d                  # 启动
```

**改完代码要上线，不要手敲上面这些，用 [`scripts/deploy.sh`](#8-重新部署改完代码怎么上线)**
（在**本机**跑，不在服务器上跑）。上面这几条是在服务器上排查/救急时用的。

### 备份

```bash
docker compose exec -T db pg_dump -U postgres rag_kb > backup_$(date +%F).sql
docker run --rm -v rag-kb_uploads:/data -v "$PWD:/out" alpine \
    tar czf /out/uploads_$(date +%F).tar.gz -C /data .
```

**数据库和上传目录必须一起备份、一起恢复**：`documents.file_path` 里存的是
`/data/uploads/xxx` 这样的路径字符串，只恢复一边会导致文档详情/重试/删除全部失效。

## 8. 重新部署（改完代码怎么上线）

在本机（**Git Bash**，不是 PowerShell）仓库根目录执行：

```bash
bash scripts/deploy.sh
```

就这一条。它会依次：把**当前工作区**打包上传 → 解到服务器并 `rsync --delete` 覆盖
`/opt/rag-kb` → 比对两边 `.env` 的键名 → `docker compose build` → `up -d` →
轮询 `/api/health` 直到 `database=ok` 且首页返回 200。

**不需要先 commit**。改完存盘就能部署，这正是选"推送式"而不是"服务器上 git pull"的原因 ——
部署想验证的通常就是那些还没提交的改动。代价是**服务器上的代码不对应任何 commit**，
所以脚本每次会把当时的本地 `git HEAD` 与未提交改动数写进服务器的
`/opt/rag-kb/.deploy-info`，出事时靠它倒查跑的是哪一版。

### 一次性准备：配免密登录

```bash
ssh-copy-id ubuntu@<服务器IP>
# Git Bash 上可能没有 ssh-copy-id：
cat ~/.ssh/id_ed25519.pub | ssh ubuntu@<服务器IP> \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'
```

脚本用 `BatchMode=yes` 连 SSH：**没配好免密它会立刻报错并打印上面这段命令**，
而不是静默转到密码提示上"卡住不动"（脚本里最难看的一种失败）。

### 服务器地址写在哪

`脚本`不硬编码服务器 IP —— 本仓库是公开的，把生产机 IP 连同"这是演示站、口令是
公开默认值"一起提交到 GitHub，等于给扫描器递名单。地址放在仓库根的 `.deploy.env`
（已 gitignore，模板 `.deploy.env.example`）：

```bash
cp .deploy.env.example .deploy.env   # 然后填 DEPLOY_HOST / DEPLOY_USER / DEPLOY_PATH
```

### 参数

| 参数 | 用途 |
|---|---|
| （无） | 全量：推代码 + 重建两个镜像 + 重启 + 健康检查 |
| `--only backend` / `--only web` | 只重建一个服务。前端那次 `pnpm install` + `vue-tsc` 是构建耗时的大头，只改后端时省一半时间 |
| `--no-build` | 只推代码 + 重启容器，**不重建镜像**。仅"想单纯重启"时有用 —— 见下 |
| `--with-env` | **危险**：连 `.env` 一起推。见下 |

> `--no-build` 有个很容易踩的坑：后端与前端的代码是 **`COPY` 进镜像**的，
> 不重建镜像就等于没换代码。它不会报错，只会让人以为"部署过了怎么没生效"。

### 为什么默认**不**推 `.env`

服务器那份 `.env` 里的 `POSTGRES_PASSWORD` 与本机不同（部署时现场生成的随机串）。
覆盖它**不会**"改掉密码"，而是让 backend 拿**新**口令去连一个**已按旧口令初始化**的
`pgdata` 卷 → `password authentication failed` → backend 无限重启循环。
要换口令必须连数据卷一起重建，那是另一件事，不是"重新部署"。

脚本改为做一件更有用的事：**比对两边 `.env` 的键名**，本地有、服务器没有的键会被
点名。那正是 pydantic `extra="ignore"` 会静默吃掉、表现为"我配了但没生效"的一类问题
（见 `.env.example` 顶部那段警告）。

### 服务器上的代码是本地工作区的**镜像**

同步用的是 `rsync -a --delete`：本地删掉的文件，服务器上也会被删掉。
所以**不要在服务器上直接改代码**，下次部署就没了。要改就改本地再 `deploy.sh`。

### 部署失败了怎么退

脚本在 `docker compose build` 失败时会**直接退出，且此时线上服务未受任何影响**
（还跑着上一版镜像）。修完再跑一次即可。真正的风险窗口只在 `up -d` 之后，
而健康检查会立刻把现场（`docker compose ps` + backend 日志末尾 40 行）打出来。

## 已知并接受的风险

当前部署是 **IP + HTTP，没有 TLS**。以下几项是明确知悉后接受的，不是疏漏：

1. **口令与 token 明文过网**，且口令是**固定不变的演示口令**（就写在仓库的
   `.env` 与 `app/config.py` 默认值里，谁拿到源码都能看到）。
   两件事叠加的后果：任何能访问到这台机器 82 端口的人，不需要猜口令就能登进来；
   明文 HTTP 下任何中间节点也都能读走 `Authorization: Bearer <token>`。
   换句话说，**这套部署的鉴权只防"误入"，不防"有意访问"** —— 演示场景可以接受，
   但**不要往里放任何真实/敏感文档**。要真正闭环需要 HTTPS（域名 + certbot），
   属于后续增量。
2. **`/api/health` 无需登录即可访问**，会回显所用模型与厂商
   （`embedding_base_url`、`llm_model`、`app`）。可经 nginx 的 82 端口拿到。
   **注意 `/docs` 与 `/openapi.json` 实际打不到** —— 实测这两个路径经 82 端口返回的是
   前端的 `index.html`（HTTP 200，但是 HTML 而不是 OpenAPI JSON）：nginx 只代理
   `/api/` 前缀，它们落进了 `location /` 的 SPA 回退；后端 8000 也没 publish 到宿主机。
   所以**接口结构并未暴露**，要看文档只能进容器内部访问。
3. **重启后端 = 全体用户登出**。token 与验证码存在进程内存（`app/services/auth.py`），
   不落库。这是既有设计，不是 bug。同理**后端永远只能单 worker / 单副本** ——
   多 worker 时 A 进程签发的 token 到 B 进程校验必然 401，表现为「随机掉登录」。
   要横向扩展得先把那两个内存字典换成 Redis 或数据库表。
4. **`requirements.txt` 全部是 `>=` 下界、没有上界**，同一份代码在不同时间构建可能
   装到不同依赖版本（前端有 `pnpm-lock.yaml` 兜着，后端没有）。
   镜像一旦构建验证通过，就别无谓重建。

## 排查

| 现象 | 原因与处理 |
|---|---|
| `docker compose build` 报 `dialing registry-1.docker.io ... A connection attempt failed` | Docker Hub 不可达。按第 0 节配镜像源 |
| `docker compose build` **不报错但十几分钟没动静**，日志停在某条 `Downloading xxx.tar.gz` | PyPI 或 npm 走了官方源（慢，不是断）。看日志卡在 `pip install` 还是 `pnpm install`，按第 0.2 节把对应的 `PIP_INDEX_URL` / `NPM_REGISTRY` 写进 `.env` 再重建。**判断依据是吞吐不是连通性** —— `curl https://pypi.org/` 返回 200 完全正常，但真正下载文件的是 `files.pythonhosted.org` |
| `docker compose up` 报 `required variable POSTGRES_USER is missing` | `.env` 没建或没写 `POSTGRES_*`。从 `.env.example` 生成 |
| backend 反复重启，日志停在 `初始化数据库` | `TEST_DATABASE_URL` 非空。正常情况下 compose 已把它强制置空，出现这条说明 `docker-compose.yml` 里那行 `TEST_DATABASE_URL: ""` 被删了 —— 加回去 |
| backend 反复重启，日志里是 `password authentication failed` | `.env` 里 `POSTGRES_PASSWORD` 与数据卷里已初始化的口令不一致。改口令后需删卷重建（会丢数据），或把口令改回去 |
| 改了 `AUTH_PASSWORD` 却仍能用改前的账号登进去 | 键名拼错了（`extra="ignore"` 会静默吞掉）。核对后端日志 `配置:` 那行的 `auth_user` |
| backend 起不来，日志 `FATAL: /data/uploads 不可写` | 数据卷属主是 root。按日志里的提示改属主 |
| 上传 500，但登录/问答都正常 | 同上，卷的属主/权限问题 |
| 上传大文件得到 nginx 的 413 HTML 页 | 文件超过 nginx 的 12m 限制，或超过了后端 10MB 限制。前者改 `frontend/nginx.conf` |
| **`web` 容器 `Up`、端口映射看着也对，但连上去被立刻关闭**；`docker compose logs web` 只停在 `10-listen-on-ipv6-by-default.sh: info: Getting the checksum of ...` | nginx 官方 entrypoint 卡在 `apk manifest nginx`（该脚本 alpine 分支会调它），**nginx 主进程根本没启动**，而容器状态和端口映射都正常，极具迷惑性。已在 `frontend/Dockerfile` 里删除那个脚本（我们自带 `default.conf`，它本就空转）。若复现，确认那行 `RUN rm -f /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh` 还在。自查命令：`docker compose exec web ps -ef \| grep -c "[n]ginx: master"`，应为 1 |
| 回答整段蹦出、不是逐字 | SSE 被缓冲。检查 `frontend/nginx.conf` 里 `/api/` 段的 `proxy_buffering off` 与 `gzip off` |
| 刷新 `/chat` 页面 404 | `try_files` 没生效。前端路由用 `createWebHistory()`，必须回落到 `index.html` |
| 外网打不开、服务器上 `curl 127.0.0.1:82` 正常 | 云控制台安全组没放行 82 端口（或 `.env` 里的 `HTTP_PORT` 与放行端口不一致） |
| `docker compose down` 后数据没了 | 卷挂错了目录。核对 `PGDATA` 与 compose 里的挂载点（必须是 `/var/lib/postgresql`，不是 `.../data`） |
| 前端构建在 `vue-tsc` 阶段失败 | 先在开发机 `pnpm build` 看完整报错。另确认 `frontend/src/auto-imports.d.ts` 与 `components.d.ts` **仍在 git 里**（它们必须入库，见下） |

## 本地开发为什么不用 compose

`.env.example` 是**唯一**的模板，里面同时带了本机开发的值和 `POSTGRES_*`，
所以 `docker compose up -d` 在开发机上**跑得起来** —— 它不是被拦住的，是你不该用：

- 镜像里的代码是 `COPY` 进去的，**改一行就得重建镜像**，迭代极慢；
- 本机那套库（宿主机 PostgreSQL）与容器里的 `db` 卷是**两份数据**，
  在 `/documents` 里看到的内容取决于你访问的是哪个后端，很容易看错。

本地开发请走 `setup.md` 的 conda 方式。compose 是服务器路径。

> 这里仍保留 `${POSTGRES_USER:?...}` 这种**强制**变量语法（而不是默认的 `${VAR}`）：
> 后者只会打一条 warning 然后**替换成空字符串继续跑**，结果是 db 容器带着空口令启动、
> 后端连不上，而根因埋在几十行日志里。键名漏写/拼错时响亮失败，比静默降级好。
