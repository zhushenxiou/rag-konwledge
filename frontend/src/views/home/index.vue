<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

const steps = [
  { title: '上传文档', desc: '支持 txt / md / pdf / docx，≤10MB，可一次选择多个' },
  {
    title: '自动入库',
    desc: '后台解析 → 分块 → 向量化 → 写入向量库，状态实时可观测',
  },
  {
    title: '提问获取回答',
    desc: '基于文档内容检索作答，回答附带引用来源与相似度',
  },
]

const features = [
  {
    icon: '📡',
    title: 'SSE 流式问答',
    desc: '回答边生成边显示，可随时停止；使用 @microsoft/fetch-event-source 稳定解析事件流',
  },
  {
    icon: '📎',
    title: '引用来源可追溯',
    desc: '每条回答附文档出处、相似度与片段，点击即可跳转原文详情',
  },
  {
    icon: '🛡️',
    title: '降低幻觉',
    desc: '检索无足够依据时明确提示"未找到依据"，不调用模型编造答案',
  },
  {
    icon: '🗂️',
    title: '知识库管理',
    desc: '上传即入库，支持内联重命名、失败重试、删除与分块详情，完整 CRUD',
  },
  {
    icon: '💬',
    title: '多会话持久化',
    desc: '会话与消息自动保存，随时新建、切换、删除，追问自动延续上下文',
  },
  {
    icon: '🔍',
    title: '向量检索',
    desc: '本地 bge 中文 Embedding + PostgreSQL pgvector 余弦相似度检索',
  },
]

const stack = [
  'Vue 3',
  'Vite',
  'TypeScript',
  'Tailwind CSS v4',
  'Element Plus',
  'FastAPI',
  'PostgreSQL + pgvector',
  'bge-small-zh-v1.5',
  'DeepSeek',
  'SSE',
]
</script>

<template>
  <div class="h-full overflow-y-auto">
    <div class="mx-auto max-w-4xl px-6 py-16">
      <!-- Hero -->
      <div class="text-center">
        <h1 class="text-3xl font-bold tracking-tight text-slate-900">
          企业知识库问答系统
        </h1>
        <p class="mx-auto mt-3 max-w-xl text-base leading-7 text-slate-500">
          基于 RAG 的智能问答平台：上传企业文档自动入库，回答实时流式返回，
          并附带可追溯的引用来源，从机制上降低大模型幻觉。
        </p>
        <div class="mt-8 flex items-center justify-center gap-3">
          <el-button type="primary" size="large" @click="router.push('/chat')">
            开始对话
          </el-button>
          <el-button size="large" @click="router.push('/documents')">
            管理知识库
          </el-button>
        </div>
      </div>

      <!-- 使用流程 -->
      <div class="mt-14 grid gap-4 sm:grid-cols-3">
        <el-card
          v-for="(s, i) in steps"
          :key="s.title"
          shadow="hover"
          :body-style="{ padding: '18px' }"
        >
          <div class="flex items-center gap-2">
            <el-tag size="small" round effect="light" type="primary">{{ i + 1 }}</el-tag>
            <h3 class="text-sm font-semibold text-slate-800">{{ s.title }}</h3>
          </div>
          <p class="mt-2 text-sm leading-6 text-slate-500">{{ s.desc }}</p>
        </el-card>
      </div>

      <!-- 特性 -->
      <div class="mt-10 grid gap-4 sm:grid-cols-2">
        <el-card
          v-for="f in features"
          :key="f.title"
          shadow="hover"
          :body-style="{ padding: '18px' }"
        >
          <div class="text-lg">{{ f.icon }}</div>
          <h3 class="mt-2 text-sm font-semibold text-slate-800">{{ f.title }}</h3>
          <p class="mt-1 text-sm leading-6 text-slate-500">{{ f.desc }}</p>
        </el-card>
      </div>

      <!-- 技术栈 -->
      <el-card
        shadow="never"
        :body-style="{ padding: '20px 24px' }"
        class="mt-10 border-slate-200"
      >
        <h3 class="text-sm font-semibold text-slate-800">技术栈</h3>
        <div class="mt-3 flex flex-wrap gap-2">
          <el-tag v-for="t in stack" :key="t" effect="plain">{{ t }}</el-tag>
        </div>
      </el-card>
    </div>
  </div>
</template>
