<script setup lang="ts">
/* 智能体对话区：SSE 流式工具链实时展示（main.js 对话逻辑 + createTimeline 原逻辑） */
import { nextTick, ref } from 'vue'
import ChatMessage from './ChatMessage.vue'
import TimelineItem, { type ToolStep } from './TimelineItem.vue'
import { useClientId } from '../composables/useClientId'
import { postAndStream } from '../composables/useSseStream'

interface ChatMsg {
  kind: string // user | assistant | error | system-note
  text: string
}
interface StreamEvent {
  type: string
  name?: string
  arguments?: Record<string, unknown>
  result?: string
  text?: string
}

const clientId = useClientId()
const chatBody = ref<HTMLElement>()
const chatInput = ref<HTMLTextAreaElement>()
const sendBtn = ref<HTMLButtonElement>()

const messages = ref<ChatMsg[]>([])
const timelines = ref<{ steps: ToolStep[] }[]>([])
const streaming = ref(false)

function scrollBottom(): void {
  nextTick(() => {
    if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
  })
}

function addMsg(kind: string, text: string): void {
  messages.value.push({ kind, text })
  scrollBottom()
}

function handleEvent(ev: StreamEvent, tl: { steps: ToolStep[] }): void {
  switch (ev.type) {
    case 'tool':
      tl.steps.push({
        name: ev.name || '',
        arguments: ev.arguments || {},
        result: ev.result || ''
      })
      scrollBottom()
      break
    case 'answer':
      addMsg('assistant', ev.text || '（无内容）')
      break
    case 'error':
      addMsg('error', ev.text || '服务异常')
      break
  }
}

async function send(): Promise<void> {
  const q = (chatInput.value?.value || '').trim()
  if (!q || streaming.value) return
  if (chatInput.value) chatInput.value.value = ''
  addMsg('user', q)
  const tl = { steps: [] }
  timelines.value.push(tl)
  streaming.value = true
  if (sendBtn.value) sendBtn.value.disabled = true

  try {
    await postAndStream(
      '/chat',
      { question: q, client_id: clientId },
      (ev) => handleEvent(ev as StreamEvent, tl)
    )
  } catch (e) {
    addMsg('error', '网络异常：' + (e as Error).message)
  } finally {
    streaming.value = false
    if (sendBtn.value) sendBtn.value.disabled = false
    chatInput.value?.focus()
  }
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void send()
  }
}

// 识别成功后注入 system-note 消息（原 main.js addMsg("system-note", ...)）
function pushSystemNote(text: string): void {
  addMsg('system-note', text)
}

defineExpose({ pushSystemNote })
</script>

<template>
  <h2>智能体对话 <span class="chat-sub">（工具调用链实时展示）</span></h2>
  <div class="chat-body" ref="chatBody">
    <ChatMessage v-for="(m, i) in messages" :key="'m' + i" :kind="m.kind" :text="m.text" />
    <TimelineItem v-for="(tl, i) in timelines" :key="'t' + i" :steps="tl.steps" />
  </div>

  <div class="chat-input">
    <textarea ref="chatInput" rows="2"
              placeholder="例如：这个能和菊花一起泡水吗？我最近眼睛干&#10;（Enter 发送，Shift+Enter 换行）"
              @keydown="onKeydown"></textarea>
    <button ref="sendBtn" @click="send">发送</button>
  </div>
</template>

<style scoped>
.chat-sub {
  font-weight: 400;
  color: var(--gray);
  font-size: 12px;
}
</style>
