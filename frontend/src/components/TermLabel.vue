<script setup lang="ts">
/**
 * Phase 10 §6.8：术语 + tooltip 解释封装。
 *
 * 用法：
 *   <TermLabel term="sharpe" />                          → 显示 "夏普比率" + 鼠标悬停看解释
 *   <TermLabel term="sharpe" label="年化夏普" />          → 自定义显示文案，仍用 sharpe 解释
 *   <TermLabel term="sharpe" :show-icon="false" />        → 隐藏右上角问号图标
 *
 * 未登记 term：降级为纯文字（无 tooltip / 无图标），不影响布局。
 */
import { computed } from 'vue'
import { QuestionCircleOutlined } from '@ant-design/icons-vue'

import { getTerm } from '@/utils/glossary'

const props = withDefaults(
  defineProps<{
    term: string
    label?: string
    showIcon?: boolean
  }>(),
  { showIcon: true },
)

const def = computed(() => getTerm(props.term))
const text = computed(() => props.label ?? def.value?.title ?? props.term)
</script>

<template>
  <span class="term-label">
    <a-tooltip v-if="def" :title="def.description" placement="top">
      <span class="term-label__text" :class="{ 'has-tooltip': true }">
        {{ text }}
        <QuestionCircleOutlined v-if="showIcon" class="term-label__icon" />
      </span>
    </a-tooltip>
    <span v-else class="term-label__text">{{ text }}</span>
  </span>
</template>

<style scoped>
.term-label {
  display: inline-flex;
  align-items: center;
}
.term-label__text.has-tooltip {
  border-bottom: 1px dashed rgba(0, 0, 0, 0.25);
  cursor: help;
}
.term-label__icon {
  margin-left: 4px;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
}
</style>
