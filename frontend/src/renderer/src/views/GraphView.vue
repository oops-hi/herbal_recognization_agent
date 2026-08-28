<script setup lang="ts">
/* 知识图谱页：D3 力导向交互图（graph.js 原逻辑 Vue 化）+ 档案检索 + 断网降级静态 PNG */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { renderForce, type GraphData, type RenderHandle } from '../lib/graphRenderer'
import staticPng from '../assets/kg_graph.png'

// ---------- 图例（数据驱动 chips） ----------
interface Chip {
  key: string
  label: string
  kind: 'dot' | 'sq' | 'line'
  color: string
  edge?: boolean // true = 边类型
}
const chips: Chip[] = [
  { key: 'herb', label: '带档案药材', kind: 'dot', color: '#4caf50' },
  { key: 'herb_minor', label: '次要节点', kind: 'dot', color: '#bdbdbd' },
  { key: 'formula', label: '经典方剂', kind: 'sq', color: '#42a5f5' },
  { key: '禁忌', label: '配伍禁忌（十八反 / 十九畏）', kind: 'line', color: '#c62828', edge: true },
  { key: '组成', label: '方剂组成', kind: 'line', color: '#b9b2a3', edge: true }
]
const chipOff = ref<Record<string, boolean>>({})

function legendChips(): { node: Record<string, boolean>; edge: Record<string, boolean> } {
  return {
    node: {
      herb: !chipOff.value['herb'],
      herb_minor: !chipOff.value['herb_minor'],
      formula: !chipOff.value['formula']
    },
    edge: { 组成: !chipOff.value['组成'], 禁忌: !chipOff.value['禁忌'] }
  }
}
function toggleChip(key: string): void {
  chipOff.value = { ...chipOff.value, [key]: !chipOff.value[key] }
  handle.value?.refreshVisibility()
}

// ---------- 图谱渲染 / 降级 ----------
const area = ref<HTMLElement>()
const svgEl = ref<SVGSVGElement>()
const tooltip = ref<HTMLElement>()
const loading = ref(true)
const degraded = ref(false)
const statsText = ref('')
const handle = ref<RenderHandle | null>(null)

async function initGraph(): Promise<void> {
  if (!area.value || !svgEl.value || !tooltip.value) return
  try {
    const r = await fetch('/api/graph')
    if (!r.ok) throw new Error('HTTP ' + r.status)
    const data = (await r.json()) as GraphData
    if (!data || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) throw new Error('载荷格式错误')
    handle.value = renderForce(data, {
      area: area.value,
      svgEl: svgEl.value,
      tooltip: tooltip.value,
      legendChips,
      onNodeClick: (id) => void showProfile(id),
      reheatBtn: () => document.getElementById('reheat-btn')
    })
    loading.value = false
  } catch (e) {
    loading.value = false
    degraded.value = true
    console.warn('[graph] 交互式图谱不可用，已降级为静态图：', e)
  }
}

// ---------- 档案检索（graph.js showProfile/herbProfile/formulaProfile 原逻辑） ----------
const profileVisible = ref(false)
const profileText = ref('')

interface ProfileJson {
  found: boolean
  category: string
  name_cn: string
  latin?: string
  source?: string
  profile?: Record<string, string | string[]>
  formula?: { 组成?: { role: string; herb: string }[]; 主治?: string; 出处?: string }
}

function herbProfile(j: ProfileJson): string {
  const p = j.profile!
  return (
    '【' + j.name_cn + '】' + (j.latin || '') + '\n' +
    '性味：' + p['性味'] + '\n' +
    '归经：' + (p['归经'] as string[]).join('、') + '\n' +
    '功效：' + p['功效'] + '\n' +
    '主治：' + p['主治'] + '\n' +
    '用量：' + p['用量'] + '\n' +
    '毒性：' + p['毒性'] + '\n' +
    '禁忌：' + p['禁忌'] + '\n' +
    '出处：' + (j.source || '')
  )
}

function formulaProfile(j: ProfileJson): string {
  const f = j.formula || {}
  const members = (f['组成'] || []).map((m) => m['role'] + '—' + m['herb']).join('  ')
  return (
    '【方剂】' + j.name_cn + '\n' +
    '主治：' + (f['主治'] || '') + '\n' +
    '组成：' + members + '\n' +
    '出处：' + (f['出处'] || j.source || '')
  )
}

async function showProfile(name: string): Promise<void> {
  const q = String(name || '').trim()
  if (!q) return
  profileVisible.value = true
  profileText.value = '查询中…'
  try {
    const r = await fetch('/api/herb/' + encodeURIComponent(q))
    const j = (await r.json()) as ProfileJson
    if (!j.found) {
      profileText.value =
        "知识库未收录「" + q + "」\n该药可能未在 20 类主药档案内，或可尝试别名（如 栝楼皮 → 瓜蒌皮）。"
      return
    }
    if (j.category === 'herb') profileText.value = herbProfile(j)
    else if (j.category === 'formula') profileText.value = formulaProfile(j)
    else profileText.value = '「' + j.name_cn + '」为次要节点（只存名字），可检索其参与的方剂 / 禁忌网络。'
  } catch (e) {
    profileText.value = '查询失败（本页为离线静态渲染，档案查询需要本地服务运行）。'
  }
}

const herbInput = ref('')
const herbSuggest = [
  '枸杞子', '菊花', '瓜蒌皮', '川乌', '山楂', '连翘', '栀子', '砂仁',
  '豆蔻', '草豆蔻', '甘草', '桃仁', '苦杏仁', '金樱子', '补骨脂'
]
function onSearchKey(e: KeyboardEvent): void {
  if (e.key === 'Enter') void showProfile(herbInput.value)
}

// ---------- 图谱统计（/api/health j.graph） ----------
onMounted(async () => {
  void initGraph()
  fetch('/api/health')
    .then((r) => r.json())
    .then((j) => {
      statsText.value = j.graph || ''
    })
    .catch(() => {})
})

onBeforeUnmount(() => {
  handle.value?.destroy()
  handle.value = null
})
</script>

<template>
  <div class="graph-wrap">
    <div ref="area" id="graph-area">
      <!-- 交互式 SVG 默认显示；加载成功后隐藏 loading -->
      <svg ref="svgEl" id="graph-svg"></svg>
      <div class="graph-loading" v-if="loading">图谱加载中…</div>
      <!-- 静态 PNG：仅 /api/graph 失败（断网/后端挂）时降级显示 -->
      <img v-if="degraded" id="graph-static" :src="staticPng" alt="知识图谱">
      <div ref="tooltip" id="graph-tooltip" class="graph-tooltip" hidden></div>
      <div class="fallback-note" v-if="degraded">
        交互式图谱加载失败（服务未运行或已断网），已自动降级为静态渲染图；档案检索随本地服务状态而定。
      </div>
    </div>

    <div class="legend-box">
      <span v-for="c in chips" :key="c.key" class="legend-chip" :class="{ off: chipOff[c.key] }"
            :title="'点击显隐'" @click="toggleChip(c.key)">
        <span v-if="c.kind === 'dot'" class="dot" :style="{ background: c.color }"></span>
        <span v-else-if="c.kind === 'sq'" class="sq" :style="{ background: c.color }"></span>
        <span v-else class="line" :style="c.edge && c.key === '组成' ? { borderColor: c.color } : {}"></span>
        {{ c.label }}
      </span>
      <span id="graph-stats" style="color: var(--gray)">{{ statsText }}</span>
      <button id="reheat-btn" title="重新布局并复位视图">↻ 重新布局</button>
    </div>

    <div class="search-box">
      <div class="row">
        <input v-model="herbInput" placeholder="离线检索药材档案，如：枸杞子 / 栝楼皮（别名）/ 瓜蒌皮"
               list="herb-suggest" @keydown="onSearchKey">
        <datalist id="herb-suggest">
          <option v-for="h in herbSuggest" :key="h" :value="h"></option>
        </datalist>
        <button @click="showProfile(herbInput)">查询</button>
      </div>
      <div class="search-result" v-if="profileVisible">{{ profileText }}</div>
    </div>
  </div>
</template>
