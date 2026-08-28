<script setup lang="ts">
/* 单条消息：kind = user | assistant | error | system-note（main.js addMsg 原逻辑）
   二期 P2：assistant 消息附加 refuse_reason 标签 + 证据链（回答依据来源，溯源率 100% 验收可见） */
import { computed } from 'vue'
import { renderMarkdown } from '../lib/markdown'

interface Evidence {
  source: string
}

const props = defineProps<{ kind: string; text: string; refuseReason?: string; evidence?: Evidence[] }>()

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

    <!-- 二期 P2：拒识原因标签 + 证据链（仅智能体回答） -->
    <template v-if="kind === 'assistant'">
      <span class="refuse-tag" v-if="refuseReason">{{ refuseReason }}，未下结论</span>
      <div class="evidence" v-if="evidence && evidence.length">
        <div class="ev-ttl">证据链（回答依据来源）</div>
        <div class="ev-item" v-for="(e, i) in evidence" :key="i">{{ e.source }}</div>
      </div>
    </template>
  </div>
</template>
