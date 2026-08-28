import { ref } from 'vue'

export interface HealthState {
  model: { text: string; cls: string; title: string }
  api: { text: string; cls: string }
}

/* 启动时健康检查：badge-model / badge-api（main.js L90-102 原逻辑） */
export function useHealth() {
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
        text: j.deepseek_configured ? 'DeepSeek：已配置' : 'DeepSeek：未配置',
        cls: 'badge ' + (j.deepseek_configured ? 'ok' : 'warn')
      }
    } catch (e) {
      model.value = { text: '服务异常', cls: 'badge warn', title: '' }
    }
  }

  return { model, api, refresh }
}
