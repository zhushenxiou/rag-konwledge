<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { getDocument } from '@/api/documents'
import type { DocumentDetail } from '@/types'

const props = defineProps<{ open: boolean; documentId: string | null }>()
const emit = defineEmits<{ close: [] }>()

const loading = ref(false)
const error = ref('')
const doc = ref<DocumentDetail | null>(null)

const visible = computed({
  get: () => props.open,
  set: (v: boolean) => {
    if (!v) emit('close')
  },
})

// 每次打开时重新拉取详情
watch(
  () => [props.open, props.documentId] as const,
  async ([open, id]) => {
    if (!open || !id) return
    loading.value = true
    error.value = ''
    doc.value = null
    try {
      doc.value = await getDocument(id)
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

function chunkMetaText(chunk: { metadata: Record<string, unknown> | null }) {
  if (!chunk.metadata) return ''
  const parts: string[] = []
  if (chunk.metadata.pages) parts.push(`第 ${chunk.metadata.pages} 页`)
  if (chunk.metadata.heading) parts.push(String(chunk.metadata.heading))
  return parts.join(' · ')
}
</script>

<template>
  <el-dialog
    v-model="visible"
    width="560px"
    align-center
    :close-on-click-modal="false"
    destroy-on-close
  >
    <template #header>
      <div class="min-w-0 pr-8">
        <h3 class="truncate text-sm font-semibold text-slate-800">
          {{ doc?.filename || '文档详情' }}
        </h3>
        <p v-if="doc" class="mt-1 flex items-center gap-2 text-xs text-slate-400">
          <span>{{ doc.chunk_count }} 个分块</span>
          <el-tag
            :type="
              doc.status === 'ready'
                ? 'success'
                : doc.status === 'failed'
                  ? 'danger'
                  : 'warning'
            "
            size="small"
            effect="plain"
          >
            {{ doc.status }}
          </el-tag>
        </p>
      </div>
    </template>

    <div class="min-h-40">
      <!-- 加载中 -->
      <el-skeleton v-if="loading" :rows="6" animated />
      <!-- 加载失败 -->
      <p v-else-if="error" class="py-8 text-center text-sm text-red-500">
        {{ error }}
      </p>
      <!-- 分块列表 -->
      <div v-else class="max-h-[55vh] space-y-3 overflow-y-auto pr-1">
        <div
          v-for="chunk in doc?.chunks ?? []"
          :key="chunk.id"
          class="rounded-lg border border-slate-200 bg-slate-50 p-3"
        >
          <div class="mb-1 flex items-center justify-between text-xs text-slate-400">
            <span>#{{ chunk.chunk_index + 1 }}</span>
            <span v-if="chunkMetaText(chunk)">{{ chunkMetaText(chunk) }}</span>
          </div>
          <p class="whitespace-pre-wrap text-xs leading-5 text-slate-600">
            {{ chunk.content }}
          </p>
        </div>
        <p v-if="doc && !doc.chunks.length" class="py-8 text-center text-sm text-slate-400">
          暂无分块数据
        </p>
      </div>
    </div>
  </el-dialog>
</template>
