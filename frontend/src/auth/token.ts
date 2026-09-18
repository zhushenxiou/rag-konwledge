/**
 * 登录态凭据的存取与失效跳转。
 *
 * 为什么单独一个文件、而不是塞进 api/auth.ts 或 api/http.ts：
 *   http.ts（传输层）需要清 token + 跳登录，api/auth.ts（接口层）需要 http 发请求，
 *   两者放一起会形成 http → auth → http 的循环依赖。这里抽出一个 **import 列表为空**
 *   的叶子模块，两边都能安全引用。
 *
 * 为什么在 auth/ 而不是 api/：
 *   它管的不是"某个后端接口"，而是**凭据本身**（还带导航副作用），与 api/ 下那几个
 *   薄薄的 http 包装（documents / conversations / auth）不是一类东西。单独成层后，
 *   api/ 保持同构，鉴权概念也集中在这里。
 *
 * 为什么 token 不放"全局 store"：既有约定是页面状态直接放各页 index.vue、不引 Pinia。
 *   token 不属于页面状态，而是所有请求共享的传输层凭据。
 *
 * ⚠️ 维护约束：本文件**不得 import 任何业务模块**（api/、router/、views/ 都不行）。
 *    `api/http.ts` → `auth/token.ts` 这条边一旦反向，循环依赖立刻回来。
 */

const TOKEN_KEY = 'rag_kb_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export function isLoggedIn(): boolean {
  return !!getToken()
}

// 并发 401 去重：文档页轮询、会话列表等可能在同一瞬间一起失效，
// 不去重会对同一次跳转连发多次导航。
let redirecting = false

/**
 * 登录失效（401）统一出口：清 token 并跳到登录页，带上 redirect 便于登录后回跳。
 *
 * 用 window.location 整页跳转而非 router.push —— 传输层反向 import router 会形成
 * http → router → views → api → http 的循环依赖；代价是丢一次整页刷新，会话过期
 * 场景下可以接受。
 */
export function redirectToLogin(): void {
  clearToken()
  // 已在登录页就不再跳：否则登录接口自身返回 401 时会无限刷新
  if (window.location.pathname === '/login' || redirecting) return
  redirecting = true
  const redirect = window.location.pathname + window.location.search
  window.location.href = `/login?redirect=${encodeURIComponent(redirect)}`
}
