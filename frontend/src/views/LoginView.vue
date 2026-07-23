<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { getSetupStatus } from '@/api/setup'
import { message } from 'ant-design-vue'

const router = useRouter()
const auth = useAuthStore()

const form = reactive({ username: '', password: '' })
const loading = ref(false)

async function onSubmit() {
  if (!form.username || !form.password) {
    message.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    // Phase 10 §6.6：首次登录且向导未完成 → 跳转 /onboarding
    try {
      const status = await getSetupStatus()
      if (!status.completed) {
        router.push('/onboarding')
        return
      }
    } catch {
      // 忽略：setup API 失败不阻塞主流程
    }
    router.push('/dashboard')
  } catch {
    message.error('登录失败，请检查用户名或密码')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-container">
    <a-card class="login-card" title="QuantPilot 量化领航">
      <a-form layout="vertical" :model="form" @finish="onSubmit">
        <a-form-item label="用户名" name="username">
          <a-input
            v-model:value="form.username"
            placeholder="请输入用户名"
            @press-enter="onSubmit"
          />
        </a-form-item>
        <a-form-item label="密码" name="password">
          <a-input-password
            v-model:value="form.password"
            placeholder="请输入密码"
            @press-enter="onSubmit"
          />
        </a-form-item>
        <a-form-item>
          <a-button type="primary" block :loading="loading" @click="onSubmit">
            登录
          </a-button>
        </a-form-item>
      </a-form>
      <!-- V1.5-G G-5：注册入口 -->
      <div class="login-register-link">
        还没有账号？<router-link to="/register">注册</router-link>
      </div>
      <!-- V1.0 整改 Batch 2 — B2-4：投顾边界合规声明 -->
      <div class="login-footer">
        本系统为<strong>个人量化交易决策辅助工具</strong>，
        <strong>不提供投资建议、不接受委托、不构成投顾服务</strong>。
        所有市场状态判断、信号、回测与绩效结果仅作为决策辅助参考，
        投资决策与盈亏由用户自行承担。
      </div>
    </a-card>
  </div>
</template>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: #f0f2f5;
}
.login-card {
  width: 360px;
}
.login-register-link {
  margin-top: 4px;
  text-align: center;
  font-size: 13px;
  color: rgba(0, 0, 0, 0.55);
}
.login-footer {
  margin-top: 12px;
  padding: 12px;
  border-top: 1px solid #f0f0f0;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
  line-height: 1.6;
  text-align: justify;
}
</style>
