<script setup lang="ts">
/* 识别成功卡（main.js showRecCard 原逻辑）：top1 + 置信度 + Top-3 + 档案 + 讲解入口 */
export interface RecCardData {
  top1: string
  confidence: number
  top3: { name: string; confidence: number }[]
  profile: string
}
defineProps<{ data: RecCardData }>()
defineEmits<{ teach: [herb: string] }>()
</script>

<template>
  <div class="rec-card">
    <div class="top1">{{ data.top1 }}</div>
    <div class="conf">置信度 {{ (data.confidence * 100).toFixed(1) }}%</div>
    <button class="teach-btn" title="让讲解子 Agent 系统讲解这味药（药典/教材/本草记载）"
            @click="$emit('teach', data.top1)">📖 讲解这味药</button>
    <div class="top3">
      Top-3：{{ data.top3.map((c) => c.name + ' ' + (c.confidence * 100).toFixed(0) + '%').join(' ｜ ') }}
    </div>
    <div class="profile">{{ data.profile }}</div>
  </div>
</template>

<style scoped>
.teach-btn {
  margin: 6px 0;
  padding: 4px 14px;
  border-radius: 999px;
  border: 1px solid var(--green);
  background: var(--green-soft, transparent);
  color: var(--green-dark);
  font-size: 12px;
  cursor: pointer;
}
.teach-btn:hover {
  background: var(--green);
  color: #fff;
}
</style>
