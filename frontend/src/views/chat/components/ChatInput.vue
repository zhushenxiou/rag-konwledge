<script setup lang="ts">
import { ref } from 'vue'
import { Promotion, VideoPause } from '@element-plus/icons-vue'

const props = defineProps<{ streaming: boolean }>()
const emit = defineEmits<{
  send: [text: string]
  stop: []
}>()

const text = ref('')

function onSend() {
  const value = text.value.trim()
  if (!value || props.streaming) return
  emit('send', value)
  text.value = ''
}

function onKeydown(e: Event | KeyboardEvent) {
  // 中文输入法组合期间按 Enter 是"确认候选词"，不是发送，必须放行
  const ke = e as KeyboardEvent
  if (ke.isComposing || ke.keyCode === 229) return
  // Enter 发送，Shift+Enter 换行
  if (ke.key === 'Enter' && !ke.shiftKey) {
    ke.preventDefault()
    onSend()
  }
}
</script>

<template>
  <div class="mx-auto w-full max-w-3xl px-4 pb-6">
    <div class="flex items-end gap-2">
    <el-input
      v-model="text"
      type="textarea"
      :autosize="{ minRows: 1, maxRows: 6 }"
      resize="none"
      :disabled="streaming"
      placeholder="输入你的问题，例如：分块策略的默认参数是多少？"
      class="flex-1"
      @keydown="onKeydown"
    />

    <!-- 流式中 -> 停止；否则 -> 发送 -->
    <el-button
      v-if="streaming"
      type="danger"
      circle
      :icon="VideoPause"
      title="停止生成"
      @click="emit('stop')"
    />
    <el-button
      v-else
      type="primary"
      circle
      :icon="Promotion"
      :disabled="!text.trim()"
      title="发送"
      @click="onSend"
    />
    </div>
    <p class="mt-2 text-center text-xs text-slate-400">
      Enter 发送 · Shift+Enter 换行 · 回答附带引用来源
    </p>
  </div>
</template>
