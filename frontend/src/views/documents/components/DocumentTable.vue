<script setup lang="ts">
import { ref } from 'vue'
import { Check, Close, Delete, EditPen, RefreshRight, View } from '@element-plus/icons-vue'
import type { DocStatus, Document } from '@/types'

defineProps<{ documents: Document[]; loading: boolean }>()
const emit = defineEmits<{
  view: [id: string]
  retry: [id: string]
  remove: [id: string]
  rename: [id: string, filename: string]
}>()

// 内联重命名
const renamingId = ref<string | null>(null)
const renameValue = ref('')

function startRename(d: Document) {
  renamingId.value = d.id
  renameValue.value = d.filename
}

function cancelRename() {
  renamingId.value = null
  renameValue.value = ''
}

function confirmRename() {
  const name = renameValue.value.trim()
  if (!renamingId.value || !name) {
    cancelRename()
    return
  }
  emit('rename', renamingId.value, name)
  cancelRename()
}

const statusMeta: Record<DocStatus, { label: string; tagType: 'success' | 'warning' | 'danger' | 'info' }> = {
  pending: { label: '排队中', tagType: 'info' },
  processing: { label: '处理中', tagType: 'warning' },
  ready: { label: '已就绪', tagType: 'success' },
  failed: { label: '失败', tagType: 'danger' },
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <div class="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
    <el-table
      :data="documents"
      v-loading="loading"
      empty-text="暂无文档，先上传一份资料吧"
      :header-cell-style="{ background: '#f8fafc', color: '#64748b' }"
      style="width: 100%"
    >
      <el-table-column label="文件名" min-width="220">
        <template #default="{ row }">
          <!-- 重命名编辑态 -->
          <div v-if="renamingId === row.id" class="flex items-center gap-1">
            <el-input
              v-model="renameValue"
              size="small"
              autofocus
              @keydown.enter="confirmRename"
              @keydown.esc="cancelRename"
            />
            <el-button link type="success" :icon="Check" title="保存" @click="confirmRename" />
            <el-button link :icon="Close" title="取消" @click="cancelRename" />
          </div>
          <!-- 普通显示态 -->
          <template v-else>
            <span class="font-medium text-slate-700">{{ row.filename }}</span>
            <p
              v-if="row.status === 'failed' && row.error_message"
              class="mt-0.5 truncate text-xs text-red-500"
              :title="row.error_message"
            >
              {{ row.error_message }}
            </p>
          </template>
        </template>
      </el-table-column>

      <el-table-column label="类型" width="90">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.source_type }}</el-tag>
        </template>
      </el-table-column>

      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag size="small" :type="statusMeta[row.status as DocStatus].tagType">
            {{ statusMeta[row.status as DocStatus].label }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="chunk_count" label="分块数" width="80" align="center" />

      <el-table-column label="大小" width="90">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>

      <el-table-column label="上传时间" width="130">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>

      <el-table-column label="操作" width="210" align="right">
        <template #default="{ row }">
          <el-button link type="primary" :icon="View" @click="emit('view', row.id)">
            详情
          </el-button>
          <el-button link type="primary" :icon="EditPen" @click="startRename(row as Document)">
            重命名
          </el-button>
          <el-button
            v-if="row.status === 'failed'"
            link
            type="warning"
            :icon="RefreshRight"
            @click="emit('retry', row.id)"
          >
            重试
          </el-button>
          <el-button link type="danger" :icon="Delete" @click="emit('remove', row.id)">
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>
