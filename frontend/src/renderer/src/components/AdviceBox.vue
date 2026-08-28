<script setup lang="ts">
/* 低置信度拒答建议框（main.js showAdvice 原逻辑） */
export interface AdviceData {
  advice: string[]
  top3: { name: string; confidence: number }[]
}
defineProps<{ data: AdviceData }>()
</script>

<template>
  <div class="advice-box">
    <div class="t">无法确认药材，拒绝下结论</div>
    <div v-for="(a, i) in data.advice" :key="i">· {{ a }}</div>
    <div v-if="data.top3 && data.top3.length">
      Top-3 参考：{{ data.top3.map((c) => c.name).join(' / ') }}
    </div>
  </div>
</template>
