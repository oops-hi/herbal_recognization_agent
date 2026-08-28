/*
 * lib/graphRenderer.ts
 * 知识图谱 v2：D3 手写力导向交互图（Obsidian 双向链接视图风格，浅色画布融入米色主题）。
 * —— static/js/graph.js 1:1 移植：IIFE → ES module，DOM 依赖参数化，新增 destroy() 清理钩子。
 *
 * 数据源：GET /api/graph（kg.query.graph_dataset()，84 节点 + 84 边）
 * 交互：拖拽 / 缩放平移 / 悬停邻接高亮 / 点击节点聚焦 + 档案联动 / 图例筛选 / 复位
 * 断网可用：d3 由 npm 打包进 bundle，页面 0 外部请求
 */
import * as d3 from 'd3'

export interface GraphNode {
  id: string
  name_cn: string
  category: string // herb | herb_minor | formula
  degree: number
  aliases?: string[]
  /* d3 力模拟运行时附加的位置字段 */
  x?: number
  y?: number
  fx?: number | null
  fy?: number | null
}
export interface GraphEdge {
  source: string
  target: string
  type: string // 禁忌 | 组成
  role?: string
  verse?: string
  pharmacopoeia?: string
  note?: string
  source_?: { name_cn: string }
  target_?: { name_cn: string }
}
export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

const CATEGORY_CN: Record<string, string> = { herb: '带档案药材', herb_minor: '次要节点', formula: '经典方剂' }

export interface RenderOpts {
  area: HTMLElement // 尺寸来源（#graph-area）
  svgEl: SVGSVGElement
  tooltip: HTMLElement
  onNodeClick: (id: string) => void // 点击节点 → 档案联动（由组件处理 fetch 与渲染）
  legendChips: () => { node: Record<string, boolean>; edge: Record<string, boolean> } // 当前图例显隐
  reheatBtn: () => HTMLElement | null
}

export interface RenderHandle {
  destroy: () => void
  /** 图例状态变化后由组件调用，只切显隐不动 simulation */
  refreshVisibility: () => void
}

export function renderForce(data: GraphData, opts: RenderOpts): RenderHandle {
  const { area, svgEl, tooltip } = opts
  const nodes = data.nodes
  const edges = data.edges
  const w = area.clientWidth || 900
  const h = area.clientHeight || 560

  svgEl.setAttribute('width', String(w))
  svgEl.setAttribute('height', String(h))
  const svg = d3.select(svgEl)

  // 背景层必须先于节点层 append：SVG 后添加的元素在最上层，
  // 透明 fill 的 rect 会拦截全部 pointer 事件，导致节点拖不动/点不到
  svg.append('rect')
    .attr('class', 'graph-bg')
    .attr('width', w)
    .attr('height', h)
    .attr('fill', 'transparent')
  const root = svg.append('g')

  const maxDeg = d3.max(nodes, (d) => d.degree) || 1

  function nodeR(d: GraphNode): number {
    let r = 5 + 7 * Math.sqrt((d.degree || 0) / maxDeg)
    if (d.category === 'herb_minor') r *= 0.7
    return r
  }
  function formulaS(d: GraphNode): number {
    return 12 + 9 * Math.sqrt((d.degree || 0) / maxDeg)
  }
  function collideR(d: GraphNode): number {
    return d.category === 'formula' ? formulaS(d) * 1.5 : nodeR(d) + 6
  }
  function nodeCategoryOf(x: string | GraphNode): string | undefined {
    return typeof x === 'string' ? nodeCategory.get(x) : x.category
  }

  const nodeCategory = new Map(nodes.map((n) => [n.id, n.category]))

  // 环形初始摆位，避免全部从原点爆开
  const R0 = 170
  nodes.forEach((n, i) => {
    const a = (i / nodes.length) * 2 * Math.PI
    n.x = w / 2 + R0 * Math.cos(a)
    n.y = h / 2 + R0 * Math.sin(a)
  })

  // ---------- 邻接表（无向；悬停高亮用） ----------
  const adj = new Map(nodes.map((n) => [n.id, new Set<string>()]))
  edges.forEach((e) => {
    const a = e.source as string
    const b = e.target as string
    if (adj.has(a)) adj.get(a)!.add(b)
    if (adj.has(b)) adj.get(b)!.add(a)
  })

  // ---------- 边 ----------
  const linkSel = root
    .append('g')
    .selectAll('path')
    .data(edges)
    .join('path')
    .attr('class', (d) => 'link ' + (d.type === '禁忌' ? 'link-taboo' : 'link-composition'))
    .attr('fill', 'none')

  // ---------- 力模拟 ----------
  const sim = d3
    .forceSimulation(nodes as never[])
    .force(
      'link',
      d3
        .forceLink(edges as never[])
        .id((d: GraphNode) => d.id)
        .distance((d: GraphEdge) => (d.type === '禁忌' ? 95 : 62))
        .strength((d: GraphEdge) => (d.type === '禁忌' ? 0.25 : 0.55))
    )
    .force('charge', d3.forceManyBody().strength(-170))
    .force('collide', d3.forceCollide().radius(collideR))
    .force('x', d3.forceX(w / 2).strength(0.035))
    .force('y', d3.forceY(h / 2).strength(0.035))
    .alpha(1)
    .alphaDecay(0.0225)
    .on('tick', ticked)

  // d3 forceLink 会在运行时把边两端从 id 字符串替换为节点对象引用 —— 类型用宽接口描述
  //（graph.js 原为 JS 无类型，此处 1:1 移植，D3 运行时行为不变）
  type LinkedEdge = GraphEdge & { source: GraphNode; target: GraphNode }

  function linkPath(d: LinkedEdge): string {
    if (d.type === '禁忌') {
      // 二次贝塞尔：中点 + 法向偏移，与组成直线区分
      const sx = d.source.x!
      const sy = d.source.y!
      const tx = d.target.x!
      const ty = d.target.y!
      const mx = (sx + tx) / 2
      const my = (sy + ty) / 2
      const dx = tx - sx
      const dy = ty - sy
      const len = Math.sqrt(dx * dx + dy * dy) || 1
      const off = 20
      return (
        'M' + sx + ',' + sy + 'Q' + (mx + (-dy / len) * off) + ',' + (my + (dx / len) * off) + ' ' + tx + ',' + ty
      )
    }
    return 'M' + d.source.x + ',' + d.source.y + 'L' + d.target.x + ',' + d.target.y
  }

  function ticked(): void {
    linkSel.attr('d', linkPath as never)
    nodeSel.attr('transform', (d: GraphNode) => 'translate(' + d.x + ',' + d.y + ')')
  }

  // ---------- 节点（circle / formula 菱形 rect+rotate45） ----------
  const nodeSel = root
    .append('g')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .attr('class', (d) => 'node node-' + d.category)

  nodeSel.each(function (this: SVGGElement, d: GraphNode) {
    const g = d3.select(this)
    if (d.category === 'formula') {
      const s = formulaS(d)
      g.append('rect')
        .attr('x', -s)
        .attr('y', -s)
        .attr('width', 2 * s)
        .attr('height', 2 * s)
        .attr('rx', 3)
        .attr('transform', 'rotate(45)')
        .attr('fill', '#42a5f5')
    } else {
      g.append('circle')
        .attr('r', nodeR(d))
        .attr('fill', d.category === 'herb' ? '#4caf50' : '#bdbdbd')
    }
    g.append('text')
      .attr('class', 'node-label')
      .attr('dy', d.category === 'formula' ? formulaS(d) * 1.6 + 6 : nodeR(d) + 13)
      .text(d.category === 'formula' ? d.name_cn : d.id)
  })

  // ---------- 悬停高亮 + 聚焦 ----------
  function clearHighlight(): void {
    nodeSel.classed('dim', false).classed('hl', false)
    linkSel.classed('dim', false).classed('hl', false)
  }
  function highlight(d: GraphNode): void {
    clearHighlight()
    const nb = adj.get(d.id) || new Set()
    nodeSel
      .classed('hl', (x: GraphNode) => x.id === d.id || nb.has(x.id))
      .classed('dim', (x: GraphNode) => !(x.id === d.id || nb.has(x.id)))
    linkSel
      .classed('hl', (e: LinkedEdge) => e.source.id === d.id || e.target.id === d.id || nb.has(e.source.id) || nb.has(e.target.id))
      .classed('dim', (e: LinkedEdge) => !(e.source.id === d.id || e.target.id === d.id || nb.has(e.source.id) || nb.has(e.target.id)))
  }

  function clearPinned(): void {
    nodeSel.classed('pinned', false)
  }

  function focusNode(d: GraphNode): void {
    nodeSel.classed('pinned', (x: GraphNode) => x.id === d.id)
    const t = d3.zoomIdentity.translate(w / 2 - d.x! * 1.6, h / 2 - d.y! * 1.6).scale(1.6)
    svg.transition().duration(450).call(zoom.transform as never, t)
  }

  // ---------- tooltip ----------
  function showTooltip(html: string): void {
    tooltip.innerHTML = html
    tooltip.hidden = false
  }
  function positionTooltip(e: MouseEvent): void {
    if (tooltip.hidden) return
    const rect = area.getBoundingClientRect()
    const x = e.clientX - rect.left + 14
    const y = e.clientY - rect.top + 14
    tooltip.style.left = Math.min(x, rect.width - tooltip.offsetWidth - 8) + 'px'
    tooltip.style.top = Math.min(y, rect.height - tooltip.offsetHeight - 8) + 'px'
  }
  function hideTooltip(): void {
    tooltip.hidden = true
  }

  function showNodeTip(e: MouseEvent, d: GraphNode): void {
    const aliases = d.aliases && d.aliases.length ? '别名：' + d.aliases.join('、') + '<br>' : ''
    showTooltip(
      '<b>' + d.name_cn + '</b> <span class="tt-tag">' + (CATEGORY_CN[d.category] || d.category) + '</span><br>' +
        '度数：与 ' + d.degree + ' 个节点相连<br>' +
        aliases +
        '<span class="tt-hint">点击查看档案 · 拖拽移动</span>'
    )
    positionTooltip(e)
  }

  function showLinkTip(e: MouseEvent, d: LinkedEdge): void {
    if (d.type === '禁忌') {
      // 双源展示：歌诀 + 药典（验收步骤 4「十八反双源」卖点）
      const pharma =
        d.pharmacopoeia === '认定'
          ? '《中国药典》2020 年版【注意】项认定：不宜同用'
          : '《中国药典》未将该对列入【注意】项（仅见歌诀记载）'
      const note = d.note ? '<br>说明：' + d.note : ''
      showTooltip(
        '<b>' + d.source.name_cn + ' × ' + d.target.name_cn + '</b>（配伍禁忌）<br>' +
          '歌诀依据：' + (d.verse || '') + '<br>' +
          '药典依据：' + pharma + note
      )
    } else {
      showTooltip(d.source.name_cn + ' → ' + d.target.name_cn + '（方剂组成，本品为「' + d.role + '」）')
    }
    positionTooltip(e)
  }

  // ---------- 事件：hover / click / drag / zoom ----------
  nodeSel
    .on('mouseover', (e: MouseEvent, d: GraphNode) => {
      highlight(d)
      showNodeTip(e, d)
    })
    .on('mousemove', positionTooltip)
    .on('mouseout', () => {
      clearHighlight()
      hideTooltip()
    })
    .on('click', (e: MouseEvent, d: GraphNode) => {
      e.stopPropagation() // 防止冒泡到 svg 背景的「取消聚焦」
      focusNode(d)
      opts.onNodeClick(d.id)
    })

  linkSel
    .on('mouseover', (e: MouseEvent, d: LinkedEdge) => showLinkTip(e, d))
    .on('mousemove', positionTooltip)
    .on('mouseout', hideTooltip)

  const zoom = d3
    .zoom()
    .scaleExtent([0.3, 4])
    .on('start', hideTooltip) // 平移/缩放时收起 tooltip，避免位置错位
    .on('zoom', (e) => root.attr('transform', e.transform))
  svg.call(zoom as never)

  // clickDistance(4)：位移 >4px 视为拖拽，松手不再误触 click
  const drag = d3
    .drag()
    .clickDistance(4)
    .on('start', (e, d: GraphNode) => {
      if (!e.active) sim.alphaTarget(0.3).restart()
      d.fx = d.x
      d.fy = d.y
    })
    .on('drag', (e, d: GraphNode) => {
      d.fx = e.x
      d.fy = e.y
    })
    .on('end', (e, d: GraphNode) => {
      if (!e.active) sim.alphaTarget(0)
      d.fx = null
      d.fy = null // 松手归位，让力场接管
    })
  nodeSel.call(drag as never)

  // 点击画布空白 → 取消聚焦
  svg.on('click', (e: MouseEvent) => {
    const t = e.target as Element
    if (t.classList && t.classList.contains('graph-bg')) clearPinned()
  })

  // ---------- 图例筛选（只切显隐，不动 simulation） ----------
  function updateVisibility(): void {
    const on = opts.legendChips()
    nodeSel.style('display', (d: GraphNode) => (on.node[d.category] ? null : 'none'))
    // 某条边的任一端点被隐藏 → 整边隐藏
    linkSel.style('display', (d) => {
      return on.edge[d.type] && on.node[nodeCategoryOf(d.source) || ''] && on.node[nodeCategoryOf(d.target) || '']
        ? null
        : 'none'
    })
  }
  updateVisibility() // 初始按图例状态渲染

  // ---------- 复位按钮 ----------
  const reheat = opts.reheatBtn()
  const onReheat = (): void => {
    clearPinned()
    hideTooltip()
    sim.alpha(1).restart()
    svg.transition().duration(400).call(zoom.transform as never, d3.zoomIdentity)
  }
  if (reheat) reheat.addEventListener('click', onReheat)

  // ---------- 窗口尺寸变化 ----------
  const onResize = (): void => {
    const nw = area.clientWidth || w
    const nh = area.clientHeight || h
    svgEl.setAttribute('width', String(nw))
    svgEl.setAttribute('height', String(nh))
    svg.select('.graph-bg').attr('width', nw).attr('height', nh)
    sim.force('x', d3.forceX(nw / 2).strength(0.035))
    sim.force('y', d3.forceY(nh / 2).strength(0.035))
    sim.alpha(0.3).restart()
  }
  window.addEventListener('resize', onResize)

  // ---------- 清理（组件卸载时调用，防力模拟/监听泄漏） ----------
  return {
    destroy: () => {
      sim.stop()
      window.removeEventListener('resize', onResize)
      if (reheat) reheat.removeEventListener('click', onReheat)
      svg.on('.zoom', null).on('.click', null)
      svg.selectAll('*').remove()
    },
    refreshVisibility: updateVisibility
  }
}
