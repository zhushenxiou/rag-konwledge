<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { streamChat } from '@/api/chat'
import {
  createConversation,
  deleteConversation,
  getMessages,
  listConversations,
} from '@/api/conversations'
import type { ChatEvent, ChatStatus, Conversation, DisplayMessage } from '@/types'
import ChatInput from './components/ChatInput.vue'
import ConversationPanel from './components/ConversationPanel.vue'
import MessageItem from './components/MessageItem.vue'

// ---- 对话状态（仅本页使用，直接放组件内，不做全局） ----
const messages = ref<DisplayMessage[]>([])
const status = ref<ChatStatus>('idle')
const conversationId = ref<string | null>(null)
let controller: AbortController | null = null
let seq = 0

// ---- 侧边栏会话列表 ----
const conversations = ref<Conversation[]>([])
const conversationsLoading = ref(false)

async function refreshConversations() {
  conversationsLoading.value = true
  try {
    conversations.value = await listConversations()
  } finally {
    conversationsLoading.value = false
  }
}

async function removeConversation(id: string) {
  await deleteConversation(id)
  await refreshConversations()
}

onMounted(refreshConversations)

// ---- 聊天逻辑 ----
/** 推入一条消息并返回【响应式元素】。
 *  关键：不能返回 push 前的原始对象 —— 原始对象上的字段变更绕过响应式
 *  set 拦截，不会触发视图更新。通过 messages.value[最后下标] 读回被代理的
 *  元素，后续 assistant.content += ... 才能触发重渲染。 */
function pushMessage(role: 'user' | 'assistant', content: string): DisplayMessage {
  messages.value.push({ id: `local-${++seq}`, role, content, sources: null })
  return messages.value[messages.value.length - 1]
}

/** 发送问题：推入用户消息 + 空的助手占位，然后流式填充。 */
async function sendQuestion(question: string) {
  const trimmed = question.trim()
  if (!trimmed || status.value === 'streaming') return

  pushMessage('user', trimmed)
  const assistant = pushMessage('assistant', '')
  assistant.streaming = true
  status.value = 'streaming'

  controller = new AbortController()

  // 没有会话时先显式创建（标题取问题前 30 字，与后端隐式行为一致）。
  // 关键：把 conversation_id 在发问前固定下来，后续任何被重复发出的请求
  // 都会复用同一会话，后端不会再为同一次提问创建第二个会话。
  if (!conversationId.value) {
    try {
      const conv = await createConversation(trimmed.slice(0, 30))
      conversationId.value = conv.id
    } catch {
      controller = null
      assistant.error = true
      assistant.content = '创建会话失败，请重试'
      assistant.streaming = false
      status.value = 'error'
      return
    }
  }

  let backendError = ''
  try {
    await streamChat(
      trimmed,
      conversationId.value,
      (ev: ChatEvent) => {
        switch (ev.type) {
          case 'chunk':
            assistant.content += ev.content
            break
          case 'sources':
            assistant.sources = ev.sources
            break
          case 'no_evidence':
            assistant.noEvidence = true
            assistant.content = ev.message
            status.value = 'no_evidence'
            break
          case 'error':
            backendError = ev.message
            break
          case 'done':
            conversationId.value = ev.conversation_id
            break
        }
      },
      controller.signal,
    )
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // 用户主动停止：保留已生成内容
      status.value = 'done'
    } else {
      assistant.error = true
      assistant.content = assistant.content || `请求失败：${(err as Error).message}`
      status.value = 'error'
    }
  } finally {
    assistant.streaming = false
    controller = null
    // 流正常结束且未收到 no_evidence/error 时收敛为 done
    if (status.value === 'streaming') {
      status.value = backendError ? 'error' : 'done'
      if (backendError) assistant.error = true
    }
    if (conversationId.value) refreshConversations()
  }
}

/** 停止当前生成（AbortController 中断 fetch 流）。 */
function stop() {
  controller?.abort()
}

/** 载入既有会话的历史消息。 */
async function loadConversation(conv: Conversation) {
  stop()
  conversationId.value = conv.id
  status.value = 'done'
  try {
    const msgs = await getMessages(conv.id)
    messages.value = msgs.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      sources: m.sources.length ? m.sources : null,
      noEvidence: m.content.includes('没有找到足够依据'),
    }))
  } catch {
    // 忽略：历史加载失败时静默保留当前空状态
  }
}

function newChat() {
  stop()
  messages.value = []
  conversationId.value = null
  status.value = 'idle'
}

// ---- 侧边栏事件 ----
function onSelectConversation(id: string) {
  const conv = conversations.value.find((c) => c.id === id)
  if (conv) loadConversation(conv)
}

async function onDeleteConversation(id: string) {
  await removeConversation(id)
  if (conversationId.value === id) newChat()
}

// ---- 滚动到底部 ----
const scrollEl = ref<HTMLElement | null>(null)

async function scrollToBottom() {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

// 消息新增 / 内容增长 / 状态变化时滚动到底部
watch(
  () => [
    messages.value.length,
    status.value,
    messages.value[messages.value.length - 1]?.content,
  ],
  scrollToBottom,
)
</script>

<template>
  <div class="flex h-full">
    <!-- 会话面板 -->
    <ConversationPanel
      :conversations="conversations"
      :loading="conversationsLoading"
      :active-id="conversationId"
      @select="onSelectConversation"
      @new="newChat"
      @delete="onDeleteConversation"
    />

    <!-- 对话区 -->
    <div class="flex min-w-0 flex-1 flex-col">
      <!-- 顶部 -->
      <header class="flex h-14 shrink-0 items-center border-b border-slate-200 bg-white/70 px-6 backdrop-blur">
      <h2 class="text-sm font-semibold text-slate-700">
        {{ conversationId ? '对话' : '新对话' }}
      </h2>
      <span
        v-if="status === 'no_evidence'"
        class="ml-3 rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-600"
      >
        未找到足够依据
      </span>
    </header>

    <!-- 消息区 -->
    <div ref="scrollEl" class="min-h-0 flex-1 overflow-y-auto">
      <!-- 空状态 -->
      <div
        v-if="!messages.length"
        class="mx-auto flex h-full max-w-2xl flex-col items-center justify-center px-6 text-center"
      >
        <el-avatar
          :size="56"
          class="mb-4"
          :style="{ backgroundColor: '#4f46e5', color: '#fff', fontSize: '24px', fontWeight: 700 }"
        >
          知
        </el-avatar>
        <h2 class="text-xl font-semibold text-slate-800">企业知识库问答</h2>
        <p class="mt-2 text-sm text-slate-500">
          基于 RAG 的智能问答，回答附带引用来源。先在「知识库」页上传资料，再向我提问。
        </p>
      </div>

      <!-- 消息列表 -->
      <div v-else class="mx-auto flex w-full max-w-3xl flex-col gap-5 px-4 py-6">
        <MessageItem v-for="m in messages" :key="m.id" :message="m" />
        <div class="h-1" />
      </div>
    </div>

    <!-- 输入区 -->
    <ChatInput :streaming="status === 'streaming'" @send="sendQuestion" @stop="stop" />
    </div>
  </div>
</template>
