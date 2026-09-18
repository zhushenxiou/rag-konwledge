<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Key, Lock, User } from "@element-plus/icons-vue";
import { fetchCaptcha, login } from "@/api/auth";
import { setToken } from "@/auth/token";

const router = useRouter();
const route = useRoute();

const username = ref("");
const password = ref("");
const captchaCode = ref("");
const captchaId = ref("");
const captchaImage = ref("");
const loading = ref(false);

/** 拉一张新验证码，并清空已输入的那串——旧 captcha_id 已作废，留着只会提交必然失败的值。 */
async function refreshCaptcha() {
  captchaCode.value = "";
  try {
    const captcha = await fetchCaptcha();
    captchaId.value = captcha.captcha_id;
    captchaImage.value = captcha.image;
  } catch (err) {
    captchaImage.value = "";
    ElMessage.error(`验证码加载失败：${(err as Error).message}`);
  }
}

/** 登录成功后的回跳地址。只接受站内路径，防止 ?redirect= 被拿来跳外站。 */
function safeRedirect(): string {
  const raw = route.query.redirect;
  const target = typeof raw === "string" ? raw : "/";
  return target.startsWith("/") && !target.startsWith("//") ? target : "/";
}

async function submit() {
  if (loading.value) return;
  if (!username.value || !password.value) {
    ElMessage.warning("请输入账号和密码");
    return;
  }
  if (!captchaCode.value) {
    ElMessage.warning("请输入验证码");
    return;
  }

  loading.value = true;
  try {
    const result = await login(
      username.value,
      password.value,
      captchaId.value,
      captchaCode.value,
    );
    setToken(result.token);
    ElMessage.success("登录成功");
    await router.replace(safeRedirect());
  } catch (err) {
    ElMessage.error((err as Error).message || "登录失败");
    // 验证码是一次性的：不论失败原因是什么，都得换一张，
    // 否则重试提交的还是同一个已作废的 captcha_id。
    await refreshCaptcha();
  } finally {
    loading.value = false;
  }
}

onMounted(refreshCaptcha);
</script>

<template>
  <div class="flex h-full items-center justify-center bg-slate-50 px-6">
    <el-card
      shadow="never"
      class="w-full max-w-sm border-slate-200"
      :body-style="{ padding: '28px' }"
    >
      <div class="flex flex-col items-center">
        <el-avatar
          :size="44"
          shape="square"
          :style="{
            backgroundColor: '#4f46e5',
            color: '#fff',
            fontSize: '20px',
            fontWeight: 700,
          }"
        >
          知
        </el-avatar>
        <h1 class="mt-3 text-lg font-semibold text-slate-800">
          企业知识库问答系统
        </h1>
        <p class="mt-1 text-xs text-slate-400">请登录后使用</p>
      </div>

      <div class="mt-6 flex flex-col gap-4">
        <el-input
          v-model="username"
          size="large"
          placeholder="账号"
          :prefix-icon="User"
        />
        <el-input
          v-model="password"
          size="large"
          type="password"
          show-password
          placeholder="密码"
          :prefix-icon="Lock"
          @keyup.enter="submit"
        />

        <div class="flex items-center gap-3">
          <el-input
            v-model="captchaCode"
            size="large"
            maxlength="4"
            placeholder="验证码"
            :prefix-icon="Key"
            @keyup.enter="submit"
          />
          <img
            v-if="captchaImage"
            :src="captchaImage"
            alt="验证码"
            title="看不清？点击刷新"
            class="h-10 w-[110px] shrink-0 cursor-pointer rounded border border-slate-200"
            @click="refreshCaptcha"
          />
          <div
            v-else
            class="h-10 w-[110px] shrink-0 cursor-pointer rounded border border-dashed border-slate-300 text-center text-xs leading-10 text-slate-400"
            @click="refreshCaptcha"
          >
            点击加载
          </div>
        </div>

        <el-button
          type="primary"
          size="large"
          class="w-full"
          :loading="loading"
          @click="submit"
        >
          登录
        </el-button>
      </div>
    </el-card>
  </div>
</template>
