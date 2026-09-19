# 后端镜像。构建上下文 = 仓库根目录，配合根 .dockerignore。
#
# 选 python:3.13-slim 而非 3.12 或 alpine：
#   - 3.13 与开发环境（conda env `langchain`, Python 3.13）一致 —— 现有验证结果
#     都来自 3.13，换个解释器跑生产等于跑一套没测过的组合。
#   - 不用 -alpine：musl 下若干依赖没有预编译轮子会退化成源码编译，那才真的需要
#     gcc；glibc 的 slim 直接命中 manylinux 轮子，无需任何编译工具链。
FROM python:3.13-slim

# PYTHONUNBUFFERED: 不加则 entrypoint 里 init_db / alembic 的输出会攒在缓冲区，
#   `docker compose logs` 里看起来像跳步或乱序。
# PYTHONIOENCODING: 容器默认 locale 是 POSIX，不显式指定会让中文 print 抛
#   UnicodeEncodeError（这个坑本机 Windows 上已经踩过一次）。
# TZ: 容器默认 UTC，日志时间比本地少 8 小时，排查时容易看错时间线。
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    UPLOAD_DIR=/data/uploads \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖单独一层：requirements.txt 没变时，改业务代码不会重装依赖。
#
# 注：requirements.txt 全部是 `>=` 下界、没有上界，所以**同一份代码在不同时间
# 构建可能装到不同版本**。镜像一旦构建通过就别无谓重建（见 deploy.md）。
#
# 构建期的包索引，默认官方 PyPI。**国内服务器上这个默认值不可用**：实测官方
# 文件 CDN（files.pythonhosted.org）只有 ~34 KB/s，一个 19 MB 的 jieba 要下 9 分钟，
# 整轮依赖直接把你耗到放弃；换国内镜像后同一轮依赖只需几秒（实测腾讯云 3s、
# 清华 4s）。值由 docker-compose.yml 的 `build.args` 从 .env 读入，见 deploy.md 第 0 节。
# 放在 RUN 前面：ARG 的值参与后续 RUN 的缓存键，改源会正确地触发重装。
ARG PIP_INDEX_URL=https://pypi.org/simple

COPY requirements.txt ./
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt

# 只拷运行期需要的东西。tests/ eval_data/ frontend/ 由 .dockerignore 挡在外面。
# 不拷 pyproject.toml —— 它只有 [tool.pytest.ini_options] 一段，运行时无用。
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/

# 非 root 运行。**/data/uploads 必须在镜像里就存在、且属主正确**：
# 命名卷首次挂载到「镜像里已存在的目录」时，Docker 会把该目录的属主/权限复制进新卷；
# 若挂载点在镜像里不存在，卷根就是 root:root，非 root 进程 mkdir 直接 PermissionError。
# 那条路径的症状是「健康检查、登录、问答全正常，唯独上传 500」，极难定位。
# 顺带把 entrypoint 里的 CRLF 去掉：仓库在 Windows 上开发，.sh 一旦被检出成
# CRLF，`#!/bin/sh\r` 会报 "bad interpreter: No such file or directory"，
# 而且只在别的机器上复现。根 .gitattributes 的 `*.sh text eol=lf` 从源头挡一道，
# 这里再兜一道（注释不写在 && 续行链中间 —— 那会踩 Dockerfile 的解析陷阱）。
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /data \
    && chmod 755 /data/uploads \
    && chmod +x /app/deploy/backend-entrypoint.sh \
    && sed -i 's/\r$//' /app/deploy/backend-entrypoint.sh

USER appuser

EXPOSE 8000

# 用 /bin/sh 显式调用，彻底绕开「执行位被 git 丢掉」的问题
ENTRYPOINT ["/bin/sh", "/app/deploy/backend-entrypoint.sh"]
