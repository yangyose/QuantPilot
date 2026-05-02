<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ text: string }>()
const expanded = ref(false)
</script>

<template>
  <a-alert
    type="warning"
    show-icon
    :message="expanded ? text : text.slice(0, 80) + (text.length > 80 ? '…' : '')"
    style="margin-bottom: 16px"
  >
    <template #action>
      <a-button size="small" type="link" @click="expanded = !expanded">
        {{ expanded ? '收起' : '展开全文' }}
      </a-button>
    </template>
  </a-alert>
</template>
