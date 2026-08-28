<script setup lang="ts">
/* 智能体对话区：SSE 流式工具链实时展示（main.js 对话逻辑 + createTimeline 原逻辑） */
import { nextTick, reactive, ref } from 'vue'
import ChatMessage from './ChatMessage.vue'
import TimelineItem, { type ToolStep } from './TimelineItem.vue'
import { useClientId } from '../composables/useClientId'
import { postAndStream } from '../composables/useSseStream'

interface Evidence {
  source: string
}
interface ChatMsg {
  kind: string // user | assistant | error | system-note
  text: string
  refuseReason?: string // 二期 P2：refuse_reason 四类结构化（知识缺口/置信不足/域外图/合规边界）
  evidence?: Evidence[] // 二期 P2：证据链（回答依据来源，带版本）
}
interface StreamEvent {
  type: string
  name?: string
  arguments?: Record<string, unknown>
  result?: string
  text?: string
  agent?: string
  refuse_reason?: string
  evidence?: Evidence[]
}

const clientId = useClientId()
const chatBody = ref<HTMLElement>()
const chatInput = ref<HTMLTextAreaElement>()
const sendBtn = ref<HTMLButtonElement>()

// 按时间顺序的对话单元流：msg（用户/回答/提示）与 timeline（工具链）交错排列
interface ChatUnit {
  kind: 'msg' | 'timeline'
  msg?: ChatMsg
  steps?: ToolStep[]
}
const units = ref<ChatUnit[]>([])
const streaming = ref(false)

function scrollBottom(): void {
  nextTick(() => {
    if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
  })
}

function addMsg(kind: string, text: string, extra?: Partial<ChatMsg>): void {
  units.value.push({ kind: 'msg', msg: { kind, text, ...extra } })
  scrollBottom()
}

function addTimeline(): ChatUnit {
  // ⚠️ 必须 reactive 创建：普通对象 push 进 ref 数组后被转成代理，
  //    原引用 steps.push 会绕过代理不触发更新（工具链不逐条弹出）
  const unit: ChatUnit = { kind: 'timeline', steps: reactive([] as ToolStep[]) }
  units.value.push(unit)
  return unit
}

function handleEvent(ev: StreamEvent, tl: ChatUnit): void {
  switch (ev.type) {
    case 'tool':
      ;(tl.steps as ToolStep[]).push({
        name: ev.name || '',
        agent: ev.agent,
        arguments: ev.arguments || {},
        result: ev.result || ''
      })
      scrollBottom()
      break
    case 'answer':
      addMsg('assistant', ev.text || '（无内容）', {
        refuseReason: ev.refuse_reason,
        evidence: ev.evidence
      })
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
  const tl = addTimeline()
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
    <template v-for="(u, i) in units" :key="'u' + i">
      <ChatMessage v-if="u.kind === 'msg'" :kind="u.msg!.kind" :text="u.msg!.text"
                   :refuse-reason="u.msg!.refuseReason" :evidence="u.msg!.evidence" />
      <TimelineItem v-else :steps="u.steps || []" />
    </template>
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
