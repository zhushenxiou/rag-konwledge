# 前端镜像：多阶段。node 阶段做类型检查 + 打包，产物交给 nginx 托管。
# 构建上下文 = ./frontend（见 docker-compose.yml），配合 frontend/.dockerignore。
#
# 钉 node:22 的理由是与本机开发环境一致（实测 v22.20.0），且满足 Vite 8 的 Node 下限。
FROM node:22-alpine AS build

WORKDIR /app

# 构建期的包索引，默认官方 npm。**国内服务器上这个默认值很慢**：实测官方
# registry ~227 KB/s，换 npmmirror 后 2.4 MB/s（快 10 倍）。值由 docker-compose.yml
# 的 `build.args` 从 .env 读入，见 deploy.md 第 0 节。
#
# 用 ENV 而不是给下面两条命令各加一个 --registry：npm 与 pnpm **都**读
# `npm_config_registry` 这个环境变量，一处设置两处生效，不会漏掉其中一条。
# （换源不影响 `--frozen-lockfile`：pnpm-lock.yaml 里没有任何 registry 绝对地址，
# 实测 0 处，只存 integrity 校验和；npmmirror 是逐字节镜像，校验和一致。）
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV npm_config_registry=${NPM_REGISTRY}

# pnpm 必须**钉版本**：package.json 里没有 packageManager 字段、frontend/ 下也没有
# .npmrc，等于没有任何地方声明该用哪个 pnpm；而 node 官方镜像又不自带。
# 不钉的话某天装到新版，lockfileVersion 一旦变化，`--frozen-lockfile` 会直接失败。
# 钉成开发机同版本（本机实测 11.5.2），保证镜像里的依赖解析结果与本地一致。
RUN npm install -g pnpm@11.5.2

# 先清单、后源码。除了让依赖层可缓存，**顺序本身还是硬约束**：若反过来先
# `COPY . .` 再装依赖，会把开发机上那个 Windows 版 node_modules（含 esbuild /
# rollup 的 .node 原生二进制）拷进 Linux 构建上下文覆盖掉容器内装好的依赖，
# 报错形式是莫名其妙的 Exec format error 或原生模块加载失败。
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

# 保留 `vue-tsc -b`（即 pnpm build 的完整语义），不偷偷换成 `vite build` 绕过 ——
# 这是本项目唯一的静态类型闸门，类型错误就该让镜像构建失败，而不是把类型错误
# 带进运行时的 JS。
#
# 前提：vue-tsc **不加载 vite.config.ts**，因此不会重新生成
# src/auto-imports.d.ts / src/components.d.ts。这两个文件目前**已入库**，必须保持
# 入库 —— 一旦有人以「生成物不该进仓库」为由把它们加进 .gitignore，干净 clone 下的
# 构建会以一大堆 `Cannot find name 'ref'` 失败，而且现象只在 CI/镜像里出现。
#
# 小内存机器上 vue-tsc + rollup 可能 OOM（症状是构建进程被 OOM killer 杀掉、
# 日志里没有任何提示）：给构建机加 2G swap，或改在开发机构建后 docker save 传镜像。
RUN pnpm build

FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
# 覆盖镜像自带的默认站点配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 删掉官方 entrypoint 里那个「给原厂 default.conf 补 IPv6 监听」的脚本。
# 两个理由：
#   1) 对我们没意义 —— 它靠 apk/dpkg 校验当前 default.conf 是否**仍等于原厂那份**，
#      不等就跳过。我们 100% 覆盖了这份配置，它必然跳过，等于空转。
#   2) 它有害 —— alpine 分支会执行 `apk manifest nginx`，而该命令在某些环境
#      （实测本机 Docker Desktop）会**无限挂住**：entrypoint 卡在它那里，nginx 主进程
#      永远不启动，容器状态却是 Up，端口映射看着也正常，只是连上去被立刻关闭。
#      IPv6 监听已在 nginx.conf 里自己写上，不依赖它。
RUN rm -f /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh

EXPOSE 80
