#!/bin/sh
# 后端容器启动脚本：等 DB → 自检 → 建库/建扩展 → 迁移 → 启动 uvicorn。
#
# 为什么这些事放在容器启动时而不是「部署时手工跑一次」：
#   app/main.py 没有任何 startup/lifespan 钩子，表结构完全交给 Alembic，
#   而 0001_initial.py 直接用 Vector(512) 建列却**不建扩展**。手工步骤一旦漏掉，
#   症状是容器起来了、健康检查也过了，但一提问就报 `type "vector" does not exist`。
#   串进启动流程里，漏不掉。
#
# 用 POSIX sh 而非 bash 写：python:3.13-slim（Debian）里 /bin/sh 是 dash，
# bash 并没装。
set -e

: "${DATABASE_URL:?FATAL: DATABASE_URL 未设置}"
: "${UPLOAD_DIR:=/data/uploads}"

log() { echo "[entrypoint] $*"; }

# ---------------------------------------------------------------- 启动前自检
# 本项目定位是演示，账号口令在**任何环境都固定为同一对**（见 .env 的 AUTH_*，
# 即 app/config.py 的默认值），所以容器**不拦演示口令** —— 不设 AUTH_* 就用默认值起。
#
# 唯一还提示的是「把 AUTH_PASSWORD 显式设成空串」这一种写法：那会让登录口令变成空字符串，
# 任何人都能用空口令登进来。完全不设这个键不会触发（那时回落到 config.py 的默认值，是正常用法）。
if [ "${AUTH_PASSWORD+set}" = "set" ] && [ -z "$AUTH_PASSWORD" ]; then
    echo "[entrypoint] WARN: AUTH_PASSWORD 被显式设成了空串 —— 空口令即可登录，请确认这是有意的。" >&2
fi

# 这个开关会让取验证码接口额外返回明文 code，验证码形同虚设 —— 只允许本地自动化验收用。
if [ "${AUTH_CAPTCHA_BYPASS:-false}" = "true" ]; then
    echo "[entrypoint] FATAL: AUTH_CAPTCHA_BYPASS=true（验证码形同虚设），生产环境禁止启动。" >&2
    exit 1
fi

# 上传目录必须可写。最常见的翻车方式是卷属主不对：命名卷首次挂载时若挂载点
# 在镜像里**不存在**，卷根是 root:root，非 root 进程建目录直接 PermissionError。
# 症状极具迷惑性 —— 健康检查、登录、问答全部正常，只有上传 500，很容易误判成业务 bug。
# （镜像里已预建 /data/uploads 并 chown，正常路径下不会触发；这里是兜底。）
if [ ! -d "$UPLOAD_DIR" ]; then
    mkdir -p "$UPLOAD_DIR" 2>/dev/null || {
        echo "[entrypoint] FATAL: 无法创建 $UPLOAD_DIR" >&2
        exit 1
    }
fi
if [ ! -w "$UPLOAD_DIR" ]; then
    echo "[entrypoint] FATAL: $UPLOAD_DIR 不可写 —— 多半是卷属主为 root。" >&2
    echo "[entrypoint]        以 root 起一个临时容器把属主改回 uid 1000 即可：" >&2
    echo "[entrypoint]          docker compose run --rm -u root backend chown -R 1000:1000 /data" >&2
    exit 1
fi

# ------------------------------------------------------------------ 等数据库
# 有上限地等：写成 while True 的话，DB 永不起来时表现为「compose 挂住、毫无报错」，
# 是最难排查的一种失败。
i=0
until python -c "
import os, sys, psycopg2
from urllib.parse import urlparse
# SQLAlchemy 的 +psycopg2 后缀不是合法 URI scheme，去掉后再交给 psycopg2
u = urlparse(os.environ['DATABASE_URL'].replace('+psycopg2', ''))
try:
    # 连 postgres 这个必定存在的管理库：目标库此时可能还没被 init_db.py 创建
    psycopg2.connect(host=u.hostname, port=u.port or 5432, user=u.username,
                     password=u.password, dbname='postgres', connect_timeout=3).close()
except Exception:
    sys.exit(1)
" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then      # 60 × 2s = 120s
        echo "[entrypoint] FATAL: 等 120 秒数据库仍不可达，放弃。" >&2
        exit 1
    fi
    sleep 2
done
log "数据库可达"

# ---------------------------------------------- 建库 + pgvector 扩展 + 迁移
# init_db.py 必须从仓库根执行（它自己做了 sys.path.insert(0, ".")），且不 chdir ——
# 一旦 cd 到别处，`import app` 或 alembic 的 script_location 就会找不到目标，
# 报错信息还很没指向性（比如 "No 'script_location' key found"）。
log "初始化数据库与 pgvector 扩展"
python /app/scripts/init_db.py

log "执行数据库迁移"
python -m alembic -c /app/alembic.ini upgrade head

# -------------------------------------------------------------- 配置摘要
# 打一行脱敏摘要：配置写错时最缺的就是「进程实际读到了什么」。
# 不含任何密钥，可以安全地留在日志里。
log "配置: data_source=$UPLOAD_DIR auth_user=${AUTH_USERNAME:-zhuliang} \
captcha_bypass=${AUTH_CAPTCHA_BYPASS:-false} memory=${MEMORY_ENABLED:-true} \
rerank=${RERANK_ENABLED:-true} embedding=${EMBEDDING_MODEL_NAME:-<默认>}"

# ---------------------------------------------------------------- 启动服务
# **绝对不要加 --workers**：登录 token 与验证码存在进程内存
# （app/services/auth.py 里的两个模块级 dict），多 worker 时 A 进程签发的 token
# 到 B 进程校验必然 401，表现为「随机掉登录」。要横向扩展得先把这两个 dict 换成
# 共享存储（Redis / DB 表），否则加副本 = 加故障。
#
# --proxy-headers: 前面有 nginx。不加的话 forwarded-allow-ips 默认只信 127.0.0.1，
#   来自 web 容器的 X-Forwarded-For/Proto 全被丢弃，访问日志里来源 IP 全是 nginx 容器地址。
exec python -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips='*'
