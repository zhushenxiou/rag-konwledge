import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/home/index.vue'
import ChatView from '@/views/chat/index.vue'
import DocumentsView from '@/views/documents/index.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/chat', name: 'chat', component: ChatView },
    { path: '/documents', name: 'documents', component: DocumentsView },
  ],
})

export default router
