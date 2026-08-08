import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { ChatEvent } from '../types'
import { ApiError } from './http'

/**
 * SSE 流式问答（基于 @microsoft/fetch-event-source）。
 *
 * 后端 POST /api/chat 返回 text/event-stream，事件按 SSE 规范：
 *   data: {json}\n\n
 *
 * 为什么用这个库而不是原生 EventSource：
 *   原生 EventSource 只支持 GET，无法携带 POST body；fetch-event-source
 *   支持任意 method/body、标准 SSE 解析、自动重连与错误分类。
 *
 * 注意：聊天流不做自动重连（重连 = 重复向模型发问），onerror 直接抛出让流终止。
 * 用户主动停止走 AbortSignal —— 该库会正常关闭流并使本 promise resolve。
 */
export async function streamChat(
  question: string,
  conversationId: string | null,
  onEvent: (ev: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  await fetchEventSource('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, conversation_id: conversationId }),
    signal,
    // 不监听 visibilitychange：默认行为是在窗口隐藏时 abort 当前流、恢复可见时
    // 用相同 POST body 重新发起请求（相当于把同一问题再问一次）。对 POST 问答
    // 而言会重复调用模型，并让后端为同一次提问创建出第二个会话。这里显式关闭，
    // 让流在后台继续走完。
    openWhenHidden: true,
    async onopen(res) {
      if (!res.ok) {
        let detail = `HTTP ${res.status}`
        try {
          const body = (await res.json()) as { detail?: string }
          if (body?.detail) detail = body.detail
        } catch {
          /* ignore */
        }
        throw new ApiError(res.status, detail)
      }
      const ct = res.headers.get('content-type') ?? ''
      if (!ct.startsWith('text/event-stream')) {
        throw new Error(`预期 text/event-stream，实际: ${ct}`)
      }
    },
    onmessage(ev) {
      if (!ev.data) return
      try {
        onEvent(JSON.parse(ev.data) as ChatEvent)
      } catch {
        /* 忽略无法解析的事件 */
      }
    },
    onerror(err) {
      // 不自动重连；用户主动停止走 signal，不会走到这里
      throw err
    },
  })
}
