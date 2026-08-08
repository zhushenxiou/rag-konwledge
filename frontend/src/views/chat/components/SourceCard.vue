<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { SourceRef } from '@/types'

const props = defineProps<{ source: SourceRef }>()
const router = useRouter()

function percent(sim: number) {
  return `${(sim * 100).toFixed(1)}%`
}

function openDocument() {
  router.push({ path: '/documents', query: { open: props.source.document_id } })
}
</script>

<template>
  <el-card
    shadow="hover"
    class="cursor-pointer"
    :body-style="{ padding: '10px 12px' }"
    @click="openDocument"
  >
    <div class="flex items-center justify-between gap-2">
      <span class="truncate text-xs font-medium text-slate-700">
        📄 {{ source.filename }}
      </span>
      <el-tag size="small" type="primary" effect="plain">
        相似度 {{ percent(source.similarity) }}
      </el-tag>
    </div>
    <p class="mt-1 line-clamp-3 text-xs leading-5 text-slate-500">{{ source.snippet }}</p>
  </el-card>
</template>
