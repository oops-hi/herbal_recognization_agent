<script setup lang="ts">
/* 上传区：拖拽/点击 → 校验 → 预览 → emit(upload)（main.js pickFile/拖拽 原逻辑） */
import { ref } from 'vue'

const emit = defineEmits<{
  upload: [file: File]
  error: [msg: string]
  reset: []
}>()
const fileInput = ref<HTMLInputElement>()
const dragging = ref(false)
const previewUrl = ref<string>('')
const previewVisible = ref(false)

function pickFile(file: File | null | undefined): void {
  if (!file) return
  if (!/\.(jpe?g|png|webp|bmp)$/i.test(file.name)) {
    emit('error', '不支持的文件格式，仅支持 jpg / png / webp / bmp')
    return
  }
  if (file.size > 16 * 1024 * 1024) {
    emit('error', '图片过大，请上传 16MB 以内的图片')
    return
  }
  // 本地预览
  previewUrl.value = URL.createObjectURL(file)
  previewVisible.value = true
  emit('reset') // 隐藏旧识别卡/建议/错误
  emit('upload', file)
}

function onDrop(e: DragEvent): void {
  e.preventDefault()
  dragging.value = false
  pickFile(e.dataTransfer?.files?.[0])
}
</script>

<template>
  <div class="dropzone" :class="{ dragover: dragging }" @click="fileInput?.click()"
       @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop="onDrop">
    <div class="big">点击或拖拽图片到此处</div>
    <div>支持 jpg / png / webp / bmp，≤ 16MB</div>
    <input ref="fileInput" type="file" accept=".jpg,.jpeg,.png,.webp,.bmp" hidden
           @change="pickFile(($event.target as HTMLInputElement).files?.[0])">
  </div>

  <div class="preview-box" v-show="previewVisible">
    <img :src="previewUrl" alt="预览">
  </div>
</template>
