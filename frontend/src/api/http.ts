/** 基于 axios 的轻量 JSON 客户端（统一 /api 前缀 + 错误处理）。 */

import axios from 'axios'

/** 统一的后端请求错误。 */
export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const instance = axios.create({
  timeout: 30000,
})

// 统一错误处理：透传后端 detail（FastAPI 错误体为 {detail: "..."}）
instance.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail ?? err.message
    return Promise.reject(new ApiError(status ?? 0, detail ?? '网络错误'))
  },
)

export const http = {
  get<T>(path: string): Promise<T> {
    return instance.get<T>(path).then((r) => r.data)
  },
  post<T>(path: string, data?: unknown): Promise<T> {
    return instance.post<T>(path, data).then((r) => r.data)
  },
  patch<T>(path: string, data?: unknown): Promise<T> {
    return instance.patch<T>(path, data).then((r) => r.data)
  },
  delete<T>(path: string): Promise<T> {
    return instance.delete<T>(path).then((r) => r.data)
  },
  /** multipart 上传：不手动设 Content-Type，让 axios 自动带 boundary。 */
  upload<T>(path: string, form: FormData): Promise<T> {
    return instance.post<T>(path, form).then((r) => r.data)
  },
}
