<script setup lang="ts">
import type { DisplayMessage } from '@/types'
import SourceCard from './SourceCard.vue'

defineProps<{ message: DisplayMessage }>()
</script>

<template>
  <!-- 用户消息：右侧气泡 -->
  <div v-if="message.role === 'user'" class="flex justify-end">
    <div
      class="max-w-[78%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-primary-600 px-4 py-2.5 text-sm leading-6 text-white shadow-sm"
    >
      {{ message.content }}
    </div>
  </div>

  <!-- 助手消息：左侧卡片 -->
  <div v-else class="flex justify-start">
    <div class="w-full max-w-[85%]">
      <div class="flex items-center gap-1.5 pb-1.5">
        <el-avatar
          :size="24"
          :style="{ backgroundColor: '#eef2ff', color: '#4338ca', fontSize: '11px', fontWeight: 700 }"
        >
          AI
        </el-avatar>
        <span class="text-xs text-slate-400">智能助手</span>
        <el-tag v-if="message.streaming" size="small" type="primary" effect="light">
          生成中…
        </el-tag>
      </div>

      <el-card shadow="never" class="assistant-card" :body-style="{ padding: '12px 16px' }">
        <!-- 无依据提示 -->
        <el-alert
          v-if="message.noEvidence"
          type="warning"
          :closable="false"
          show-icon
          :title="message.content"
        />
        <!-- 错误提示 -->
        <el-alert
          v-else-if="message.error"
          type="error"
          :closable="false"
          show-icon
          :title="message.content"
        />
        <!-- 正常回答 -->
        <div
          v-else
          class="whitespace-pre-wrap break-words text-sm leading-6 text-slate-700"
          :class="{ 'typing-cursor': message.streaming }"
        >
          <template v-if="message.content">{{ message.content }}</template>
          <template v-else-if="message.streaming">
            <span class="text-slate-400">正在检索知识库并生成回答…</span>
          </template>
        </div>

        <!-- 引用来源（折叠） -->
        <el-collapse v-if="message.sources && message.sources.length" class="source-collapse">
          <el-collapse-item :title="`引用来源（${message.sources.length}）`" name="sources">
            <div class="space-y-1.5">
              <SourceCard v-for="s in message.sources" :key="s.chunk_id" :source="s" />
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-card>
    </div>
  </div>
</template>

<style>
/* 引用来源折叠区与消息卡片更紧凑 */
.assistant-card.el-card {
  border-color: #e2e8f0;
  border-radius: 12px;
}
.source-collapse.el-collapse {
  margin-top: 12px;
  border-top: 1px solid #f1f5f9;
  padding-top: 10px;
  border-bottom: none;
  --el-collapse-header-height: auto;
}
.source-collapse .el-collapse-item__header {
  padding: 0;
  font-size: 12px;
  font-weight: 500;
  color: #94a3b8;
  border-bottom: none;
}
.source-collapse .el-collapse-item__content {
  padding-bottom: 0;
}
</style>
