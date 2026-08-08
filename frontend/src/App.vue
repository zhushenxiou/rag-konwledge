<script setup lang="ts">
import AppHeader from './components/AppHeader.vue'
</script>

<template>
  <div class="flex h-full flex-col">
    <AppHeader />
    <main class="min-h-0 flex-1">
      <router-view />
    </main>
  </div>
</template>
