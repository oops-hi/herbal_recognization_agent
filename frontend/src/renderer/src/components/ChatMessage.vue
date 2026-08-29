<script setup lang="ts">
/* 单条消息：kind = user | assistant | error | system-note（main.js addMsg 原逻辑）
   二期 P2：assistant 消息附加 refuse_reason 标签 + 证据链（回答依据来源，溯源率 100% 验收可见）
   2026-08-29：user 消息可带图片（/uploads server URL 或 blob: 临时 URL） */
import { computed, ref } from 'vue'
import { renderMarkdown } from '../lib/markdown'

interface Evidence {
  source: string
}

const props = defineProps<{ kind: string; text: string; image?: string; refuseReason?: string; evidence?: Evidence[] }>()

const who = computed(
  () =>
    props.kind === 'user' ? '你' : props.kind === 'error' ? '系统' : props.kind === 'system-note' ? '·' : '智能体'
)
// 仅智能体回答渲染 markdown（本地渲染器离线可用）；其余一律纯文本防 XSS
const html = computed(() => (props.kind === 'assistant' ? renderMarkdown(props.text) : ''))
// XSS 防护：图片 src 只放行合法来源——http(s)/blob:（本地预览）/server 相对路由 /uploads/
// ⚠️ 上传成功后消息里的 image 是相对路径 /uploads/...，漏掉它图片会消失
const safeImage = computed(() =>
  props.image &&
  (props.image.startsWith('http') || props.image.startsWith('blob:') || props.image.startsWith('/uploads/'))
    ? props.image
    : ''
)
const imgBroken = ref(false) // 服务端图被清理（保留 5 张策略/24h 清理）→ 占位文本
</script>

<template>
  <div class="msg" :class="kind">
    <div class="who">{{ who }}</div>
    <!-- 用户上传的图片消息（只有图没字时不渲染空气泡） -->
    <template v-if="kind === 'user' && safeImage">
      <img v-if="!imgBroken" class="chat-img" :src="safeImage" alt="用户上传图片"
           @error="imgBroken = true">
      <span v-else class="chat-img-fallback">图片已清理</span>
    </template>
    <div class="bubble" v-if="kind === 'assistant'" v-html="html"></div>
    <div class="bubble" v-else-if="text || kind !== 'user'">{{ text }}</div>

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
