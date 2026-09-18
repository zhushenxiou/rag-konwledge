<script setup lang="ts">
import { useRoute } from 'vue-router'
import AppHeader from './components/AppHeader.vue'

const route = useRoute()
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 登录页走 meta.hideHeader，整屏居中、不显示顶部导航 -->
    <AppHeader v-if="!route.meta.hideHeader" />
    <main class="min-h-0 flex-1">
      <router-view />
    </main>
  </div>
</template>
