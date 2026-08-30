<script setup lang="ts">
/* 主页：识别结果（左卡）+ 智能体对话（右卡，上传入口已并入对话输入框）
   2026-08-29：上传/识别逻辑下沉到 ChatPanel（@rec-result 事件回填左卡） */
import { ref } from 'vue'
import RecCard, { type RecCardData } from '../components/RecCard.vue'
import AdviceBox, { type AdviceData } from '../components/AdviceBox.vue'
import ChatPanel, { type RecResultPayload } from '../components/ChatPanel.vue'

// keep-alive :include 按组件 name 匹配（script setup 按文件名推断，显式声明最稳）
defineOptions({ name: 'HomeView' })

const chatRef = ref<InstanceType<typeof ChatPanel> | null>(null)
const recData = ref<RecCardData | null>(null)
const adviceData = ref<AdviceData | null>(null)
const uploadError = ref('')
const visionBadge = ref<RecResultPayload['visionBadge']>(null)

const visionBadgeText = (v: NonNullable<RecResultPayload['visionBadge']>): string => {
  switch (v.state) {
    case 'consistent': return '双通道一致（云端复核：' + (v.vlm_top1 || '') + '）'
    case 'conflict': return '双通道分歧（云端：' + (v.vlm_top1 || '?') + '），维持本地结论'
    case 'non_herb': return '云端判定：' + (v.category ? '疑似' + v.category + '，' : '') + '域外图，拒绝下结论'
    case 'none': return '云端复核：未能确认，未下结论'
    case 'unavailable': return '云端复核不可用，已回退本地结论'
    default: return '云端复核未参与'
  }
}

// ChatPanel 识别结果回填（undefined=不改该字段；null=清空）
function onRecResult(p: RecResultPayload): void {
  if (p.recData !== undefined) recData.value = p.recData
  if (p.adviceData !== undefined) adviceData.value = p.adviceData
  if (p.visionBadge !== undefined) visionBadge.value = p.visionBadge
  if (p.uploadError !== undefined) uploadError.value = p.uploadError || ''
}
</script>

<template>
  <main>
    <!-- ============ 左：识别结果（上传入口在右侧对话框 📎） ============ -->
    <section class="card result-card">
      <h2>识别结果</h2>
      <div class="empty-hint" v-if="!recData && !adviceData && !uploadError && !visionBadge">
        在右侧对话框 📎 添加或拖入图片开始识别
      </div>
      <RecCard v-if="recData" :data="recData" @teach="(h) => chatRef?.teachHerb(h)" />
      <AdviceBox v-if="adviceData" :data="adviceData" />
      <div class="vision-badge" v-if="visionBadge" :data-state="visionBadge.state">
        {{ visionBadgeText(visionBadge) }}
      </div>
      <div class="error-box" v-if="uploadError">{{ uploadError }}</div>
    </section>

    <!-- ============ 右：对话区（含图片上传入口） ============ -->
    <section class="card">
      <ChatPanel ref="chatRef" @rec-result="onRecResult" />
    </section>
  </main>
</template>
