<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { UploadFilled } from '@element-plus/icons-vue'
import {
  deleteDocument,
  listDocuments,
  renameDocument,
  retryDocument,
  uploadDocument,
} from '@/api/documents'
import { isUnauthorized } from '@/api/http'
import type { Document } from '@/types'
import DocumentDetailDialog from './components/DocumentDetailDialog.vue'
import DocumentTable from './components/DocumentTable.vue'

const route = useRoute()

// ---- 文档列表 + 上传 + 状态轮询（仅本页使用，直接放组件内，不做全局） ----
const documents = ref<Document[]>([])
const loading = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

function stopAutoRefresh() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

/** 有文档处于入库中则持续轮询，全部终态后停止。 */
function startAutoRefresh() {
  stopAutoRefresh()
  timer = setInterval(async () => {
    await refresh()
    const busy = documents.value.some((d) => d.status === 'pending' || d.status === 'processing')
    if (!busy) stopAutoRefresh()
  }, 1500)
}

async function refresh(status?: string) {
  loading.value = true
  try {
    documents.value = await listDocuments(status)
  } catch (e) {
    // 失败必须停轮询：否则会变成每 1.5s 一次的错误风暴（会话失效时尤其明显）
    stopAutoRefresh()
    // 401 由 http.ts 拦截器统一跳登录，这里不重复弹提示
    if (!isUnauthorized(e)) ElMessage.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function upload(file: File) {
  await uploadDocument(file)
  await refresh()
  startAutoRefresh()
}

async function remove(id: string) {
  await deleteDocument(id)
  await refresh()
}

async function retry(id: string) {
  await retryDocument(id)
  await refresh()
  startAutoRefresh()
}

async function rename(id: string, filename: string) {
  await renameDocument(id, filename)
  await refresh()
}

const detailId = ref<string | null>(null)

onMounted(async () => {
  await refresh()
  // 从引用卡片跳转过来时自动打开详情
  const open = route.query.open as string | undefined
  if (open) detailId.value = open
})

// 若在知识库页面切了 query，也响应
watch(
  () => route.query.open as string | undefined,
  (id) => {
    if (id) detailId.value = id
  },
)

// el-upload 前置校验：不合法返回 false 阻止该文件
function validateFile(file: File) {
  const ext = (file.name.split('.').pop() ?? '').toLowerCase()
  if (!ALLOWED.includes(ext)) {
    ElMessage.error(`不支持的文件类型 .${ext || '?'}，仅支持 ${ALLOWED.join(' / ')}`)
    return false
  }
  if (file.size > MAX_MB * 1024 * 1024) {
    ElMessage.error(`${file.name} 超过 ${MAX_MB}MB 限制`)
    return false
  }
  return true
}

// 自定义上传：走既有入库流程（上传 → 刷新 → 自动轮询状态）
async function doUpload(options: { file: File }) {
  try {
    await upload(options.file)
  } catch (e) {
    ElMessage.error(`${options.file.name}: ${(e as Error).message}`)
  }
}

async function onRetry(id: string) {
  try {
    await retry(id)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function onRename(id: string, filename: string) {
  try {
    await rename(id, filename)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function onRemove(id: string) {
  try {
    await ElMessageBox.confirm('确定删除该文档？将同时删除其向量与分块。', '删除文档', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  // 注意这段在 confirm 的 try/catch 之外，必须单独兜住，否则删除失败时
  // 只会走 Vue 的全局 errorHandler 打条日志，用户看不到任何原因
  try {
    await remove(id)
  } catch (e) {
    if (!isUnauthorized(e)) ElMessage.error((e as Error).message)
    return
  }
  if (detailId.value === id) detailId.value = null
}

const ALLOWED = ['txt', 'md', 'pdf', 'docx']
const MAX_MB = 10
</script>

<template>
  <div class="flex h-full flex-col overflow-hidden">
    <div class="min-h-0 flex-1 overflow-y-auto px-6 py-6">
      <!-- 页头 -->
      <div class="mb-5 flex items-center justify-between">
        <div>
          <h2 class="text-lg font-semibold text-slate-800">知识库管理</h2>
          <p class="mt-0.5 text-sm text-slate-500">
            知识库由文档构成：上传即入库（创建），可查看详情、重命名、失败重试与删除，实现完整 CRUD。
          </p>
        </div>
      </div>

      <!-- 上传区（el-upload 拖拽） -->
      <el-upload
        drag
        multiple
        accept=".txt,.md,.pdf,.docx"
        :show-file-list="false"
        :before-upload="validateFile"
        :http-request="doUpload"
        class="upload-drop mb-5"
      >
        <div class="py-8">
          <el-icon class="mb-2 text-4xl text-slate-300"><UploadFilled /></el-icon>
          <div class="text-sm font-medium text-slate-600">点击或拖拽文件到此处上传</div>
          <p class="mt-1 text-xs text-slate-400">
            支持一次选择多个文件，上传后自动开始入库
          </p>
        </div>
      </el-upload>

      <!-- 文档表格 -->
      <DocumentTable
        :documents="documents"
        :loading="loading"
        @view="detailId = $event"
        @retry="onRetry"
        @remove="onRemove"
        @rename="onRename"
      />
    </div>

    <!-- 文档详情（居中弹窗） -->
    <DocumentDetailDialog
      :open="!!detailId"
      :document-id="detailId"
      @close="detailId = null"
    />
  </div>
</template>
