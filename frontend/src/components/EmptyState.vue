<script setup lang="ts">
defineProps<{
  title?: string
  description?: string
}>()
</script>

<template>
  <div class="empty-state">
    <a-empty>
      <template #description>
        <span class="empty-title">{{ title ?? '暂无数据' }}</span>
        <br v-if="description" />
        <span v-if="description" class="empty-desc">{{ description }}</span>
      </template>
    </a-empty>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  justify-content: center;
  padding: 48px 0;
}
.empty-title {
  font-size: 16px;
  color: rgba(0, 0, 0, 0.45);
}
.empty-desc {
  font-size: 13px;
  color: rgba(0, 0, 0, 0.3);
}
</style>
