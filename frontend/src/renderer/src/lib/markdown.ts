/* ============================================================
   迷你 markdown 渲染器（离线可用，无 CDN 依赖 —— 答辩教室断网约束）
   覆盖子集：**加粗** / *斜体* / `行内代码` / ```代码块``` / #标题 /
             - 无序列表 / 1. 有序列表 / > 引用 / 段落
   安全：先 HTML 转义再渲染，原始 HTML 一律无效（防 XSS）
   —— 由 static/js/markdown.js 原样 ES module 化
   ============================================================ */

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// 行内样式：先保护行内代码（其内部的 ** * 不解析），再处理加粗/斜体，最后还原
function inline(s: string): string {
  const codes: string[] = []
  s = s.replace(/`([^`]+)`/g, (_, c: string) => {
    codes.push('<code>' + esc(c) + '</code>')
    return '\x00' + (codes.length - 1) + '\x00'
  })
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  s = s.replace(/\x00(\d+)\x00/g, (_, i: string) => codes[+i])
  return s
}

export function renderMarkdown(md: string): string {
  const out: string[] = []
  const lines = String(md).replace(/\r\n/g, '\n').split('\n')
  let list: { tag: string; items: string[] } | null = null // 连续列表合并成一个 ul/ol
  let codeBuf: string[] | null = null // 代码块缓冲（内容不解析 markdown）

  function flushList(): void {
    if (list) {
      out.push('<' + list.tag + '>' + list.items.join('') + '</' + list.tag + '>')
      list = null
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i]
    const t = ln.trim()

    if (codeBuf !== null) {
      if (t === '```') {
        out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>')
        codeBuf = null
      } else codeBuf.push(esc(ln))
      continue
    }
    if (t.startsWith('```')) {
      flushList()
      codeBuf = []
      continue
    }
    if (!t) {
      flushList()
      continue
    }

    // # 标题（#→h3, ##→h4, ###→h5）
    let m = t.match(/^(#{1,4})\s+(.*)$/)
    if (m) {
      flushList()
      const lv = m[1].length + 2
      out.push('<h' + lv + '>' + inline(esc(m[2])) + '</h' + lv + '>')
      continue
    }
    // - 无序列表
    m = t.match(/^[-*]\s+(.*)$/)
    if (m) {
      if (!list || list.tag !== 'ul') {
        flushList()
        list = { tag: 'ul', items: [] }
      }
      list.items.push('<li>' + inline(esc(m[1])) + '</li>')
      continue
    }
    // 1. 有序列表
    m = t.match(/^\d+[.)]\s+(.*)$/)
    if (m) {
      if (!list || list.tag !== 'ol') {
        flushList()
        list = { tag: 'ol', items: [] }
      }
      list.items.push('<li>' + inline(esc(m[1])) + '</li>')
      continue
    }
    // > 引用
    m = t.match(/^>\s?(.*)$/)
    if (m) {
      flushList()
      out.push('<blockquote>' + inline(esc(m[1])) + '</blockquote>')
      continue
    }

    // 普通段落
    flushList()
    out.push('<p>' + inline(esc(ln)) + '</p>')
  }
  if (codeBuf !== null) out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>')
  flushList()
  return out.join('')
}
