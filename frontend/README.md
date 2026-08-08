# 企业知识库问答 —— 前端

Vue 3 + TypeScript + Vite + vue-router + Tailwind CSS v4 实现的知识库问答前端，对接后端 RAG 服务（FastAPI :8000）。

## 功能

- **首页** `/`：落地页，介绍项目（特性 / 技术栈 / 使用流程），CTA 直达对话与知识库
- **对话页** `/chat`：SSE 流式问答（增量显示、可停止生成）、引用来源折叠展示（点击跳转文档详情）、无依据/错误提示、左侧会话列表新建/切换/删除
- **知识库页** `/documents`：知识库 CRUD（拖拽/点击上传 txt/md/pdf/docx ≤10MB、状态徽章 排队→处理→就绪/失败、失败重试、**内联重命名**、删除、分块详情抽屉）

全局顶部为 **header 风格 tab 导航**（首页 / 对话 / 知识库管理），其余页面保持最简布局。

## 技术要点

- **SSE 流式**：后端 `POST /api/chat` 返回 `text/event-stream`。原生 `EventSource` 只支持 GET、无法携带 POST body，故使用 `@microsoft/fetch-event-source` 库（`src/api/chat.ts`）：`onopen` 校验状态码与 `text/event-stream` Content-Type，`onmessage` 逐事件派发，`signal` 中止时取消请求。
- **UI 组件**：全部走 Element Plus **按需导入**（`unplugin-auto-import` + `unplugin-vue-components` + `ElementPlusResolver`，`directives: true` 支持 `v-loading`），d.ts 自动生成在 `src/` 下纳入类型检查。导航 `el-menu`、上传 `el-upload`、表格 `el-table`、卡片 `el-card`、折叠 `el-collapse`、弹窗 `el-dialog`、消息 `ElMessage`、确认框 `ElMessageBox`、图标 `@element-plus/icons-vue`；仅聊天气泡等专属场景保留极少量 Tailwind 布局。
- **状态管理**：不用全局 store —— 页面状态直接放在各页面的 `index.vue` 组件内（`ref`），子组件只做展示、通过 props 收数据、通过 emits 上报事件。聊天状态（消息 / 状态机 / 会话列表）在 `chat/index.vue`，知识库列表与轮询在 `documents/index.vue`。切换页面后状态会重置，会话与文档均持久化在服务端，可随时从侧边栏重新载入。
- **代理**：`vite.config.ts` 中 `/api` 代理到 `http://localhost:8000`，开发时无跨域。

## 开发

```bash
pnpm install
pnpm dev          # http://localhost:5173 （需先启动后端 :8000）
pnpm build        # 产物 frontend/dist
```

## 目录

```
src/
├── router/        # 路由：/ 落地页，/chat 对话页，/documents 知识库页
├── api/           # axios 封装(http.ts) + documents/conversations/chat(SSE)
├── views/
│   ├── home/index.vue              # 落地页
│   ├── chat/                       # 对话页
│   │   ├── index.vue
│   │   └── components/             # ChatInput / ConversationPanel / MessageItem / SourceCard
│   └── documents/                  # 知识库管理页
│       ├── index.vue
│       └── components/             # DocumentTable / DocumentDetailDialog
├── components/    # 公共组件：AppHeader（页面私有组件放各页面 components/ 下）
└── types.ts       # 与后端 schemas 对齐的类型
```

> 页面结构约定：每个页面是 `views/<页面>/index.vue`，页面私有组件放 `views/<页面>/components/`；仅跨页面复用组件放公共 `components/`。导入统一用 `@/` 别名（`@` → `src/`，配置在 `vite.config.ts` + `tsconfig.app.json`）。
```
