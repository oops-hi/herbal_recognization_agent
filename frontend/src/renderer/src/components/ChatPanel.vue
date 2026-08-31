<script setup lang="ts">
/* 智能体对话区：SSE 流式工具链实时展示 + 图片消息（📎内联上传，点发送才识别）
   持久化（2026-08-29）：localStorage 存 units/stats，重启恢复；timeline 必须 reactive 包装（代理坑） */
import { computed, nextTick, onActivated, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import ChatMessage from './ChatMessage.vue'
import TimelineItem, { type ToolStep } from './TimelineItem.vue'
import ContextStatsBar, { type SessionStats } from './ContextStatsBar.vue'
import {
  initSessions, unitsKey, statsKey, setCurrent, addSession, removeSession, touchSession,
  mergeServerSessions, type SessionMeta
} from '../composables/useSessions'
import { postAndStream } from '../composables/useSseStream'

interface Evidence {
  source: string
}
interface ChatMsg {
  kind: string // user | assistant | error | system-note
  text: string
  image?: string // 图片消息：blob:（上传完成前临时）/ /uploads/...（持久，跨重启显示）
  refuseReason?: string // 二期 P2：refuse_reason 四类结构化（知识缺口/置信不足/域外图/合规边界）
  evidence?: Evidence[] // 二期 P2：证据链（回答依据来源，带版本）
  animate?: boolean // 打字机效果（仅新收到的 answer 置 true，恢复历史一律 false 不重播）
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
  turns?: number
  steps?: number
  llm_time?: number
  tool_time?: number
  tokens_in?: number
  tokens_out?: number
  cache_hit?: number
  cache_miss?: number
}

// 识别结果回传 HomeView 左卡（「识别结果」面板）的载荷：字段存在性判断，undefined=不改
export interface RecResultPayload {
  recData?: { top1: string; confidence: number; top3: { name: string; confidence: number }[]; profile: string } | null
  adviceData?: { advice: string[]; top3: { name: string; confidence: number }[] } | null
  visionBadge?: { state: string; vlm_top1?: string; verdict?: string; reason?: string; category?: string } | null
  uploadError?: string | null
}

// /upload 响应（ok 与三个 low_confidence / model_not_ready 分支，含新增 image_url）
interface UploadResp {
  status: string
  top1?: string
  confidence?: number
  top3?: { name: string; confidence: number }[]
  profile?: string
  advice?: string[]
  message?: string
  error?: string
  vision?: RecResultPayload['visionBadge']
  image_url?: string
  sim_pair?: { candidates: string[]; tip: string } | null  // 相似对鉴别：Top-3 命中易混对
}

const emit = defineEmits<{ 'rec-result': [payload: RecResultPayload] }>()

// 一键讲解入口（RecCard「📖 讲解」）：注入『讲讲X』+ 强制讲解子 Agent（force_agent，app.py 白名单校验）
let pendingAgent: string | null = null

// ---- 多会话（2026-08-29）：会话 = 服务端 client_id；本地索引 + 当前 id 由 useSessions 管理 ----
const sessions = ref<SessionMeta[]>([])
const currentId = ref('')

const chatBody = ref<HTMLElement>()
const chatInput = ref<HTMLTextAreaElement>()

// keep-alive 滚动位置补偿：切换路由时 Chromium 把移出文档容器的 scrollTop 清零 → 切回停顶部
// deactivate 记录位置，activate 恢复；离开时近底部（≤60px）则吸底，翻历史则原位恢复
let savedScrollTop = 0
let leaveNearBottom = true
const sendBtn = ref<HTMLButtonElement>()
const fileInput = ref<HTMLInputElement>()

// 按时间顺序的对话单元流：msg（用户/回答/提示）与 timeline（工具链）交错排列
interface ChatUnit {
  kind: 'msg' | 'timeline'
  msg?: ChatMsg
  steps?: ToolStep[]
}
const units = ref<ChatUnit[]>([])
const streaming = ref(false)
const uploading = ref(false)
// 停止按钮：AbortController 中止 SSE fetch（DeepSeek 挂起/60s 超时窗口内用户可自救，2026-08-29）
const abortCtrl = ref<AbortController | null>(null)
// 待发送图片（「选图 → 预览 → 点发送才上传识别」）
const pendingImage = ref<{ file: File; url: string } | null>(null)
const dragOver = ref(false)
// 会话上下文统计（服务端累计，stats 事件逐轮/逐步推送）
const stats = ref<SessionStats | null>(null)

// ---- localStorage 持久化（重启/刷新恢复对话记录；多会话版按会话分键 herb_agent_units_<id>） ----
let saveTimer: ReturnType<typeof setTimeout> | undefined

function scrollBottom(): void {
  nextTick(() => {
    if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
  })
}

onDeactivated(() => {
  const el = chatBody.value
  if (!el) return
  savedScrollTop = el.scrollTop
  leaveNearBottom = el.scrollTop >= el.scrollHeight - el.clientHeight - 60
})

onActivated(() => {
  nextTick(() => {
    const el = chatBody.value
    if (!el) return
    if (leaveNearBottom) {
      el.scrollTop = el.scrollHeight
    } else {
      el.scrollTop = savedScrollTop
    }
  })
})

function addMsg(kind: string, text: string, extra?: Partial<ChatMsg>): ChatMsg {
  const msg: ChatMsg = { kind, text, ...extra }
  units.value.push({ kind: 'msg', msg })
  scrollBottom()
  return msg
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
        evidence: ev.evidence,
        animate: true // 打字机逐字输出
      })
      break
    case 'error':
      addMsg('error', ev.text || '服务异常')
      break
    case 'stats':
      stats.value = {
        turns: ev.turns || 0,
        steps: ev.steps || 0,
        llm_time: ev.llm_time || 0,
        tool_time: ev.tool_time || 0,
        tokens_in: ev.tokens_in || 0,
        tokens_out: ev.tokens_out || 0,
        cache_hit: ev.cache_hit || 0,
        cache_miss: ev.cache_miss || 0
      }
      break
  }
}

// ---- 图片接入（📎 按钮 / 拖拽，复用原 UploadZone 校验） ----

function validateImageFile(file: File): string | null {
  if (!/\.(jpe?g|png|webp|bmp)$/i.test(file.name)) {
    return '不支持的文件格式，仅支持 jpg / png / webp / bmp'
  }
  if (file.size > 16 * 1024 * 1024) {
    return '图片过大，请上传 16MB 以内的图片'
  }
  return null
}

function pickFile(file?: File | null): void {
  if (!file) return
  const err = validateImageFile(file)
  if (err) {
    addMsg('error', err)
    return
  }
  if (pendingImage.value) URL.revokeObjectURL(pendingImage.value.url)
  pendingImage.value = { file, url: URL.createObjectURL(file) }
  // 选中新图即清掉旧识别卡/建议（对齐旧 UX：上传新图前 onReset）
  emit('rec-result', { recData: null, adviceData: null, visionBadge: null, uploadError: null })
  focusInput()
}

function onDrop(e: DragEvent): void {
  e.preventDefault()
  dragOver.value = false
  pickFile(e.dataTransfer?.files?.[0])
}

// Ctrl+V / 粘贴板图片（截图粘贴等）：剪贴板带图片文件时优先当附件
function onPaste(e: ClipboardEvent): void {
  const files = e.clipboardData?.files
  if (!files || files.length === 0) return
  if (uploading.value) return
  e.preventDefault() // 不把图片当文本粘进 textarea
  pickFile(files[0])
}

function clearPending(): void {
  if (pendingImage.value) URL.revokeObjectURL(pendingImage.value.url)
  pendingImage.value = null
}

// 半场载荷构建（原 HomeView.onUpload 填充逻辑平移）：/upload 响应 → 左卡数据
function buildRecResult(j: UploadResp): RecResultPayload {
  if (j.status === 'ok') {
    return {
      recData: { top1: j.top1!, confidence: j.confidence!, top3: j.top3!, profile: j.profile || '' },
      adviceData: null,
      visionBadge: j.vision || null,
      uploadError: null
    }
  }
  if (j.status === 'low_confidence') {
    return {
      recData: null,
      adviceData: { advice: j.advice || [], top3: j.top3 || [] },
      visionBadge: j.vision || null,
      uploadError: null
    }
  }
  return { recData: null, adviceData: null, visionBadge: null, uploadError: null }
}

// ---- 多会话：切换 / 新建 / 删除（历史会话管理，2026-08-29） ----

const sortedSessions = computed(() => [...sessions.value].sort((a, b) => b.updated - a.updated))

function fmtTime(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// 从 localStorage 恢复指定会话的显示层（units/stats）；timeline 必须 reactive([]) 包装（代理坑）
function restoreUnits(id: string): void {
  units.value = []
  stats.value = null
  try {
    const raw = localStorage.getItem(unitsKey(id))
    if (raw) {
      const arr = JSON.parse(raw)
      if (Array.isArray(arr)) {
        for (const u of arr.slice(-200)) {
          if (!u || typeof u !== 'object') continue
          if (u.kind === 'msg' && u.msg && typeof u.msg === 'object') {
            const m = u.msg as ChatMsg
            units.value.push({
              kind: 'msg',
              // blob: 临时 URL 跨重启无效，恢复时丢弃；server URL /uploads 正常保留
              msg: {
                ...m,
                image: m.image && !m.image.startsWith('blob:') ? m.image : undefined,
                animate: false // 历史消息不重播打字机
              }
            })
          } else if (u.kind === 'timeline' && Array.isArray(u.steps)) {
            const unit: ChatUnit = { kind: 'timeline', steps: reactive([] as ToolStep[]) }
            ;(unit.steps as ToolStep[]).push(...(u.steps as ToolStep[]).filter((s) => s && typeof s === 'object'))
            units.value.push(unit)
          }
        }
      }
    }
    const sRaw = localStorage.getItem(statsKey(id))
    if (sRaw) {
      const s = JSON.parse(sRaw)
      if (s && typeof s === 'object') stats.value = s as SessionStats
    }
  } catch {
    /* 损坏数据静默丢弃 */
  }
  scrollBottom()
}

function switchSession(id: string): void {
  if (!id || id === currentId.value) return
  if (streaming.value || uploading.value) return
  saveLocalTo(currentId.value) // 旧会话先落盘
  currentId.value = id
  setCurrent(id)
  clearPending()
  restoreUnits(id)
  // 左卡识别结果是上一会话的，切会话即清（各会话上下文独立）
  emit('rec-result', { recData: null, adviceData: null, visionBadge: null, uploadError: null })
  focusInput()
}

// 「新会话」：新 id（服务端首次使用时自动建空会话）→ 清空显示层；旧会话留在列表里可随时切回
function newSession(): void {
  if (streaming.value || uploading.value) return
  saveLocal()
  const meta = addSession(sessions.value)
  currentId.value = meta.id
  setCurrent(meta.id)
  units.value = []
  stats.value = null
  clearPending()
  emit('rec-result', { recData: null, adviceData: null, visionBadge: null, uploadError: null })
  addMsg('system-note', '已开启新会话')
  focusInput()
}

async function deleteSession(): Promise<void> {
  if (streaming.value || uploading.value || sessions.value.length <= 1) return
  const id = currentId.value
  const label = sessions.value.find((s) => s.id === id)?.title || '该会话'
  if (!window.confirm('删除会话「' + label + '」？服务端与本地记录将一并清除，不可恢复。')) return
  try {
    await fetch('/api/sessions/' + encodeURIComponent(id), { method: 'DELETE' })
  } catch {
    /* 断网静默：本地已删，服务端记录可稍后再清 */
  }
  removeSession(sessions.value, id)
  if (currentId.value === id) {
    const next = [...sessions.value].sort((a, b) => b.updated - a.updated)[0]
    if (next) {
      currentId.value = next.id
      setCurrent(next.id)
      clearPending()
      restoreUnits(next.id)
    }
  }
  emit('rec-result', { recData: null, adviceData: null, visionBadge: null, uploadError: null })
  focusInput()
}

async function send(): Promise<void> {
  const q = (chatInput.value?.value || '').trim()
  const img = pendingImage.value
  if ((!q && !img) || streaming.value || uploading.value) return

  // 立即上屏（图片先显示 blob 临时 URL，上传成功后替换为 server URL——持久化跨重启显示）
  const userMsg = addMsg('user', q, img ? { image: img.url } : undefined)
  if (chatInput.value) chatInput.value.value = ''

  if (img) {
    pendingImage.value = null
    uploading.value = true
    if (sendBtn.value) sendBtn.value.disabled = true
    try {
      const fd = new FormData()
      fd.append('image', img.file)
      fd.append('client_id', currentId.value)
      const resp = await fetch('/upload', { method: 'POST', body: fd })
      const j = (await resp.json()) as UploadResp
      if (!resp.ok) {
        addMsg('error', j.error || '上传失败')
        emit('rec-result', { uploadError: j.error || '上传失败' })
        return // 图片消息保留（blob URL 本地仍有效）
      }
      // blob（临时）→ server URL（持久）：原引用属性修改对代理链可见（见 addMsg 返回原始对象）
      if (j.image_url) {
        userMsg.image = j.image_url
        URL.revokeObjectURL(img.url)
      }
      emit('rec-result', buildRecResult(j))
      if (j.status === 'ok') {
        // 识别成功以智能体回复样式输出（用户要求：不用小字 system-note）
        addMsg('assistant', '已识别：' + j.top1 + '（置信度 ' + (j.confidence! * 100).toFixed(1) +
          '%）。你可以直接提问，比如『这个能和菊花一起泡水吗？』', { animate: true })
        if (j.sim_pair) {
          // 相似对鉴别闭环：Top-3 命中易混对 → 以智能体回复样式主动引导（用户要求：不用小字 system-note）
          addMsg('assistant',
            '识别候选中「' + j.sim_pair.candidates.join('、') +
            '」为外形易混淆药材。你可以问『这是什么？』，我会逐项与你确认可观察特征后再作判断。',
            { animate: true })
        }
        if (!q) addMsg('system-note', '识别完成，可在下方输入框直接提问（如『这个怎么用？』）')
      } else if (j.status === 'low_confidence') {
        const nonHerb = j.vision?.state === 'non_herb'
        addMsg('system-note',
          (nonHerb ? '图片判定为非中药饮片，已拒绝下结论' : '图片识别置信不足，已拒绝下结论') +
          '（详见左侧提示）—— 可直接追问，或补拍后重新上传。')
      } else if (j.status === 'model_not_ready') {
        addMsg('error', j.message || '模型未就绪')
        addMsg('system-note', '模型未就绪，图片已保存但暂未识别，可先体验图谱查询与对话。')
      }
    } catch (e) {
      addMsg('error', '网络异常：' + (e as Error).message)
      emit('rec-result', { uploadError: '网络异常：' + (e as Error).message })
    } finally {
      uploading.value = false
      if (sendBtn.value) sendBtn.value.disabled = false
    }
  }

  // 只要用户打了字就继续发 /chat（识别成功走 current_herb；拒识/未就绪走服务端 upload_ctx 口径）
  if (q) {
    const agentHint = pendingAgent // 一键讲解的 force_agent：本组请求用一次即清
    pendingAgent = null
    const tl = addTimeline()
    streaming.value = true
    if (sendBtn.value) sendBtn.value.disabled = true
    abortCtrl.value = new AbortController()
    try {
      await postAndStream(
        '/chat',
        { question: q, client_id: currentId.value, ...(agentHint ? { agent: agentHint } : {}) },
        (ev) => handleEvent(ev as StreamEvent, tl),
        abortCtrl.value.signal
      )
    } catch (e) {
      // 用户点「停止」主动中止：静默（不报错泡，服务端历史由 GeneratorExit + 两遍循环兜底）
      if ((e as Error).name === 'AbortError') {
        addMsg('system-note', '已停止本次回答生成')
      } else {
        addMsg('error', '网络异常：' + (e as Error).message)
      }
    } finally {
      streaming.value = false
      abortCtrl.value = null
      if (sendBtn.value) sendBtn.value.disabled = false
    }
  }
  chatInput.value?.focus()
}

// 「停止」：中止 SSE fetch → 前端 catch AbortError 静默停流（服务端 generate() 落盘收尾）
function stopStream(): void {
  abortCtrl.value?.abort()
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void send()
  }
}

// 上传后自动聚焦输入框：用户无需手动点击即可直接提问
function focusInput(): void {
  chatInput.value?.focus()
}

// 识别卡「📖 讲解」入口：填入『讲讲X』并强制讲解子 Agent（确定性优先于 Router 关键词）
function teachHerb(herb: string): void {
  if (streaming.value || uploading.value || !herb) return
  if (chatInput.value) {
    chatInput.value.value = '讲讲' + herb
    pendingAgent = '讲解'
    void send()
  }
}

defineExpose({ teachHerb })

// ---- localStorage 保存（按当前会话分键）----

function saveLocalTo(id: string): void {
  try {
    let list = units.value
    if (list.length >= 500) list = list.slice(list.length - 200) // 上限截断，防 localStorage 膨胀
    // blob: 前缀是临时 URL，跨重启无效——序列化时清掉（/uploads server URL 正常保留）
    const cleaned: ChatUnit[] = list.map((u) =>
      u.kind === 'msg' && u.msg && u.msg.image && u.msg.image.startsWith('blob:')
        ? { kind: 'msg', msg: { ...u.msg, image: undefined } }
        : u
    )
    localStorage.setItem(unitsKey(id), JSON.stringify(cleaned))
    localStorage.setItem(statsKey(id), JSON.stringify(stats.value ?? null))
    // 会话索引：标题 = 首条用户文字（截 20 字），updated = 现在
    const firstUser = units.value.find((u) => u.kind === 'msg' && u.msg?.kind === 'user' && u.msg.text?.trim())
    touchSession(sessions.value, id, firstUser ? firstUser.msg!.text!.trim().slice(0, 20) : '')
  } catch {
    /* QuotaExceeded/隐私模式：静默 */
  }
}

function saveLocal(): void {
  if (currentId.value) saveLocalTo(currentId.value)
}

onMounted(() => {
  // 会话初始化：迁移旧单会话数据 → 读索引/当前会话 → 恢复显示层（KeepAlive 下只执行一次）
  const { index, current } = initSessions()
  sessions.value = index
  currentId.value = current
  restoreUnits(current)
  // 合并服务端会话（sessions.json 里有、本机索引没有的 → 补进列表，如后端恢复的其他会话）
  fetch('/api/sessions')
    .then((r) => r.json())
    .then((j) => {
      if (j && Array.isArray(j.sessions)) mergeServerSessions(sessions.value, j.sessions)
    })
    .catch(() => {
      /* 后端未起/断网：本地索引照常用 */
    })
})

// 深监听（steps 是嵌套 reactive 数组原地 push，必须 deep）→ 防抖写盘
watch([units, stats], () => {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(saveLocal, 500)
}, { deep: true })
</script>

<template>
  <h2>智能体对话</h2>
  <!-- 历史会话栏：切换 / 新建 / 删除（多会话管理，2026-08-29） -->
  <div class="session-bar">
    <select class="sess-select" :value="currentId" :title="'历史会话（' + sessions.length + ' 个，最近优先）'"
            @change="switchSession(($event.target as HTMLSelectElement).value)">
      <option v-for="s in sortedSessions" :key="s.id" :value="s.id">
        {{ (s.title || '新会话').slice(0, 16) }}（{{ fmtTime(s.updated) }}）
      </option>
    </select>
    <button class="sess-btn" @click="newSession" :disabled="streaming || uploading">＋ 新会话</button>
    <button class="sess-btn sess-del" @click="deleteSession"
            :disabled="streaming || uploading || sessions.length <= 1" title="删除当前会话">🗑</button>
  </div>
  <ContextStatsBar :stats="stats" />
  <div class="chat-body" ref="chatBody">
    <template v-for="(u, i) in units" :key="'u' + i">
      <ChatMessage v-if="u.kind === 'msg'" :kind="u.msg!.kind" :text="u.msg!.text" :image="u.msg!.image"
                   :refuse-reason="u.msg!.refuseReason" :evidence="u.msg!.evidence" :animate="u.msg!.animate"
                   @tick="scrollBottom" />
      <TimelineItem v-else :steps="u.steps || []" />
    </template>
    <!-- 处理中指示：上传识别/思考期间（LLM 挂起/工具执行间隙）给用户持续可见的反馈，不再「看着卡死」 -->
    <div class="thinking" v-if="streaming || uploading">{{ uploading ? '识别中' : '思考中' }}<span class="dots"></span></div>
  </div>

  <!-- 上传并入输入框：📎 + 拖拽 / Ctrl+V 粘贴选图 → 待发预览 → 点发送才上传识别 -->
  <div class="chat-zone" :class="{ 'drag-over': dragOver }"
       @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onDrop"
       @paste="onPaste">
    <div class="pending-img" v-if="pendingImage">
      <img :src="pendingImage.url" alt="待发送图片">
      <span class="pending-hint">待发送图片</span>
      <button class="pending-x" title="移除" @click="clearPending">×</button>
    </div>
    <div class="chat-input">
      <button class="clip-btn" title="添加图片（点击 / 拖拽 / Ctrl+V 粘贴）" @click="fileInput?.click()"
              :disabled="uploading">📎</button>
      <textarea ref="chatInput" rows="2"
                placeholder="例如：这个能和菊花一起泡水吗？我最近眼睛干&#10;（Enter 发送，Shift+Enter 换行）"
                @keydown="onKeydown"></textarea>
      <button v-if="!streaming" ref="sendBtn" :disabled="streaming || uploading" @click="send">发送</button>
      <button v-else class="stop-btn" @click="stopStream">停止</button>
      <input ref="fileInput" type="file" accept=".jpg,.jpeg,.png,.webp,.bmp" hidden
             @change="pickFile(($event.target as HTMLInputElement).files?.[0]); ($event.target as HTMLInputElement).value = ''">
    </div>
  </div>
</template>

<style scoped>
/* 思考中指示（底部转圈点） */
.thinking {
  font-size: 12px;
  color: var(--gray);
  padding: 2px 0 6px;
}
.thinking .dots::after {
  content: "";
  animation: dots 1.2s steps(4) infinite;
}
@keyframes dots {
  0% { content: ""; }
  25% { content: "."; }
  50% { content: ".."; }
  75% { content: "..."; }
 100% { content: ""; }
}
/* 停止按钮（streaming 期间替换发送按钮） */
.stop-btn {
  background: var(--red-soft) !important;
  color: var(--red) !important;
}
/* 历史会话栏（多会话切换/新建/删除） */
.session-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  align-items: center;
}
.sess-select {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card);
  color: var(--text);
  padding: 6px 10px;
  font-size: 13px;
  font-family: inherit;
  outline: none;
}
.sess-select:focus { border-color: var(--green); }
.sess-btn {
  border: 1px solid var(--border);
  border-radius: 999px;
  background: transparent;
  color: var(--gray);
  font-size: 12px;
  padding: 3px 12px;
  cursor: pointer;
  white-space: nowrap;
}
.sess-btn:hover:not(:disabled) { color: var(--green-dark); border-color: var(--green-dark); }
.sess-btn:disabled { opacity: .5; cursor: default; }
.sess-del { padding: 3px 8px; }

/* 上传并入输入框：外围拖拽区（虚线框 hover/拖入高亮）+ 待发预览行 */
.chat-zone {
  margin-top: 12px;
  padding: 6px;
  border: 2px dashed transparent;
  border-radius: 10px;
  transition: border-color 0.2s, background 0.2s;
}
.chat-zone:hover,
.chat-zone.drag-over {
  border-color: var(--green);
  background: var(--green-soft);
}
.chat-zone.drag-over textarea {
  pointer-events: none; /* 拖入时避免 textarea 吞掉 drop 事件 */
}
.chat-zone .chat-input {
  margin-top: 0; /* 抵消全局 .chat-input 的 12px 顶部间距（已由 .chat-zone 提供） */
}
.clip-btn {
  /* 覆盖全局 .chat-input button 的绿色大按钮样式（specificity：scoped .chat-zone .chat-input .clip-btn 更高） */
  background: transparent !important;
  color: var(--green-dark) !important;
  padding: 0 8px !important;
  font-size: 16px;
  border: none !important;
}
.clip-btn:disabled {
  color: var(--gray) !important;
}
.pending-img {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
}
.pending-img img {
  max-height: 96px;
  max-width: 180px;
  border-radius: 6px;
  border: 1px solid var(--border);
}
.pending-hint {
  font-size: 12px;
  color: var(--gray);
}
.pending-x {
  border: none;
  background: none;
  color: var(--gray);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 2px 6px;
}
.pending-x:hover {
  color: var(--danger, #c33);
}
</style>
