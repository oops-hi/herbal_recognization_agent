<script setup lang="ts">
/* 设置页：通用 OpenAI 兼容 LLM 配置（API 地址 / Key / 模型名）+ 视觉验证（二期 V2-A6 云端 VLM）
   + 跨对话记忆（LLM 自动摘要，/api/memory）
   三卡独立：聊天配置 / 视觉验证 / 跨对话记忆 互不干扰，均写 exe 旁文件立即生效 */
import { onMounted, ref } from 'vue'
import { useHealth } from '../composables/useHealth'

const { refresh } = useHealth()

// ---------- 当前配置快照（GET /api/config 回显） ----------
const baseUrl = ref('')
const model = ref('')
const keyMasked = ref('')
const keyInput = ref('')       // 密码框；留空 = 保留已保存 Key
const clearKey = ref(false)    // 勾选 = 保存时删除已保存 Key
const currentConfigured = ref(false)

// ---------- 视觉验证配置快照（GET /api/vision-config 回显，V2-A6） ----------
const vBaseUrl = ref('')
const vModel = ref('')
const vKeyMasked = ref('')
const vKeyInput = ref('')
const vClearKey = ref(false)
const vEnabled = ref(false)
const vConfigured = ref(false)

// ---------- 反馈条 ----------
const busy = ref(false)
const vBusy = ref(false)
const msg = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)
const vMsg = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)

function showOk(text: string): void { msg.value = { kind: 'ok', text } }
function showErr(text: string): void { msg.value = { kind: 'err', text } }
function vShowOk(text: string): void { vMsg.value = { kind: 'ok', text } }
function vShowErr(text: string): void { vMsg.value = { kind: 'err', text } }

async function loadConfig(): Promise<void> {
  try {
    const r = await fetch('/api/config')
    const j = await r.json()
    baseUrl.value = j.base_url || ''
    model.value = j.model || ''
    keyMasked.value = j.key_masked || ''
    currentConfigured.value = !!j.configured
  } catch (e) {
    showErr('无法读取当前配置（后端服务不可用）')
  }
}

async function loadVisionConfig(): Promise<void> {
  try {
    const r = await fetch('/api/vision-config')
    const j = await r.json()
    vBaseUrl.value = j.base_url || ''
    vModel.value = j.model || ''
    vKeyMasked.value = j.key_masked || ''
    vEnabled.value = !!j.enabled
    vConfigured.value = !!j.configured
  } catch (e) {
    vShowErr('无法读取视觉验证配置（后端服务不可用）')
  }
}

// ---------- 视觉验证：前端校验 ----------
function vValidate(): string | null {
  const url = vBaseUrl.value.trim()
  if (!url) return 'API 地址不能为空'
  if (!/^https?:\/\//.test(url)) return 'API 地址必须以 http:// 或 https:// 开头'
  if (!vModel.value.trim()) return '模型名不能为空'
  if (!vClearKey.value && !vKeyInput.value.trim() && !vKeyMasked.value) return '尚未保存过 Key：请填写 API Key'
  return null
}

// ---------- 视觉验证：保存 / 测试 ----------
async function vSave(): Promise<void> {
  const err = vValidate()
  if (err) { vShowErr(err); return }
  vBusy.value = true
  try {
    const r = await fetch('/api/vision-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url: vBaseUrl.value.trim(),
        api_key: vKeyInput.value.trim(),
        model: vModel.value.trim(),
        enabled: vEnabled.value,
        clear_key: vClearKey.value
      })
    })
    const j = await r.json()
    if (!r.ok) { vShowErr(j.error || '保存失败'); return }
    vKeyInput.value = ''
    vKeyMasked.value = j.key_masked || ''
    vEnabled.value = !!j.enabled
    vConfigured.value = !!j.configured
    vShowOk(vEnabled.value
      ? '已保存并开启视觉验证：低置信 / 混淆对识别将自动调用云端复核'
      : '已保存（视觉验证关闭，识别行为与之前一致）')
    vClearKey.value = false
  } catch (e) {
    vShowErr('保存失败：网络异常')
  } finally {
    vBusy.value = false
  }
}

async function vTest(): Promise<void> {
  const err = vValidate()
  if (err) { vShowErr(err); return }
  vBusy.value = true
  vMsg.value = { kind: 'ok', text: '正在测试连接…' }
  try {
    const r = await fetch('/api/vision-config/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url: vBaseUrl.value.trim(),
        api_key: vKeyInput.value.trim(),
        model: vModel.value.trim()
      })
    })
    const j = await r.json()
    if (!r.ok) { vShowErr(j.error || '连接失败'); return }
    vShowOk(j.message || '连接成功')
  } catch (e) {
    vShowErr('测试失败：网络异常')
  } finally {
    vBusy.value = false
  }
}

// ---------- 前端校验（不通过不发请求） ----------
function validate(): string | null {
  const url = baseUrl.value.trim()
  if (!url) return 'API 地址不能为空'
  if (!/^https?:\/\//.test(url)) return 'API 地址必须以 http:// 或 https:// 开头'
  if (!model.value.trim()) return '模型名不能为空'
  if (!clearKey.value && !keyInput.value.trim() && !keyMasked.value) return '尚未保存过 Key：请填写 API Key'
  return null
}

// ---------- 保存 / 测试 ----------
async function save(): Promise<void> {
  const err = validate()
  if (err) { showErr(err); return }
  busy.value = true
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url: baseUrl.value.trim(),
        api_key: keyInput.value.trim(),
        model: model.value.trim(),
        clear_key: clearKey.value
      })
    })
    const j = await r.json()
    if (!r.ok) { showErr(j.error || '保存失败'); return }
    // 保存成功 → 徽章即时更新 + 回显新掩码 + 清空 Key 输入
    await refresh()
    keyInput.value = ''
    keyMasked.value = j.key_masked || ''
    currentConfigured.value = !!j.configured
    showOk(clearKey.value ? '已清除 Key 并保存配置' : '已保存，对话服务立即生效（无需重启）')
    clearKey.value = false
  } catch (e) {
    showErr('保存失败：网络异常')
  } finally {
    busy.value = false
  }
}

async function test(): Promise<void> {
  const err = validate()
  if (err) { showErr(err); return }
  busy.value = true
  msg.value = { kind: 'ok', text: '正在测试连接…' }
  try {
    const r = await fetch('/api/config/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url: baseUrl.value.trim(),
        api_key: keyInput.value.trim(),
        model: model.value.trim()
      })
    })
    const j = await r.json()
    if (!r.ok) { showErr(j.error || '连接失败'); return }
    showOk(j.message || '连接成功')
  } catch (e) {
    showErr('测试失败：网络异常')
  } finally {
    busy.value = false
  }
}

// ---------- 跨对话记忆（GET/POST/DELETE /api/memory · LLM 自动画像提取） ----------
const memEnabled = ref(false)
const memUpdated = ref(0)
const memEntries = ref<Array<{ id: string; ts: number; text: string; herbs: string[]; kind?: string }>>([])
const memBusy = ref(false)
const memMsg = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)

const MEM_KIND_LABELS: Record<string, string> = {
  identity: '身份', goal: '目标', health: '健康', preference: '偏好', fact: '事实'
}
function memKindLabel(kind?: string): string {
  return MEM_KIND_LABELS[kind || ''] || '偏好'
}

function memShowOk(text: string): void { memMsg.value = { kind: 'ok', text } }
function memShowErr(text: string): void { memMsg.value = { kind: 'err', text } }

async function loadMemory(): Promise<void> {
  try {
    const r = await fetch('/api/memory')
    const j = await r.json()
    memEnabled.value = !!j.enabled
    memUpdated.value = j.updated || 0
    memEntries.value = Array.isArray(j.entries) ? j.entries : []
  } catch (e) {
    memShowErr('无法读取记忆（后端服务不可用）')
  }
}

async function saveMemory(): Promise<void> {
  memBusy.value = true
  try {
    const r = await fetch('/api/memory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: memEnabled.value })
    })
    const j = await r.json()
    if (!r.ok) { memShowErr(j.error || '保存失败'); return }
    memEnabled.value = !!j.enabled
    memUpdated.value = j.updated || 0
    memEntries.value = Array.isArray(j.entries) ? j.entries : []
    memShowOk(memEnabled.value
      ? '已开启：连续对话几轮后自动整理您的偏好与常问主题'
      : '已关闭：不再自动整理，已有条目保留')
  } catch (e) {
    memShowErr('保存失败：网络异常')
  } finally {
    memBusy.value = false
  }
}

async function removeEntry(id: string): Promise<void> {
  memBusy.value = true
  try {
    const r = await fetch('/api/memory/' + encodeURIComponent(id), { method: 'DELETE' })
    const j = await r.json()
    if (!r.ok) { memShowErr(j.error || '删除失败'); return }
    memEntries.value = Array.isArray(j.entries) ? j.entries : []
  } catch (e) {
    memShowErr('删除失败：网络异常')
  } finally {
    memBusy.value = false
  }
}

function fmtMemTime(ts: number): string {
  return ts ? new Date(ts * 1000).toLocaleString() : '—'
}

onMounted(() => {
  void loadConfig()
  void loadVisionConfig()
  void loadMemory()
})
</script>

<template>
  <div class="settings-wrap">
    <div class="card">
      <h2>大模型配置</h2>
      <p class="sub">对接任意 OpenAI 兼容服务（默认 DeepSeek，可换 Kimi / 智谱 / Ollama 等）。保存后立即生效，无需重启。</p>

      <!-- 当前配置快照 -->
      <div class="state-row">
        <span class="kv">
          <b>当前状态：</b>
          <span class="pill" :class="currentConfigured ? 'ok' : 'warn'">
            {{ currentConfigured ? '已配置' : '未配置' }}
          </span>
        </span>
        <span class="kv"><b>API 地址：</b>{{ baseUrl || '—' }}</span>
        <span class="kv"><b>模型：</b>{{ model || '—' }}</span>
        <span class="kv"><b>API Key：</b>{{ keyMasked || '（无）' }}</span>
      </div>

      <!-- 表单 -->
      <label class="field">
        <span>API 地址（base_url）</span>
        <input v-model="baseUrl" type="text" placeholder="https://api.deepseek.com"
               @keydown.enter.prevent="save">
        <span class="hint">完整地址或域名均可：https://api.deepseek.com 会自动补 /chat/completions</span>
      </label>

      <label class="field">
        <span>API Key</span>
        <input v-model="keyInput" type="password" placeholder="留空 = 保留已保存的 Key"
               autocomplete="off" @keydown.enter.prevent="save">
        <span class="hint">密钥仅保存在本机 exe 旁的 .env，不会离开您的电脑</span>
      </label>

      <label class="field">
        <span>模型名</span>
        <input v-model="model" type="text" placeholder="deepseek-chat" @keydown.enter.prevent="save">
      </label>

      <!-- 操作行 -->
      <div class="actions">
        <button class="btn" :disabled="busy" @click="test">测试连接</button>
        <button class="btn primary" :disabled="busy" @click="save">保存</button>
        <label class="clear-box">
          <input v-model="clearKey" type="checkbox">
          清除已保存的 Key
        </label>
      </div>

      <!-- 反馈条 -->
      <div v-if="msg" class="feedback" :class="msg.kind">{{ msg.text }}</div>

      <p class="foot-note">
        <b>说明：</b>「清除 Key」保存后立即失效并删除本机 .env 中的密钥；
        测试连接仅验证当前输入（不落盘），保存才会写入配置。
      </p>
    </div>

    <!-- ============ 第二卡：视觉验证（二期 V2-A6 云端 VLM，默认关闭） ============ -->
    <div class="card">
      <h2>视觉验证（云端 VLM 辅助识别）</h2>
      <p class="sub">可选增强：低置信 / 混淆对识别时调用云端视觉大模型做第二通道复核（双通道一致提信、域外图拒答）。
        默认关闭，识别行为与之前完全一致；仅灰区触发，不影响正常识别速度。</p>

      <!-- 当前配置快照 -->
      <div class="state-row">
        <span class="kv">
          <b>当前状态：</b>
          <span class="pill" :class="vEnabled ? 'ok' : 'warn'">
            {{ vEnabled ? '已开启' : '已关闭' }}
          </span>
        </span>
        <span class="kv"><b>API 地址：</b>{{ vBaseUrl || '—' }}</span>
        <span class="kv"><b>模型：</b>{{ vModel || '—' }}</span>
        <span class="kv"><b>API Key：</b>{{ vKeyMasked || '（无）' }}</span>
      </div>

      <!-- 表单 -->
      <label class="field">
        <span>API 地址（base_url）</span>
        <input v-model="vBaseUrl" type="text" placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
               @keydown.enter.prevent="vSave">
        <span class="hint">默认阿里云百炼（qwen-vl-max），OpenAI 兼容端点可换其他视觉模型</span>
      </label>

      <label class="field">
        <span>API Key</span>
        <input v-model="vKeyInput" type="password" placeholder="留空 = 保留已保存的 Key"
               autocomplete="off" @keydown.enter.prevent="vSave">
        <span class="hint">与聊天密钥相互独立；图片仅临时发送给该服务用于归类判断，不落库</span>
      </label>

      <label class="field">
        <span>模型名</span>
        <input v-model="vModel" type="text" placeholder="qwen-vl-max" @keydown.enter.prevent="vSave">
      </label>

      <!-- 操作行 -->
      <div class="actions">
        <button class="btn" :disabled="vBusy" @click="vTest">测试连接</button>
        <button class="btn primary" :disabled="vBusy" @click="vSave">保存</button>
        <label class="clear-box">
          <input v-model="vClearKey" type="checkbox">
          清除已保存的 Key
        </label>
        <label class="toggle">
          <input v-model="vEnabled" type="checkbox">
          开启视觉验证
        </label>
      </div>

      <!-- 反馈条 -->
      <div v-if="vMsg" class="feedback" :class="vMsg.kind">{{ vMsg.text }}</div>

      <p class="foot-note">
        <b>说明：</b>开启后仅当识别进入灰区（置信度 0.60~0.90、命中混淆对、候选差距小）才调用云端；
        断网 / 超时 / 余额不足自动跳过（回退本地结论），不影响识别、图谱与对话。默认关闭更省。
      </p>
    </div>

    <!-- ============ 第三卡：跨对话记忆（LLM 自动摘要，默认开启） ============ -->
    <div class="card">
      <h2>跨对话记忆</h2>
      <p class="sub">智能体在连续对话后自动整理您的偏好与常问主题（如「常问枸杞子搭配」「中医学生备考」），
        新对话时自动带入。只记用户偏好，不记任何药性结论。</p>

      <!-- 当前状态快照 -->
      <div class="state-row">
        <span class="kv">
          <b>当前状态：</b>
          <span class="pill" :class="memEnabled ? 'ok' : 'warn'">
            {{ memEnabled ? '已开启' : '已关闭' }}
          </span>
        </span>
        <span class="kv"><b>记忆条数：</b>{{ memEntries.length }}/20</span>
        <span class="kv"><b>最后更新：</b>{{ fmtMemTime(memUpdated) }}</span>
      </div>

      <!-- 操作行 -->
      <div class="actions">
        <label class="toggle">
          <input v-model="memEnabled" type="checkbox">
          开启跨对话记忆
        </label>
        <button class="btn primary" :disabled="memBusy" @click="saveMemory">保存</button>
      </div>

      <!-- 条目列表 -->
      <div v-if="memEntries.length" class="mem-list">
        <div v-for="e in memEntries" :key="e.id" class="mem-item">
          <span class="mem-kind" :class="'k-' + (e.kind || 'preference')">{{ memKindLabel(e.kind) }}</span>
          <span class="mem-text">{{ e.text }}</span>
          <span v-if="e.herbs && e.herbs.length" class="mem-herbs">{{ e.herbs.join('、') }}</span>
          <button class="mem-del" :disabled="memBusy" title="删除该条记忆" @click="removeEntry(e.id)">删除</button>
        </div>
      </div>
      <p v-else class="mem-empty">暂无记忆条目。连续对话几轮后，智能体会自动整理您的偏好与常问主题。</p>

      <!-- 反馈条 -->
      <div v-if="memMsg" class="feedback" :class="memMsg.kind">{{ memMsg.text }}</div>

      <p class="foot-note">
        <b>说明：</b>记忆仅存「用户偏好 / 常问主题」等用户层面信息（保存在本机 memory.json，不上传）；
        药性 / 功效 / 用量 / 禁忌一律实时查询知识图谱，记忆不作为药性依据。
      </p>
    </div>
  </div>
</template>

<style scoped>
.settings-wrap {
  max-width: 720px;
  margin: 24px auto;
  padding: 0 16px;
}
.sub { color: var(--gray); font-size: 13px; margin: -4px 0 14px; }
.state-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  padding: 10px 12px;
  background: var(--green-soft);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 18px;
}
.kv b { color: var(--gray); font-weight: 500; }
.pill {
  display: inline-block;
  padding: 1px 10px;
  border-radius: 10px;
  color: #fff;
  font-size: 12px;
}
.pill.ok { background: #3d8b5f; }
.pill.warn { background: #b3402f; }
.field { display: block; margin-bottom: 14px; }
.field span { display: block; font-size: 13px; color: var(--gray); margin-bottom: 4px; }
.field input {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
  font-size: 14px;
  box-sizing: border-box;
}
.field input:focus { outline: none; border-color: var(--green); }
.hint { font-size: 12px; margin-top: 4px; }
.actions { display: flex; align-items: center; gap: 10px; margin: 6px 0 14px; }
.btn {
  padding: 8px 18px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
  color: var(--green-dark);
  font-size: 14px;
  cursor: pointer;
}
.btn:hover { border-color: var(--green); }
.btn.primary { background: var(--green); border-color: var(--green); color: #f4efe6; }
.btn.primary:hover { background: var(--green-dark); }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.clear-box { font-size: 13px; color: var(--gray); display: flex; align-items: center; gap: 6px; cursor: pointer; }
.toggle { font-size: 13px; color: var(--green-dark); display: flex; align-items: center; gap: 6px; cursor: pointer; }
.feedback {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  margin-bottom: 12px;
}
.feedback.ok { background: var(--green-soft); color: var(--green-dark); border: 1px solid var(--green); }
.feedback.err { background: #fbeae7; color: #b3402f; border: 1px solid #d8a59c; }
.foot-note { font-size: 12px; color: var(--gray); }

/* ---------- 跨对话记忆卡 ---------- */
.mem-list {
  margin: 4px 0 14px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.mem-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  font-size: 13px;
  border-bottom: 1px solid var(--border);
}
.mem-item:last-child { border-bottom: none; }
.mem-kind {
  flex-shrink: 0;
  padding: 0 8px;
  border-radius: 9px;
  font-size: 11px;
  color: #fff;
}
.mem-kind.k-identity { background: #3d8b5f; }
.mem-kind.k-goal { background: #6b5bb5; }
.mem-kind.k-health { background: #b8863f; }
.mem-kind.k-preference { background: #4a7ba6; }
.mem-kind.k-fact { background: #8b5f3d; }
.mem-text { flex: 1; color: var(--green-dark); }
.mem-herbs { color: var(--gray); font-size: 12px; flex-shrink: 0; }
.mem-del {
  flex-shrink: 0;
  padding: 2px 10px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #fff;
  color: #b3402f;
  cursor: pointer;
}
.mem-del:disabled { opacity: 0.6; cursor: not-allowed; }
.mem-empty { font-size: 13px; color: var(--gray); margin: 0 0 14px; }
</style>
