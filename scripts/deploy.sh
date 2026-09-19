#!/usr/bin/env bash
#
# 一键重新部署：把本机【当前工作区】打包推到服务器，重建镜像并重启。
#
#   bash scripts/deploy.sh                  # 推代码 → 重建 → 重启 → 健康检查
#   bash scripts/deploy.sh --only backend   # 只重建 backend（前端不动，省一半时间）
#   bash scripts/deploy.sh --no-build       # 只推代码 + 重启容器，不重建镜像
#   bash scripts/deploy.sh --with-env       # 【危险，先读下面那段】连 .env 一起推
#
# 服务器地址从仓库根下的 `.deploy.env` 读（已 gitignore，模板见 .deploy.env.example）。
#
# ── 为什么是"推工作区"而不是"服务器上 git pull" ──────────────────────────
# 工作区里常有还没 commit 的改动，而部署想验证的恰恰就是这些改动。打包清单用
# `git ls-files --cached --others --exclude-standard`：**内容读的是工作区**（所以
# 未提交的修改会进去），**清单来自 git**（所以 .env / node_modules / dist 这些
# gitignore 掉的东西不会进去，同时新加但还没 `git add` 的文件也会进去）。
# ⚠️ 别把这里改成 `git checkout-index`：它导出的是**索引**里的版本，工作区改过但
# 还没 `git add` 的内容会静默丢失，症状是"部署跑完了，跑的还是旧代码"。
#
# ── 为什么默认不推 .env ────────────────────────────────────────────
# 服务器那份 .env 的 POSTGRES_PASSWORD 与本机不同（部署时现场生成的随机串）。
# 覆盖它并不会"改掉密码"，而是让 backend 拿**新**口令去连一个**已按旧口令初始化**
# 的 pgdata 卷 → `password authentication failed` → backend 无限重启。
# 只有"故意要换口令"才用 --with-env，那种情况必须连数据卷一起重建。
# 脚本会退一步做件更有用的事：**比对两边 .env 的键名**，本地有、服务器没有的
# 键会被点名 —— 那正是 pydantic `extra="ignore"` 会静默吃掉、表现为
# "我配了但没生效"的一类问题。
#
# ── 服务器上的代码是本地工作区的**镜像** ───────────────────────────
# 同步用 `rsync -a --delete`：本地删掉的文件，服务器上也会被删掉。
# 所以**不要在服务器上直接改代码**，下次部署就没了。
#
# ── 前置条件（一次性）──────────────────────────────────────────────
# 免密登录。没配的话脚本第一步就会明确报错并给出命令，不会闷着卡住。

set -euo pipefail

# Git Bash 会把命令行里形如 /tmp/xxx 的参数当成 Windows 路径改写掉（远端路径
# 被本地改成 C:\...，于是 ssh 过去执行的是个不存在的路径）。本脚本里给出的
# 路径全是**远端**路径，关掉这个转换。
export MSYS_NO_PATHCONV=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

WITH_ENV=0
DO_BUILD=1
ONLY=""

usage() {
  sed -n '3,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --with-env) WITH_ENV=1 ;;
    --no-build) DO_BUILD=0 ;;
    --only)
      shift
      ONLY="${1:-}"
      case "$ONLY" in
        backend|web|db) ;;
        *) echo "--only 只接受 backend / web / db，收到：'$ONLY'" >&2; exit 2 ;;
      esac
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; echo; usage; exit 2 ;;
  esac
  shift
done

# ── 读配置 ────────────────────────────────────────────────────────
# 顺序：环境变量 > .deploy.env > 报错。刻意**不给内置默认值**，理由见模板文件。
if [ -f "$REPO_ROOT/.deploy.env" ]; then
  # shellcheck disable=SC1091
  . "$REPO_ROOT/.deploy.env"
fi

: "${DEPLOY_HOST:?未设置 DEPLOY_HOST —— cp .deploy.env.example .deploy.env 后填上服务器地址}"
: "${DEPLOY_USER:=ubuntu}"
: "${DEPLOY_PATH:=/opt/rag-kb}"

TARGET="$DEPLOY_USER@$DEPLOY_HOST"
# BatchMode=yes：没配好免密就**立刻失败**，而不是静默转到交互式密码提示上等着 ——
# 后者在脚本里表现为"卡住不动"，很难判断到底在等什么。
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "$TARGET")

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

step()  { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()    { printf '  \033[32m[v]\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m[!]\033[0m %s\n' "$1"; }
die()   { printf '  \033[31m[x]\033[0m %s\n' "$1" >&2; exit 1; }

# ── 0. 连通性 ─────────────────────────────────────────────────────
step "0/5 检查到 $TARGET 的免密连接"
if ! "${SSH[@]}" true 2>/dev/null; then
  cat >&2 <<EOF

  连不上（或还没配免密登录）。先手动确认这一步能通：

      ssh $TARGET 'echo hi'

  免密没配的话，把本机公钥装上去（一次性）：

      ssh-copy-id $TARGET
      # Git Bash 上没有 ssh-copy-id 时：
      cat ~/.ssh/id_ed25519.pub | ssh $TARGET \\
        'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'

EOF
  exit 1
fi
ok "免密可用"

"${SSH[@]}" "test -f '$DEPLOY_PATH/docker-compose.yml'" \
  || die "$DEPLOY_PATH 下没有 docker-compose.yml —— 先按 deploy.md 把项目部署一次"

# ── 1. 打包上传 ───────────────────────────────────────────────────
step "1/5 打包并上传工作区"

GIT_HEAD="$(git rev-parse --short HEAD 2>/dev/null || echo 'n/a')"
GIT_DIRTY="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
FILE_COUNT="$(git ls-files --cached --others --exclude-standard | wc -l | tr -d ' ')"
echo "  文件数 $FILE_COUNT（已跟踪 + 新加但还没 add 的）；本地 HEAD $GIT_HEAD，未提交改动 $GIT_DIRTY 处"

# 打包走管道，不落盘：省掉一个临时文件，也绕开 GNU tar 把 "C:/..." 当远端主机名的老毛病。
#
# ⚠️ `--exclude` 在 GNU tar 里是**位置选项**：放在 `-T -` 之后会被当成"影响它之后的参数"，
# 而它后面已经没有参数了 —— tar 只会打一行 warning 然后**完全忽略**它，归档里该有的
# 还是有。必须写在 `-T` 之前。（踩过一次：加了 exclude 却在包里看到了 .env。）
# 这两条 exclude 本就是二道保险（.env 已被 gitignore 挡住），防的是有人 `git add -f .env`
# 过 —— 那种情况下密钥会被 rsync 到服务器、再被 Dockerfile 的 COPY 带进镜像层。
if ! git ls-files -z --cached --others --exclude-standard \
     | tar --null --exclude='.env' --exclude='.deploy.env' -czf - -T - \
     | "${SSH[@]}" 'cat > /tmp/rag-kb-deploy.tar.gz'; then
  die "打包/上传失败"
fi
ARCHIVE_BYTES="$("${SSH[@]}" 'stat -c%s /tmp/rag-kb-deploy.tar.gz')"
ok "已上传 $(( ARCHIVE_BYTES / 1024 )) KB"

# ── 2. 解包 + 同步 ────────────────────────────────────────────────
step "2/5 在服务器上解包并同步到 $DEPLOY_PATH"

# 先解到 staging，再 rsync --delete 覆盖过去。不直接 tar -C 解开，是为了让
# "本地删掉的文件"在服务器上也真的消失。
#
# ⚠️ 过 ssh 的变量只能是**单字**。ssh 会把它的 argv 用空格拼成一条命令字符串交给
# 远端 shell，所以本地 `VAR="$A $B"` 传过去会变成 `VAR=$A $B` —— 远端 shell 把
# $B 当成另一条命令去执行（报 `--exclude=xxx: command not found`）。
# 所以这里只传 0/1 开关，参数本身在远端脚本里拼。
if [ "$WITH_ENV" = "1" ]; then KEEP_ENV=1; else KEEP_ENV=0; fi

"${SSH[@]}" DEPLOY_PATH="$DEPLOY_PATH" KEEP_ENV="$KEEP_ENV" bash -s <<'REMOTE'
set -euo pipefail
cd "$DEPLOY_PATH"

rm -rf .deploy-staging
mkdir -p .deploy-staging
tar -xzf /tmp/rag-kb-deploy.tar.gz -C .deploy-staging

# 守卫：解包不全就**绝不能**往下走 rsync --delete —— 那会把 /opt/rag-kb 连同
# 线上代码一起清空。比起"多检查一次"，这个代价完全不对等。
missing=""
for f in docker-compose.yml Dockerfile app/main.py frontend/Dockerfile frontend/nginx.conf requirements.txt; do
  [ -e ".deploy-staging/$f" ] || missing="$missing $f"
done
if [ -n "$missing" ]; then
  echo "  解包结果缺少：$missing —— 已中止，线上文件未被改动" >&2
  exit 1
fi

# 服务器上的 .env（含现场生成的 POSTGRES_PASSWORD）默认**保留不覆盖**，理由见文件头。
if [ "$KEEP_ENV" = "1" ]; then
  EXCLUDES="--exclude=.deploy-info --exclude=.deploy-staging"
else
  EXCLUDES="--exclude=.env --exclude=.deploy.env --exclude=.deploy-info --exclude=.deploy-staging"
fi

# shellcheck disable=SC2086
rsync -a --delete $EXCLUDES .deploy-staging/ ./

rm -rf .deploy-staging /tmp/rag-kb-deploy.tar.gz
echo "  同步完成"
REMOTE
ok "代码已同步（.env 未动）"

# 把部署时的本地 git 状态留个记录：服务器上的代码不对应任何 commit，
# 这是"推送式"的固有代价，出事时靠这个文件倒查当时跑的是哪一版。
"${SSH[@]}" "printf '%s\n' \
  '部署时间        : $(date '+%Y-%m-%d %H:%M:%S')' \
  '本地 git HEAD   : $GIT_HEAD' \
  '未提交改动数    : $GIT_DIRTY' \
  '打包文件数      : $FILE_COUNT' \
  > '$DEPLOY_PATH/.deploy-info'"

# ── 3. .env 键名比对 ──────────────────────────────────────────────
step "3/5 比对本地与服务器的 .env 键名"
if [ ! -f "$REPO_ROOT/.env" ]; then
  warn "本机没有 .env，跳过比对"
else
  TMPD="$(mktemp -d)"
  # 只取键名、不取值 —— 比对的是"哪些配置项服务器上根本没有"，
  # 那才是 pydantic extra="ignore" 会静默吃掉的东西。
  LC_ALL=C grep -oE '^[A-Za-z_][A-Za-z0-9_]*' "$REPO_ROOT/.env" | LC_ALL=C sort -u > "$TMPD/local.keys"
  # LC_ALL=C 两端都要设：locale 不同会让同一个集合排出两种顺序，comm 就会误报。
  "${SSH[@]}" "LC_ALL=C grep -oE '^[A-Za-z_][A-Za-z0-9_]*' '$DEPLOY_PATH/.env' | tr -d '\r' | LC_ALL=C sort -u" > "$TMPD/remote.keys" || true

  MISSING="$(comm -23 "$TMPD/local.keys" "$TMPD/remote.keys" || true)"
  if [ -n "$MISSING" ]; then
    warn "以下配置项本机 .env 有、服务器 .env 没有 —— 服务器上会用 app/config.py 的默认值："
    printf '%s\n' "$MISSING" | sed 's/^/      /'
    warn "确认这些默认值是你想要的；否则把它们补进服务器的 $DEPLOY_PATH/.env 再重启 backend"
  else
    ok "两边键名一致（服务器没有缺项）"
  fi
fi

# ── 4. 构建 + 重启 ────────────────────────────────────────────────
if [ "$DO_BUILD" = "1" ]; then
  step "4/5 重建镜像${ONLY:+（仅 $ONLY）}"
  echo "  Docker 分层缓存生效，没变的层会直接命中；后端依赖层只在 requirements.txt 变化时重装"
  echo "  （构建日志较长，下面是完整输出）"
  echo
  if ! "${SSH[@]}" "cd '$DEPLOY_PATH' && docker compose build ${ONLY:-}"; then
    echo
    die "镜像构建失败 —— **线上服务未受影响**，仍在跑上一版镜像。修完再跑一次本脚本即可"
  fi
  ok "镜像已就绪"
else
  step "4/5 跳过镜像重建（--no-build）"
  warn "注意：后端与前端的代码是 COPY 进镜像的，只改代码不重建镜像 = 部署的仍是旧代码。"
  warn '这个开关只在「想单纯重启容器」时有意义。'
fi

step "  重启容器"
UP_OUT="$("${SSH[@]}" "cd '$DEPLOY_PATH' && docker compose up -d ${ONLY:-}" 2>&1)"
printf '%s\n' "$UP_OUT" | sed 's/^/  /'
# compose 打印每个容器是 Recreated / Created / Running。按**行数**统计会骗人
# （同一个容器在 Recreate→Recreated→Starting→Started 里出现四次），所以取容器名去重。
# 只认 Recreated|Created，不认 Started —— 后者对 Running 中的容器也会打印。
#
# ⚠️ 末尾的 `|| true` 不能省：没有任何容器被重建时 grep 退出码是 1，而本脚本开了
# `set -o pipefail`，管道状态会传出来，`set -e` 于是直接终止整个脚本 —— 表现为
# "什么都变了的时候跑得好好的，什么都没变时脚本无声无息地停在重启那一步"。
# 「没有变化」是**最常见**的一次部署，这条路径必须走通。
CHANGED="$(printf '%s\n' "$UP_OUT" | grep -E 'Recreated|Created' | awk '{print $2}' | sort -u | tr '\n' ' ' | sed 's/ $//' || true)"
if [ -n "$CHANGED" ]; then
  ok "被重建的容器：$CHANGED"
else
  warn "没有容器被重建 —— 代码推上去了但镜像没变（多半是 --no-build，或这次改动没触及镜像内容）"
fi

# ── 5. 健康检查 ───────────────────────────────────────────────────
step "5/5 健康检查"
HTTP_PORT="$("${SSH[@]}" "grep -E '^HTTP_PORT=' '$DEPLOY_PATH/.env' | tail -1 | cut -d= -f2 | tr -d '\r '" || true)"
HTTP_PORT="${HTTP_PORT:-82}"
echo "  探测 http://127.0.0.1:$HTTP_PORT/（宿主端口，取自服务器 .env 的 HTTP_PORT）"

# 在远端一次 ssh 里轮询，省掉每 5 秒开一条连接的开销。
# 给 180 秒：entrypoint 要等 DB → 建库建扩展 → 跑迁移，retrieval 模块导入期
# 还会调 jieba.initialize() 加载词典，冷启动本身就要十几秒。
if "${SSH[@]}" HTTP_PORT="$HTTP_PORT" DEPLOY_PATH="$DEPLOY_PATH" bash -s <<'REMOTE'
set -uo pipefail
for i in $(seq 1 36); do
  db="$(curl -sf --max-time 5 "http://127.0.0.1:$HTTP_PORT/api/health" 2>/dev/null \
        | sed -n 's/.*"database"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  if [ "$db" = "ok" ]; then
    # 顺带验一下前端：nginx 有没有真的把页面发出来。
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$HTTP_PORT/" 2>/dev/null)"
    echo "  database=ok，首页 HTTP $code（第 ${i} 次探测）"
    [ "$code" = "200" ] && exit 0
    echo "  后端是好的，但首页不是 200 —— 前端容器多半没起来" >&2
    exit 2
  fi
  sleep 5
done
echo "  等了 180 秒仍没等到 database=ok" >&2
cd "$DEPLOY_PATH"
echo "  --- docker compose ps ---" >&2
docker compose ps >&2 || true
echo "  --- backend 日志末尾 40 行 ---" >&2
docker compose logs --tail=40 backend >&2 || true
exit 1
REMOTE
then
  ok "部署完成，服务健康"
else
  die "健康检查未通过 —— 上面是现场状态。代码已经推上去了，回滚只需在服务器上改回旧代码再跑一次本脚本"
fi

printf '\n\033[32m==> 完成\033[0m  浏览器访问 http://%s:%s/\n' "$DEPLOY_HOST" "$HTTP_PORT"
echo "    本次部署的 git 状态记在服务器 $DEPLOY_PATH/.deploy-info"
