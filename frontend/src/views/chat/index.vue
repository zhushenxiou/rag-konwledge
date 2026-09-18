<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useVirtualizer } from '@tanstack/vue-virtual'
import { streamChat } from '@/api/chat'
import {
  createConversation,
  deleteConversation,
  getMessages,
  listConversations,
} from '@/api/conversations'
import { isUnauthorized } from '@/api/http'
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
  } catch (e) {
    // 401 交给拦截器跳登录；其余错误提示一下，否则侧栏只会静默空白
    if (!isUnauthorized(e)) ElMessage.error((e as Error).message)
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
      // 无依据消息在后端始终以「空来源的助手消息」落库（见 chat_service.NO_EVIDENCE_MSG）。
      // 用结构信号判定而非匹配文案，避免与后端措辞耦合、也防正常回答误标。
      noEvidence: m.role === 'assistant' && m.sources.length === 0,
    }))
    // 恢复会话状态：末条为无依据时头部 tag 与实时行为保持一致
    const last = messages.value[messages.value.length - 1]
    status.value = last?.noEvidence ? 'no_evidence' : messages.value.length ? 'done' : 'idle'
  } catch (e) {
    // 会话已切换却加载失败时，必须清空消息区，否则会留下「标题是新会话、
    // 内容还是旧会话」的错位。401 由拦截器接管，不必再收尾。
    if (isUnauthorized(e)) return
    messages.value = []
    status.value = 'error'
    ElMessage.error(`加载会话失败：${(e as Error).message}`)
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
  try {
    await removeConversation(id)
  } catch (e) {
    if (!isUnauthorized(e)) ElMessage.error((e as Error).message)
    return
  }
  if (conversationId.value === id) newChat()
}

// ---- 虚拟滚动（消息量大时只渲染可视窗口） ----
// 注意：el-scrollbar 通过 expose() 暴露 wrapRef，Vue 的 exposeProxy 用
// proxyRefs 包装，运行时拿到的已是【解包后的 DOM 元素】。而 EP 的 .d.ts
// 仍把 wrapRef 声明为 Ref，两者不一致 —— 这里用自定义窄类型标注为 DOM，
// 访问时直接取 wrapRef，不要再加 .value。
const scrollbarRef = ref<{ wrapRef: HTMLDivElement | undefined }>()

// 每行高度的初始估算
const USER_MSG_EST = 56
const ASSISTANT_MSG_EST = 220

const virtualizer = useVirtualizer(
  computed(() => ({
    count: messages.value.length,
    getScrollElement: () => scrollbarRef.value?.wrapRef ?? null,
    estimateSize: (index: number) =>
      messages.value[index]?.role === 'user' ? USER_MSG_EST : ASSISTANT_MSG_EST,
    overscan: 5,
    getItemKey: (index: number) => messages.value[index]?.id ?? index,
  })),
)

/** 滚动到底部：虚拟列表用 scrollToIndex 定位最后一条。 */
async function scrollToBottom() {
  await nextTick()
  const last = messages.value.length - 1
  if (last >= 0) virtualizer.value.scrollToIndex(last, { align: 'end' })
}

/** 是否接近底部（阈值内）——流式增长时只在接近底部才跟随，用户上翻不打扰。 */
function isNearBottom() {
  const wrap = scrollbarRef.value?.wrapRef
  return wrap ? wrap.scrollTop + wrap.clientHeight >= wrap.scrollHeight - 80 : false
}

// 新增消息 / 载入会话：直接滚到底（用户在互动中）
watch(() => messages.value.length, scrollToBottom)

// 流式内容增长 / 状态变化：仅在接近底部时跟随
watch(
  () => [status.value, messages.value[messages.value.length - 1]?.content],
  () => {
    if (isNearBottom()) scrollToBottom()
  },
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
        <el-tag
          v-if="status === 'no_evidence'"
          class="ml-3"
          type="warning"
          effect="light"
          size="small"
        >
          未找到足够依据
        </el-tag>
      </header>

      <!-- 消息区（虚拟滚动，只渲染可视窗口） -->
      <el-scrollbar ref="scrollbarRef" class="min-h-0 flex-1">
        <!-- 空状态 -->
        <el-empty
          v-if="!messages.length"
          class="h-full min-h-80"
          description="企业知识库问答"
        >
          <p class="mt-1 text-sm text-slate-500">
            基于 RAG 的智能问答，回答附带引用来源。先在「知识库」页上传资料，再向我提问。
          </p>
        </el-empty>

        <!-- 消息列表：外层撑总高，行用绝对定位 + translateY 排布 -->
        <div
          v-else
          class="relative mx-auto w-full max-w-3xl"
          :style="{ height: `${virtualizer.getTotalSize()}px` }"
        >
          <div
            v-for="row in virtualizer.getVirtualItems()"
            :key="messages[row.index].id"
            :data-index="row.index"
            :ref="(el) => virtualizer.measureElement(el as HTMLElement | null)"
            class="absolute left-0 top-0 w-full px-4 py-3"
            :style="{ transform: `translateY(${row.start}px)` }"
          >
            <MessageItem :message="messages[row.index]" />
          </div>
        </div>
      </el-scrollbar>

      <!-- 输入区 -->
      <ChatInput :streaming="status === 'streaming'" @send="sendQuestion" @stop="stop" />
    </div>
  </div>
</template>
