import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/home/index.vue'
import ChatView from '@/views/chat/index.vue'
import DocumentsView from '@/views/documents/index.vue'
import LoginView from '@/views/login/index.vue'
import { isLoggedIn } from '@/auth/token'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    // hideHeader：登录页整屏居中，不显示顶部导航（见 App.vue）
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { public: true, hideHeader: true },
    },
    { path: '/', name: 'home', component: HomeView },
    { path: '/chat', name: 'chat', component: ChatView },
    { path: '/documents', name: 'documents', component: DocumentsView },
  ],
})

/**
 * 全局登录守卫。
 *
 * 这只是体验层的拦截（避免未登录时白跑一屏请求）；真正的权限判定在后端
 * require_auth —— 手工往 localStorage 塞个假 token 也过不了接口。
 */
router.beforeEach((to) => {
  if (to.meta.public) {
    // 已登录还去登录页就直接回首页，避免重复登录
    return isLoggedIn() && to.name === 'login' ? { name: 'home' } : true
  }
  if (isLoggedIn()) return true
  return { name: 'login', query: { redirect: to.fullPath } }
})

export default router
