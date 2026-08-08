<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
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

    <!-- 右侧说明 -->
    <span class="hidden text-xs text-slate-400 sm:block">RAG 智能问答</span>
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
