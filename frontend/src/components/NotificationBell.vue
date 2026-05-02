<script setup lang="ts">
import { BellOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { onMounted, onUnmounted, ref } from 'vue'

import {
  getUnreadCount,
  listNotifications,
  markAllRead,
  markNotificationRead,
} from '@/api/notifications'
import type { NotificationItem } from '@/types/api'

const unread = ref(0)
const notifications = ref<NotificationItem[]>([])
const loading = ref(false)
const dropdownVisible = ref(false)

const POLL_INTERVAL_MS = 30_000
let timer: ReturnType<typeof setInterval> | null = null

async function refreshCount() {
  try {
    unread.value = await getUnreadCount()
  } catch {
    // 静默失败：Bell 不应打扰用户，下次轮询会自愈
  }
}

async function loadRecent() {
  loading.value = true
  try {
    const data = await listNotifications({ limit: 10 })
    notifications.value = data.items
    unread.value = data.unread
  } catch {
    message.error('通知加载失败')
  } finally {
    loading.value = false
  }
}

async function onDropdownOpen(visible: boolean) {
  dropdownVisible.value = visible
  if (visible) await loadRecent()
}

async function onClickItem(n: NotificationItem) {
  if (n.read_at) return
  try {
    await markNotificationRead(n.id)
    n.read_at = new Date().toISOString()
    unread.value = Math.max(0, unread.value - 1)
  } catch {
    message.error('标记已读失败')
  }
}

async function onMarkAll() {
  try {
    await markAllRead()
    message.success('已全部标记为已读')
    await loadRecent()
  } catch {
    message.error('操作失败')
  }
}

function fmtTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 19)
}

function typeColor(t: string): string {
  const map: Record<string, string> = {
    SIGNAL_BUY: 'red',
    SIGNAL_SELL: 'green',
    MARKET_STATE: 'blue',
    STOP_LOSS_WARN: 'orange',
    RISK_WARN: 'red',
    FACTOR_ALERT: 'purple',
    PIPELINE_FAILURE: 'red',
  }
  return map[t] ?? 'default'
}

onMounted(() => {
  refreshCount()
  timer = setInterval(refreshCount, POLL_INTERVAL_MS)
})

onUnmounted(() => {
  if (timer !== null) clearInterval(timer)
})
</script>

<template>
  <a-dropdown
    :open="dropdownVisible"
    trigger="click"
    placement="bottomRight"
    @open-change="onDropdownOpen"
  >
    <a-badge :count="unread" :offset="[-4, 4]">
      <a-button type="text" style="padding: 0 8px">
        <template #icon><BellOutlined /></template>
      </a-button>
    </a-badge>

    <template #overlay>
      <div class="notify-dropdown">
        <div class="notify-header">
          <span class="notify-title">通知中心</span>
          <a-button
            type="link"
            size="small"
            :disabled="unread === 0"
            @click="onMarkAll"
          >
            全部已读
          </a-button>
        </div>

        <a-spin :spinning="loading">
          <div v-if="notifications.length === 0" class="notify-empty">
            暂无通知
          </div>
          <div v-else class="notify-list">
            <div
              v-for="n in notifications"
              :key="n.id"
              class="notify-item"
              :class="{ unread: !n.read_at }"
              @click="onClickItem(n)"
            >
              <div class="notify-item-head">
                <a-tag :color="typeColor(n.notify_type)" style="margin-right: 6px">
                  {{ n.notify_type }}
                </a-tag>
                <span class="notify-item-title">{{ n.title }}</span>
              </div>
              <div class="notify-item-body">{{ n.body }}</div>
              <div class="notify-item-meta">
                <span>{{ fmtTime(n.created_at) }}</span>
                <span v-if="n.wx_pushed" class="notify-wx">✓ 微信已推送</span>
              </div>
            </div>
          </div>
        </a-spin>
      </div>
    </template>
  </a-dropdown>
</template>

<style scoped>
.notify-dropdown {
  width: 360px;
  max-height: 480px;
  overflow-y: auto;
  background: #fff;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}
.notify-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid #f0f0f0;
}
.notify-title {
  font-weight: 600;
  font-size: 14px;
}
.notify-empty {
  padding: 24px;
  text-align: center;
  color: #bfbfbf;
  font-size: 13px;
}
.notify-list {
  display: flex;
  flex-direction: column;
}
.notify-item {
  padding: 10px 12px;
  border-bottom: 1px solid #fafafa;
  cursor: pointer;
  font-size: 12px;
  line-height: 1.5;
  transition: background 0.2s;
}
.notify-item:hover {
  background: #f9f9f9;
}
.notify-item.unread {
  background: #fffbe6;
}
.notify-item-head {
  display: flex;
  align-items: center;
  margin-bottom: 2px;
}
.notify-item-title {
  font-weight: 500;
  color: rgba(0, 0, 0, 0.85);
}
.notify-item-body {
  color: rgba(0, 0, 0, 0.65);
  word-break: break-word;
}
.notify-item-meta {
  color: #bfbfbf;
  font-size: 11px;
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
}
.notify-wx {
  color: #52c41a;
}
</style>
