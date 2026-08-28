<script setup lang="ts">
/* 应用骨架：header（徽章）+ 路由视图 + 免责声明 footer（index.html/graph.html 原结构） */
import { onMounted } from 'vue'
import { useHealth } from './composables/useHealth'

const { model, api, refresh } = useHealth()

onMounted(() => {
  void refresh()
})
</script>

<template>
  <header>
    <h1>🌿 多模态中草药识别智能体</h1>
    <span class="tagline">图像识别 · 知识图谱 · 智能体工具链</span>
    <div class="header-right">
      <span class="badge" :class="model.cls" :title="model.title">{{ model.text }}</span>
      <span class="badge" :class="api.cls">{{ api.text }}</span>
      <router-link to="/graph" class="badge" style="text-decoration: none">📊 知识图谱</router-link>
      <router-link to="/settings" class="badge" style="text-decoration: none">⚙️ 设置</router-link>
    </div>
  </header>

  <router-view />

  <footer class="disclaimer">
    <b>免责声明：</b>本工具仅供学习与科普参考，不构成医疗、诊断或用药建议；识别结果存在误差，
    不能作为药材真伪、安全性或食用/药用依据，请以执业药师或专业机构鉴定为准；
    有毒药材信息依据《中国药典》标注，请勿自行采食；紧急情况请立即就医。
  </footer>
</template>
