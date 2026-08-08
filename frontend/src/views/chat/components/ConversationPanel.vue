<script setup lang="ts">
import { ChatDotRound, Delete, Plus } from '@element-plus/icons-vue'
import type { Conversation } from '@/types'

// 纯展示组件：状态与逻辑都在父级 chat/index.vue，这里只负责渲染与发事件
defineProps<{ conversations: Conversation[]; loading: boolean; activeId: string | null }>()
const emit = defineEmits<{
  select: [id: string]
  new: []
  delete: [id: string]
}>()

async function onDelete(id: string) {
  try {
    await ElMessageBox.confirm('确定删除该会话？删除后不可恢复。', '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  emit('delete', id)
}
</script>

<template>
  <aside class="flex h-full w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
    <!-- 新建对话 -->
    <div class="p-3">
      <el-button type="primary" class="w-full" :icon="Plus" @click="emit('new')">
        新建对话
      </el-button>
    </div>

    <!-- 会话列表 -->
    <el-scrollbar class="min-h-0 flex-1">
      <p class="px-4 pt-1 pb-1 text-xs font-medium text-slate-400">会话</p>
      <el-menu class="conv-menu" :default-active="activeId ?? ''">
        <el-menu-item
          v-for="c in conversations"
          :key="c.id"
          :index="c.id"
          class="group"
          @click="emit('select', c.id)"
        >
          <el-icon><ChatDotRound /></el-icon>
          <span class="min-w-0 flex-1 truncate">{{ c.title }}</span>
          <el-button
            link
            type="danger"
            size="small"
            :icon="Delete"
            title="删除会话"
            :class="activeId === c.id ? '' : 'opacity-0 group-hover:opacity-100'"
            @click.stop="onDelete(c.id)"
          />
        </el-menu-item>
      </el-menu>
      <p v-if="!conversations.length && !loading" class="px-4 pt-2 text-xs text-slate-400">
        暂无会话，点击上方「新建对话」开始
      </p>
    </el-scrollbar>
  </aside>
</template>

<style>
/* 会话列表竖向 el-menu 融入白底侧栏 */
.conv-menu.el-menu {
  --el-menu-item-height: 40px;
  --el-menu-active-color: #4f46e5;
  --el-menu-hover-bg-color: #eef2ff;
  --el-menu-hover-text-color: #4338ca;
  background: transparent;
  border-right: none;
  --el-menu-base-level-padding: 12px;
}
</style>
