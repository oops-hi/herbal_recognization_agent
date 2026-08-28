<script setup lang="ts">
/* 工具调用链时间轴：每个问题一个折叠块，实时逐条出现（main.js createTimeline/addToolStep 原逻辑） */
import { ref } from 'vue'

export interface ToolStep {
  name: string
  arguments: Record<string, unknown>
  result: string
}

defineProps<{ steps: ToolStep[] }>()
const collapsed = ref(false)

const isBad = (result: string): boolean => String(result).includes('异常') || String(result).includes('失败')
const shortArg = (args: Record<string, unknown>): string => {
  const t = JSON.stringify(args)
  return t.length > 60 ? t.slice(0, 60) + '…' : t
}
</script>

<template>
  <div class="timeline" :data-collapsed="collapsed ? '1' : '0'">
    <div class="ttl" @click="collapsed = !collapsed">
      {{ collapsed ? '▸ 工具调用链（已折叠）' : '▾ 工具调用链' }}
    </div>
    <div class="tl-step" v-for="(s, i) in steps" :key="i" :class="{ bad: isBad(s.result) }">
      <span class="name">{{ s.name }}</span>{{ shortArg(s.arguments) }}
      <div class="result">{{ s.result }}</div>
    </div>
  </div>
</template>
