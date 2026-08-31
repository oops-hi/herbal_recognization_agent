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
    <!-- 品牌 logo 可点击返回对话首页（Electron 无浏览器返回按钮，导航必须显式可回） -->
    <h1><router-link to="/" class="brand-link"><img src="./assets/herb-child-logo.png" alt="多模态中草药识别智能体" class="brand-logo" /></router-link></h1>
    <div class="header-right">
      <span class="badge" :class="model.cls" :title="model.title">{{ model.text }}</span>
      <span class="badge" :class="api.cls">{{ api.text }}</span>
      <router-link to="/" class="badge" style="text-decoration: none">💬 对话</router-link>
      <router-link to="/graph" class="badge" style="text-decoration: none">📊 知识图谱</router-link>
      <router-link to="/settings" class="badge" style="text-decoration: none">⚙️ 设置</router-link>
    </div>
  </header>

  <!-- KeepAlive：切图谱/设置页不丢对话记录（HomeView 保活，含 units/识别卡/统计） -->
  <router-view v-slot="{ Component }">
    <keep-alive :include="['HomeView']">
      <component :is="Component" />
    </keep-alive>
  </router-view>

  <footer class="disclaimer">
    <b>免责声明：</b>本工具仅供学习与科普参考，不构成医疗、诊断或用药建议；识别结果请以执业药师或专业机构鉴定为准。
  </footer>
</template>
