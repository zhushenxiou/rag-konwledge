<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { UserFilled } from '@element-plus/icons-vue'
import { fetchMe, logout } from '@/api/auth'
import { clearToken } from '@/auth/token'

const route = useRoute()
const router = useRouter()

const username = ref('')

// 启动时用本地 token 问一次后端，既拿到展示用用户名，也顺带校验存量 token 是否
// 还有效：已失效（如后端重启过）时 http.ts 拦截器会统一跳登录页。
onMounted(async () => {
  try {
    username.value = (await fetchMe()).username
  } catch {
    // 401 已由拦截器处理；其他错误（如后端没起来）静默降级，不阻塞页面渲染
  }
})

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', { type: 'warning' })
  } catch {
    return // 用户取消
  }
  try {
    await logout() // 通知后端撤销 token
  } catch {
    // 后端不可达或 token 本已失效都无所谓，本地清掉即可
  }
  clearToken()
  router.replace('/login')
}
</script>

<template>
  <header
    class="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6"
  >
    <!-- 品牌 -->
    <div class="flex items-center gap-2.5">
      <el-avatar
        :size="30"
        shape="square"
        :style="{ backgroundColor: '#4f46e5', color: '#fff', fontSize: '14px', fontWeight: 700 }"
      >
        知
      </el-avatar>
      <span class="text-sm font-semibold text-slate-700">企业知识库</span>
    </div>

    <!-- 页面 tab（header 风格） -->
    <el-menu
      mode="horizontal"
      :router="true"
      :default-active="route.path"
      :ellipsis="false"
      class="app-nav"
    >
      <el-menu-item index="/">首页</el-menu-item>
      <el-menu-item index="/chat">对话</el-menu-item>
      <el-menu-item index="/documents">知识库管理</el-menu-item>
    </el-menu>

    <!-- 当前用户 + 退出登录 -->
    <div class="flex items-center gap-2">
      <span class="hidden items-center gap-1 text-xs text-slate-500 sm:flex">
        <el-icon><UserFilled /></el-icon>
        {{ username || '未登录' }}
      </span>
      <el-button link type="primary" size="small" @click="handleLogout">退出登录</el-button>
    </div>
  </header>
</template>

<style>
/* 让横向 el-menu 贴合 56px header */
.app-nav.el-menu {
  --el-menu-item-height: 56px;
  --el-menu-active-color: #4f46e5;
  background: transparent;
  border-bottom: none;
}
</style>
