/** 基于 axios 的轻量 JSON 客户端（统一 /api 前缀 + 错误处理）。 */

import axios from 'axios'
import { getToken, redirectToLogin } from '@/auth/token'

/** 统一的后端请求错误。 */
export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** 判断是否为「登录失效」。页面级 catch 用它跳过重复提示——401 已由拦截器统一跳登录。 */
export function isUnauthorized(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401
}

const instance = axios.create({
  timeout: 30000,
})

// 统一带上登录凭据。单点注入，覆盖全部走 axios 的请求
// （注意 SSE 的 /api/chat 不走这里，用的是 fetchEventSource，见 chat.ts）
instance.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 统一错误处理：透传后端 detail（FastAPI 错误体为 {detail: "..."}）
instance.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail ?? err.message
    // 登录失效统一跳登录页。但登录接口自身的 401（账号密码错）必须原样抛给登录页
    // 展示，否则用户看不到"账号或密码错误"，还会被跳转打断输入。
    const url: string = err.config?.url ?? ''
    if (status === 401 && !url.includes('/api/auth/login')) {
      redirectToLogin()
    }
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
