<script setup lang="ts">
/* 单条消息：kind = user | assistant | error | system-note（main.js addMsg 原逻辑） */
import { computed } from 'vue'
import { renderMarkdown } from '../lib/markdown'

const props = defineProps<{ kind: string; text: string }>()

const who = computed(
  () =>
    props.kind === 'user' ? '你' : props.kind === 'error' ? '系统' : props.kind === 'system-note' ? '·' : '智能体'
)
// 仅智能体回答渲染 markdown（本地渲染器离线可用）；其余一律纯文本防 XSS
const html = computed(() => (props.kind === 'assistant' ? renderMarkdown(props.text) : ''))
</script>

<template>
  <div class="msg" :class="kind">
    <div class="who">{{ who }}</div>
    <div class="bubble" v-if="kind === 'assistant'" v-html="html"></div>
    <div class="bubble" v-else>{{ text }}</div>
  </div>
</template>
