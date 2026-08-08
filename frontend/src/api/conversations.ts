import { http } from './http'
import type { Conversation, Message } from '../types'

export function listConversations(): Promise<Conversation[]> {
  return http.get<Conversation[]>('/api/conversations')
}

export function createConversation(title?: string): Promise<Conversation> {
  return http.post<Conversation>('/api/conversations', title ? { title } : undefined)
}

export function getMessages(conversationId: string): Promise<Message[]> {
  return http.get<Message[]>(`/api/conversations/${conversationId}/messages`)
}

export function deleteConversation(conversationId: string): Promise<void> {
  return http.delete<void>(`/api/conversations/${conversationId}`)
}
