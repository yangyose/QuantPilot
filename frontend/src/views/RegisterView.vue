<script setup lang="ts">
/**
 * V1.5-G G-5：用户注册页。
 * 设计 §4.4：注册成功不签发 token（为邮箱验证留位）→ 跳登录页手动登录。
 */
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { register } from '@/api/auth'

const router = useRouter()

const form = reactive({ username: '', email: '', password: '', confirm: '' })
const loading = ref(false)

function validate(): string | null {
  if (!form.username || !form.email || !form.password) return '请填写完整信息'
  if (form.username.length < 3 || form.username.length > 32) return '用户名长度须为 3-32 位'
  if (form.password.length < 8) return '密码至少 8 位'
  if (/^\d+$/.test(form.password)) return '密码不能是纯数字'
  if (form.password !== form.confirm) return '两次输入的密码不一致'
  return null
}

async function onSubmit() {
  const err = validate()
  if (err) {
    message.warning(err)
    return
  }
  loading.value = true
  try {
    await register(form.username, form.email, form.password)
    message.success('注册成功，请登录')
    router.push('/login')
  } catch (e: unknown) {
    const resp = (e as { response?: { status?: number; data?: { msg?: string } } }).response
    if (resp?.status === 409) {
      message.error(resp.data?.msg ?? '用户名或邮箱已被注册')
    } else if (resp?.status === 429) {
      message.error('注册请求过于频繁，请稍后再试')
    } else {
      message.error(resp?.data?.msg ?? '注册失败，请检查输入后重试')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="register-container">
    <a-card class="register-card" title="注册 QuantPilot 账号">
      <a-form layout="vertical" :model="form" @finish="onSubmit">
        <a-form-item label="用户名" name="username">
          <a-input
            v-model:value="form.username"
            placeholder="3-32 位"
            @press-enter="onSubmit"
          />
        </a-form-item>
        <a-form-item label="邮箱" name="email">
          <a-input
            v-model:value="form.email"
            placeholder="you@example.com"
            @press-enter="onSubmit"
          />
        </a-form-item>
        <a-form-item label="密码" name="password">
          <a-input-password
            v-model:value="form.password"
            placeholder="至少 8 位，不能是纯数字"
            @press-enter="onSubmit"
          />
        </a-form-item>
        <a-form-item label="确认密码" name="confirm">
          <a-input-password
            v-model:value="form.confirm"
            placeholder="再次输入密码"
            @press-enter="onSubmit"
          />
        </a-form-item>
        <a-form-item>
          <a-button type="primary" block :loading="loading" @click="onSubmit">
            注册
          </a-button>
        </a-form-item>
      </a-form>
      <div class="register-footer">
        已有账号？<router-link to="/login">去登录</router-link>
      </div>
    </a-card>
  </div>
</template>

<style scoped>
.register-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: #f0f2f5;
}
.register-card {
  width: 360px;
}
.register-footer {
  margin-top: 12px;
  text-align: center;
  font-size: 13px;
  color: rgba(0, 0, 0, 0.55);
}
</style>
