/** 与后端 schemas.py 对齐的类型定义。 */

export type DocStatus = 'pending' | 'processing' | 'ready' | 'failed'

export interface Document {
  id: string
  filename: string
  file_size: number
  source_type: string
  status: DocStatus
  chunk_count: number
  error_message: string
  created_at: string
}

export interface Chunk {
  id: string
  chunk_index: number
  content: string
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface DocumentDetail extends Document {
  chunks: Chunk[]
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

/** 引用来源（messages.sources 中的一项）。 */
export interface SourceRef {
  document_id: string
  filename: string
  chunk_id: string
  chunk_index: number
  similarity: number
  snippet: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources: SourceRef[]
  created_at: string
}

/** SSE 聊天事件。 */
export type ChatEvent =
  | { type: 'chunk'; content: string }
  | { type: 'sources'; sources: SourceRef[] }
  | { type: 'no_evidence'; message: string }
  | { type: 'error'; message: string }
  | { type: 'done'; conversation_id: string }

/** 聊天页面展示用消息（流式中的助手消息 content 会持续增长）。 */
export interface DisplayMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources: SourceRef[] | null
  noEvidence?: boolean
  streaming?: boolean
  error?: boolean
}

export type ChatStatus = 'idle' | 'streaming' | 'done' | 'no_evidence' | 'error'
