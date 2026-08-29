/* 多会话管理（2026-08-29）：
   会话 = 服务端 client_id（sessions.json 按 id 落盘，后端 /chat /upload 天然按 id 隔离上下文）
   本地索引 herb_agent_sessions（[{id,title,updated}]）+ 当前会话 herb_agent_current
   每会话显示层：herb_agent_units_<id> / herb_agent_stats_<id>
   旧单会话键（herb_agent_units / herb_agent_stats / herb_agent_client_id）首次启动一次性迁移 */

export interface SessionMeta {
  id: string
  title: string
  updated: number // ms 时间戳（服务端 last_active 是秒，合并时 ×1000）
}

export interface ServerSession {
  id: string
  title: string
  updated: number
}

const INDEX_KEY = 'herb_agent_sessions'
const CUR_KEY = 'herb_agent_current'
const LEGACY_UNITS = 'herb_agent_units'
const LEGACY_STATS = 'herb_agent_stats'
const LEGACY_CLIENT = 'herb_agent_client_id'

export const MAX_LOCAL_SESSIONS = 20

export function unitsKey(id: string): string {
  return 'herb_agent_units_' + id
}
export function statsKey(id: string): string {
  return 'herb_agent_stats_' + id
}

export function newSessionId(): string {
  return (
    (crypto.randomUUID && crypto.randomUUID()) ||
    'c' + Date.now() + Math.random().toString(16).slice(2)
  )
}

function readIndex(): SessionMeta[] {
  try {
    const arr = JSON.parse(localStorage.getItem(INDEX_KEY) || '[]')
    if (!Array.isArray(arr)) return []
    return arr
      .filter((s) => s && typeof s.id === 'string' && typeof s.updated === 'number')
      .map((s) => ({
        id: s.id,
        title: typeof s.title === 'string' && s.title ? s.title : '新会话',
        updated: s.updated
      }))
  } catch {
    return []
  }
}

export function writeIndex(list: SessionMeta[]): void {
  try {
    localStorage.setItem(INDEX_KEY, JSON.stringify(list))
  } catch {
    /* 隐私模式/QuotaExceeded 静默 */
  }
}

// 旧单会话数据迁移：并入一个会话（id 沿用旧 client_id），只做一次
function migrateLegacy(): void {
  try {
    if (localStorage.getItem(INDEX_KEY)) return
    const legacyUnits = localStorage.getItem(LEGACY_UNITS)
    const legacyStats = localStorage.getItem(LEGACY_STATS)
    if (!legacyUnits && !legacyStats) return
    const id = localStorage.getItem(LEGACY_CLIENT) || newSessionId()
    if (legacyUnits) localStorage.setItem(unitsKey(id), legacyUnits)
    if (legacyStats) localStorage.setItem(statsKey(id), legacyStats)
    localStorage.setItem(CUR_KEY, id)
    localStorage.removeItem(LEGACY_UNITS)
    localStorage.removeItem(LEGACY_STATS)
  } catch {
    /* 静默 */
  }
}

// 启动初始化：迁移旧数据 → 读索引 → 校验当前会话 → 空索引时建默认会话
export function initSessions(): { index: SessionMeta[]; current: string } {
  migrateLegacy()
  const index = readIndex()
  let current = localStorage.getItem(CUR_KEY) || ''
  if (!index.some((s) => s.id === current)) {
    // 当前会话丢失（索引被清理/损坏）→ 回退最近一个
    current = index.length ? [...index].sort((a, b) => b.updated - a.updated)[0].id : ''
  }
  if (!current) {
    current = newSessionId()
    index.push({ id: current, title: '新会话', updated: Date.now() })
    writeIndex(index)
    localStorage.setItem(CUR_KEY, current)
  }
  return { index, current }
}

export function setCurrent(id: string): void {
  try {
    localStorage.setItem(CUR_KEY, id)
  } catch {
    /* 静默 */
  }
}

export function addSession(index: SessionMeta[]): SessionMeta {
  const meta: SessionMeta = { id: newSessionId(), title: '新会话', updated: Date.now() }
  index.push(meta)
  writeIndex(index)
  return meta
}

export function removeSession(index: SessionMeta[], id: string): void {
  const i = index.findIndex((s) => s.id === id)
  if (i >= 0) index.splice(i, 1)
  writeIndex(index)
  try {
    localStorage.removeItem(unitsKey(id))
    localStorage.removeItem(statsKey(id))
  } catch {
    /* 静默 */
  }
}

// 更新索引条目（标题/最近活动）并落盘；title 为空串时保留原标题
export function touchSession(index: SessionMeta[], id: string, title: string): void {
  const s = index.find((x) => x.id === id)
  if (!s) return
  if (title) s.title = title
  s.updated = Date.now()
  writeIndex(index)
}

// 合并服务端会话列表（sessions.json 恢复的、本机索引里没有的会话补进来；updated 秒→毫秒）
export function mergeServerSessions(index: SessionMeta[], server: ServerSession[]): void {
  for (const s of server) {
    if (s && typeof s.id === 'string' && !index.some((x) => x.id === s.id)) {
      index.push({
        id: s.id,
        title: typeof s.title === 'string' && s.title ? s.title : '新会话',
        updated: (s.updated || 0) * 1000
      })
    }
  }
  index.sort((a, b) => b.updated - a.updated)
  if (index.length > MAX_LOCAL_SESSIONS) index.length = MAX_LOCAL_SESSIONS
  writeIndex(index)
}
