<script setup lang="ts">
/* 识别成功卡（main.js showRecCard 原逻辑）：top1 + 置信度 + Top-3 + 档案 */
export interface RecCardData {
  top1: string
  confidence: number
  top3: { name: string; confidence: number }[]
  profile: string
}
defineProps<{ data: RecCardData }>()
</script>

<template>
  <div class="rec-card">
    <div class="top1">{{ data.top1 }}</div>
    <div class="conf">置信度 {{ (data.confidence * 100).toFixed(1) }}%</div>
    <div class="top3">
      Top-3：{{ data.top3.map((c) => c.name + ' ' + (c.confidence * 100).toFixed(0) + '%').join(' ｜ ') }}
    </div>
    <div class="profile">{{ data.profile }}</div>
  </div>
</template>
