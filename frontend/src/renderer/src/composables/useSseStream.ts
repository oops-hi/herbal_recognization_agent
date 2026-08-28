/* ============================================================
   SSE 流式对话：fetch + ReadableStream（POST 语义，EventSource 用不了）
   后端帧格式（data-only，无 event 行）：data: {json}\n\n
   —— main.js L233-251 原逻辑 ES module 化
   ============================================================ */

/** 按 "\n\n" 切帧、取 "data: " 前缀并 JSON.parse；坏帧静默忽略 */
export function parseSseFrames(buf: string, onEvent: (ev: unknown) => void): string {
  let rest = buf
  let idx: number
  while ((idx = rest.indexOf('\n\n')) >= 0) {
    const chunk = rest.slice(0, idx)
    rest = rest.slice(idx + 2)
    for (const line of chunk.split('\n')) {
      if (line.startsWith('data: ')) {
        try {
          onEvent(JSON.parse(line.slice(6)))
        } catch (e) {
          /* 忽略坏帧 */
        }
      }
    }
  }
  return rest
}

/** POST + 流式读取；onEvent 逐帧回调；返回 Promise（结束时 resolve，异常 reject） */
export async function postAndStream(
  url: string,
  body: unknown,
  onEvent: (ev: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal
  })
  const ct = resp.headers.get('content-type')
  if (!ct || !ct.includes('text/event-stream')) {
    const j = await resp.json()
    throw new Error(j.error || '请求失败（HTTP ' + resp.status + '）')
  }
  const reader = resp.body!.getReader()
  const dec = new TextDecoder('utf-8')
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf = parseSseFrames(buf + dec.decode(value, { stream: true }), onEvent)
  }
}
