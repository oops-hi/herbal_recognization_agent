import { ref } from 'vue'

export interface HealthState {
  model: { text: string; cls: string; title: string }
  api: { text: string; cls: string }
}

/* 模块级单例：App.vue 与设置页共享同一份 ref，设置页保存后 header 徽章即时变 */
const model = ref({ text: '模型检查中…', cls: 'badge', title: '' })
const api = ref({ text: 'API 检查中…', cls: 'badge' })

async function refresh(): Promise<void> {
  try {
    const j = await (await fetch('/api/health')).json()
    model.value = {
      text: j.model_ready ? '模型：就绪' : '模型：未就绪',
      cls: 'badge ' + (j.model_ready ? 'ok' : 'warn'),
      title: j.model_reason || ''
    }
    api.value = {
      text: j.deepseek_configured ? '对话：已配置' : '对话：未配置',
      cls: 'badge ' + (j.deepseek_configured ? 'ok' : 'warn')
    }
  } catch (e) {
    model.value = { text: '服务异常', cls: 'badge warn', title: '' }
  }
}

export function useHealth() {
  return { model, api, refresh }
}
