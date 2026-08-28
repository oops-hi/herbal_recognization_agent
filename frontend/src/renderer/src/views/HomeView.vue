<script setup lang="ts">
/* 主页：上传识别（左卡）+ 智能体对话（右卡）（main.js 全量逻辑） */
import { ref } from 'vue'
import UploadZone from '../components/UploadZone.vue'
import RecCard, { type RecCardData } from '../components/RecCard.vue'
import AdviceBox, { type AdviceData } from '../components/AdviceBox.vue'
import ChatPanel from '../components/ChatPanel.vue'
import { useClientId } from '../composables/useClientId'

const clientId = useClientId()
const chatPanel = ref<InstanceType<typeof ChatPanel>>()

interface UploadResp {
  status: 'ok' | 'low_confidence' | 'model_not_ready'
  top1?: string
  confidence?: number
  top3?: { name: string; confidence: number }[]
  profile?: string
  advice?: string[]
  message?: string
  error?: string
  vision?: VisionMeta // 二期 V2-A6：云端 VLM 二段裁决
}

// vision.state: consistent(绿) | conflict(黄) | non_herb(红) | none(黄) | skipped/unavailable(灰)
interface VisionMeta {
  state: string
  vlm_top1?: string
  verdict?: string
  reason?: string
}

const recData = ref<RecCardData | null>(null)
const adviceData = ref<AdviceData | null>(null)
const uploadError = ref('')
const visionBadge = ref<VisionMeta | null>(null)

const visionBadgeText = (v: VisionMeta): string => {
  switch (v.state) {
    case 'consistent': return '双通道一致（云端复核：' + (v.vlm_top1 || '') + '）'
    case 'conflict': return '双通道分歧（云端：' + (v.vlm_top1 || '?') + '）未下结论'
    case 'non_herb': return '云端判定：域外图，拒绝下结论'
    case 'none': return '云端复核：未能确认，未下结论'
    case 'unavailable': return '云端复核不可用，已回退本地结论'
    default: return '云端复核未参与'
  }
}

function showError(msg: string): void {
  uploadError.value = msg
}

async function onUpload(file: File): Promise<void> {
  const fd = new FormData()
  fd.append('image', file)
  fd.append('client_id', clientId)
  try {
    const resp = await fetch('/upload', { method: 'POST', body: fd })
    const j = (await resp.json()) as UploadResp
    if (!resp.ok) {
      showError(j.error || '上传失败')
      return
    }
    visionBadge.value = j.vision || null
    if (j.status === 'ok') {
      recData.value = {
        top1: j.top1!,
        confidence: j.confidence!,
        top3: j.top3!,
        profile: j.profile || ''
      }
      chatPanel.value?.pushSystemNote(
        '已识别： ' + j.top1 + '（置信度 ' + (j.confidence! * 100).toFixed(1) +
        '%）—— 可直接提问，如『这个能和菊花一起泡水吗？』'
      )
    } else if (j.status === 'low_confidence') {
      adviceData.value = { advice: j.advice!, top3: j.top3 || [] }
    } else if (j.status === 'model_not_ready') {
      showError(j.message + '。上传已保存，可先体验图谱查询与（配置密钥后的）对话。')
    }
  } catch (e) {
    showError('网络异常：' + (e as Error).message)
  }
}

// 上传新图时清掉旧识别卡/建议/错误
function onReset(): void {
  recData.value = null
  adviceData.value = null
  uploadError.value = ''
  visionBadge.value = null
}
</script>

<template>
  <main>
    <!-- ============ 左：上传识别区 ============ -->
    <section class="card">
      <h2>上传中药饮片图片</h2>
      <UploadZone @upload="onUpload" @error="showError" @reset="onReset" />

      <RecCard v-if="recData" :data="recData" />
      <AdviceBox v-if="adviceData" :data="adviceData" />
      <div class="vision-badge" v-if="visionBadge" :data-state="visionBadge.state">
        {{ visionBadgeText(visionBadge) }}
      </div>
      <div class="error-box" v-if="uploadError">{{ uploadError }}</div>
    </section>

    <!-- ============ 右：对话区 ============ -->
    <section class="card">
      <ChatPanel ref="chatPanel" />
    </section>
  </main>
</template>
