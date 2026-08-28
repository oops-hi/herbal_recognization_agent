<script setup lang="ts">
/* 设置页：通用 OpenAI 兼容 LLM 配置（API 地址 / Key / 模型名）→ 保存到 exe 旁 .env，立即生效 */
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

// ---------- 反馈条 ----------
const busy = ref(false)
const msg = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)

function showOk(text: string): void { msg.value = { kind: 'ok', text } }
function showErr(text: string): void { msg.value = { kind: 'err', text } }

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

onMounted(() => {
  void loadConfig()
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
.feedback {
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  margin-bottom: 12px;
}
.feedback.ok { background: var(--green-soft); color: var(--green-dark); border: 1px solid var(--green); }
.feedback.err { background: #fbeae7; color: #b3402f; border: 1px solid #d8a59c; }
.foot-note { font-size: 12px; color: var(--gray); }
</style>
