<script setup lang="ts">
/* 上下文统计状态栏：会话级累计指标（类 Claude Code status line 的对话栏版）
   数据来自后端 SSE stats 事件（每轮/每步实时推送，跨对话轮次累积） */
export interface SessionStats {
  turns: number
  steps: number
  llm_time: number
  tool_time: number
  tokens_in: number
  tokens_out: number
  cache_hit: number
  cache_miss: number
}

const props = defineProps<{ stats: SessionStats | null }>()

function fmtDur(s: number): string {
  if (!isFinite(s) || s < 0) return '—'
  // 工具调用量级两极：图查询是微秒级（check_compatibility ~0.02ms 实测）、
  // 向量检索是秒级（retrieve_doc 首调 5.6s/热调 12ms 实测）→ 三档显示，亚毫秒显示 <1ms 而非 0
  if (s < 0.001) return s === 0 ? '0ms' : '<1ms'
  if (s < 1) return Math.round(s * 1000) + 'ms'
  if (s < 60) return s.toFixed(1) + 's'
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  if (m < 60) return `${m}m${String(sec).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`
}

function fmtTok(n: number): string {
  if (!isFinite(n) || n < 0) return '—'
  if (n < 1000) return String(Math.round(n))
  if (n < 1e6) return (n / 1000).toFixed(1) + 'K'
  return (n / 1e6).toFixed(1) + 'M'
}

function fmtPct(hit: number, miss: number): string {
  const tot = hit + miss
  if (tot <= 0) return '—'
  return Math.round((hit / tot) * 100) + '%'
}

function fmtAvgResp(): string {
  if (!props.stats || props.stats.turns <= 0) return '—'
  return fmtDur(props.stats.llm_time / props.stats.turns)
}

function fmtTps(): string {
  if (!props.stats || props.stats.llm_time <= 0) return '—'
  return String(Math.round(props.stats.tokens_out / props.stats.llm_time))
}

function fmtTurns(): string {
  return props.stats ? String(props.stats.turns) : '—'
}

function fmtSteps(): string {
  return props.stats ? String(props.stats.steps) : '—'
}

function fmtLlmTime(): string {
  return props.stats ? fmtDur(props.stats.llm_time) : '—'
}

function fmtToolTime(): string {
  return props.stats ? fmtDur(props.stats.tool_time) : '—'
}

function fmtTokensIn(): string {
  return props.stats ? fmtTok(props.stats.tokens_in) : '—'
}

function fmtTokensOut(): string {
  return props.stats ? fmtTok(props.stats.tokens_out) : '—'
}

function fmtCache(): string {
  return props.stats ? fmtPct(props.stats.cache_hit, props.stats.cache_miss) : '—'
}
</script>

<template>
  <div class="ctx-stats" title="会话上下文统计（跨对话轮次累计；新建/切换会话时归零，历史会话各自保留）">
    <span class="cs-seg">
      <b>{{ fmtTurns() }}</b> 轮 · <b>{{ fmtSteps() }}</b> 步
    </span>
    <span class="cs-sep">|</span>
    <span class="cs-seg">LLM <b>{{ fmtLlmTime() }}</b> · 工具 <b>{{ fmtToolTime() }}</b></span>
    <span class="cs-sep">|</span>
    <span class="cs-seg">平均响应 <b>{{ fmtAvgResp() }}</b> · <b>{{ fmtTps() }}</b> tok/s</span>
    <span class="cs-sep">|</span>
    <span class="cs-seg">缓存命中 <b>{{ fmtCache() }}</b></span>
    <span class="cs-sep">|</span>
    <span class="cs-seg">输入 <b>{{ fmtTokensIn() }}</b> tok · 输出 <b>{{ fmtTokensOut() }}</b> tok</span>
  </div>
</template>

<style scoped>
.ctx-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 10px;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--gray);
  background: var(--green-soft);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin: 8px 0 10px;
}
.ctx-stats b {
  font-weight: 600;
  color: var(--green-dark);
}
.cs-sep {
  color: var(--border);
}
</style>
