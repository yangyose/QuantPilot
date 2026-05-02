<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import {
  DashboardOutlined,
  BellOutlined,
  BankOutlined,
  BarChartOutlined,
  FileTextOutlined,
  ExperimentOutlined,
  SettingOutlined,
  LogoutOutlined,
} from '@ant-design/icons-vue'
import NotificationBell from '@/components/NotificationBell.vue'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const collapsed = ref(false)

const selectedKeys = computed(() => {
  const path = route.path.replace('/', '') || 'dashboard'
  return [path]
})

const menuItems = [
  { key: 'dashboard', label: '总览', icon: DashboardOutlined },
  { key: 'signals', label: '信号', icon: BellOutlined },
  { key: 'positions', label: '持仓', icon: BankOutlined },
  { key: 'factors', label: '因子监控', icon: BarChartOutlined },
  { key: 'reports', label: '报告', icon: FileTextOutlined },
  { key: 'backtest', label: '回测', icon: ExperimentOutlined },
  { key: 'settings', label: '设置', icon: SettingOutlined },
]

function navigate(key: string) {
  router.push(`/${key}`)
}

function onMenuClick(info: { key: string }) {
  navigate(info.key)
}

function logout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider v-model:collapsed="collapsed" collapsible>
      <div class="logo">
        <span v-if="!collapsed" style="font-size: 18px; font-weight: 700; color: #fff">
          QuantPilot
        </span>
        <span v-else style="font-size: 14px; font-weight: 700; color: #fff">QP</span>
      </div>
      <a-menu
        theme="dark"
        mode="inline"
        :selected-keys="selectedKeys"
        @click="onMenuClick"
      >
        <a-menu-item v-for="item in menuItems" :key="item.key">
          <component :is="item.icon" />
          <span>{{ item.label }}</span>
        </a-menu-item>
      </a-menu>
    </a-layout-sider>

    <a-layout>
      <a-layout-header style="background: #fff; padding: 0 16px; display: flex; justify-content: flex-end; align-items: center; gap: 12px">
        <NotificationBell />
        <span v-if="auth.username" style="color: rgba(0,0,0,.65)">{{ auth.username }}</span>
        <a-button type="link" @click="logout">
          <template #icon><LogoutOutlined /></template>
          退出
        </a-button>
      </a-layout-header>

      <a-layout-content style="margin: 16px; overflow: initial">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.logo {
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.1);
  margin-bottom: 4px;
}
</style>
