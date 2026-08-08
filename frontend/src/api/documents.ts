import { http } from './http'
import type { Document, DocumentDetail } from '../types'

export function listDocuments(status?: string): Promise<Document[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : ''
  return http.get<Document[]>(`/api/documents${qs}`)
}

export function getDocument(id: string): Promise<DocumentDetail> {
  return http.get<DocumentDetail>(`/api/documents/${id}`)
}

export function uploadDocument(file: File): Promise<Document> {
  const form = new FormData()
  form.append('file', file)
  return http.upload<Document>('/api/documents/upload', form)
}

export function deleteDocument(id: string): Promise<void> {
  return http.delete<void>(`/api/documents/${id}`)
}

export function retryDocument(id: string): Promise<Document> {
  return http.post<Document>(`/api/documents/${id}/retry`)
}

export function renameDocument(id: string, filename: string): Promise<Document> {
  return http.patch<Document>(`/api/documents/${id}`, { filename })
}

