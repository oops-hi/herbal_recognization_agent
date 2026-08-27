/*
 * static/js/graph.js
 * 知识图谱页 v2：D3 手写力导向交互图（Obsidian 双向链接视图风格，浅色画布融入米色主题）。
 *
 * - 数据源：GET /api/graph（后端 kg.query.graph_dataset()，84 节点 + 84 边）
 * - 交互：拖拽 / 缩放平移 / 悬停邻接高亮 / 点击节点聚焦 + 档案联动 / 图例筛选 / 复位
 * - 降级：d3 缺失或 /api/graph 失败 → 保留静态 PNG + 提示条（验收步骤 7「离线渲染」语义）
 * - 零外部依赖：vendor 本地 d3.v7.min.js，页面 0 外部请求
 */
(function () {
  "use strict";

  var area = document.getElementById("graph-area");
  var staticImg = document.getElementById("graph-static");
  var svgEl = document.getElementById("graph-svg");
  var fallback = document.getElementById("graph-fallback");
  var tooltip = document.getElementById("graph-tooltip");
  var resultBox = document.getElementById("herb-result");

  var CATEGORY_CN = { herb: "带档案药材", herb_minor: "次要节点", formula: "经典方剂" };

  // ---------- 降级分支（断网 / 后端挂 / vendor 缺失） ----------
  function degrade(reason) {
    var loading = document.getElementById("graph-loading");
    if (loading) loading.style.display = "none";
    staticImg.style.display = "block"; // 降级显示静态 PNG（离线渲染语义）
    if (fallback) fallback.hidden = false;
    console.warn("[graph] 交互式图谱不可用，已降级为静态图：", reason);
  }

  // ---------- 档案渲染（搜索框与节点点击共用同一函数） ----------
  function herbProfile(j) {
    var p = j.profile;
    return (
      "【" + j.name_cn + "】" + (j.latin || "") + "\n" +
      "性味：" + p["性味"] + "\n" +
      "归经：" + p["归经"].join("、") + "\n" +
      "功效：" + p["功效"] + "\n" +
      "主治：" + p["主治"] + "\n" +
      "用量：" + p["用量"] + "\n" +
      "毒性：" + p["毒性"] + "\n" +
      "禁忌：" + p["禁忌"] + "\n" +
      "出处：" + (j.source || "")
    );
  }

  function formulaProfile(j) {
    var f = j.formula || {};
    var members = (f["组成"] || []).map(function (m) {
      return m["role"] + "—" + m["herb"];
    }).join("  ");
    return (
      "【方剂】" + j.name_cn + "\n" +
      "主治：" + (f["主治"] || "") + "\n" +
      "组成：" + members + "\n" +
      "出处：" + (f["出处"] || j.source || "")
    );
  }

  function showProfile(name) {
    if (!resultBox) return;
    name = String(name || "").trim();
    if (!name) return;
    resultBox.style.display = "block";
    resultBox.textContent = "查询中…";
    fetch("/api/herb/" + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.found) {
          resultBox.innerHTML = "<b style='color:var(--red);'>知识库未收录「" + name + "」</b>" +
            "<br>该药可能未在 20 类主药档案内，或可尝试别名（如 栝楼皮 → 瓜蒌皮）。";
          return;
        }
        if (j.category === "herb") { resultBox.textContent = herbProfile(j); return; }
        if (j.category === "formula") { resultBox.textContent = formulaProfile(j); return; }
        resultBox.textContent = "「" + j.name_cn + "」为次要节点（只存名字），可检索其参与的方剂 / 禁忌网络。";
      })
      .catch(function () {
        resultBox.textContent = "查询失败（本页为离线静态渲染，档案查询需要本地服务运行）。";
      });
  }

  // ---------- 图谱渲染 ----------
  function renderForce(data) {
    var loading = document.getElementById("graph-loading");
    if (loading) loading.style.display = "none";
    staticImg.style.display = "none"; // 幂等保险
    svgEl.style.display = "block";    // 幂等保险（CSS 默认已是 block）

    var nodes = data.nodes;
    var edges = data.edges;
    var w = area.clientWidth || 900;
    var h = area.clientHeight || 560;

    svgEl.setAttribute("width", w);
    svgEl.setAttribute("height", h);
    var svg = d3.select(svgEl);

    // 背景层必须先于节点层 append：SVG 后添加的元素在最上层，
    // 透明 fill 的 rect 会拦截全部 pointer 事件，导致节点拖不动/点不到
    svg.append("rect")
      .attr("class", "graph-bg")
      .attr("width", w)
      .attr("height", h)
      .attr("fill", "transparent");
    var root = svg.append("g");

    var maxDeg = d3.max(nodes, function (d) { return d.degree; }) || 1;

    function nodeR(d) {
      var r = 5 + 7 * Math.sqrt((d.degree || 0) / maxDeg);
      if (d.category === "herb_minor") r *= 0.7;
      return r;
    }
    function formulaS(d) { return 12 + 9 * Math.sqrt((d.degree || 0) / maxDeg); }
    function collideR(d) {
      return d.category === "formula" ? formulaS(d) * 1.5 : nodeR(d) + 6;
    }
    function nodeCategoryOf(x) { return typeof x === "string" ? nodeCategory.get(x) : x.category; }

    var nodeCategory = new Map(nodes.map(function (n) { return [n.id, n.category]; }));

    // 环形初始摆位，避免全部从原点爆开
    var R0 = 170;
    nodes.forEach(function (n, i) {
      var a = (i / nodes.length) * 2 * Math.PI;
      n.x = w / 2 + R0 * Math.cos(a);
      n.y = h / 2 + R0 * Math.sin(a);
    });

    // ---------- 邻接表（无向；悬停高亮用） ----------
    var adj = new Map(nodes.map(function (n) { return [n.id, new Set()]; }));
    edges.forEach(function (e) {
      var a = e.source, b = e.target;
      if (adj.has(a)) adj.get(a).add(b);
      if (adj.has(b)) adj.get(b).add(a);
    });

    // ---------- 边 ----------
    var linkSel = root.append("g").selectAll("path")
      .data(edges).join("path")
      .attr("class", function (d) { return "link " + (d.type === "禁忌" ? "link-taboo" : "link-composition"); })
      .attr("fill", "none");

    // ---------- 力模拟 ----------
    var sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(edges).id(function (d) { return d.id; })
        .distance(function (d) { return d.type === "禁忌" ? 95 : 62; })
        .strength(function (d) { return d.type === "禁忌" ? 0.25 : 0.55; }))
      .force("charge", d3.forceManyBody().strength(-170))
      .force("collide", d3.forceCollide().radius(collideR))
      .force("x", d3.forceX(w / 2).strength(0.035))
      .force("y", d3.forceY(h / 2).strength(0.035))
      .alpha(1).alphaDecay(0.0225)
      .on("tick", ticked);

    function linkPath(d) {
      if (d.type === "禁忌") {
        // 二次贝塞尔：中点 + 法向偏移，与组成直线区分
        var sx = d.source.x, sy = d.source.y, tx = d.target.x, ty = d.target.y;
        var mx = (sx + tx) / 2, my = (sy + ty) / 2;
        var dx = tx - sx, dy = ty - sy;
        var len = Math.sqrt(dx * dx + dy * dy) || 1;
        var off = 20;
        return "M" + sx + "," + sy + "Q" + (mx + (-dy / len) * off) + "," + (my + (dx / len) * off) + " " + tx + "," + ty;
      }
      return "M" + d.source.x + "," + d.source.y + "L" + d.target.x + "," + d.target.y;
    }

    function ticked() {
      linkSel.attr("d", linkPath);
      nodeSel.attr("transform", function (d) { return "translate(" + d.x + "," + d.y + ")"; });
    }

    // ---------- 节点（circle / formula 菱形 rect+rotate45） ----------
    var nodeSel = root.append("g").selectAll("g")
      .data(nodes).join("g")
      .attr("class", function (d) { return "node node-" + d.category; });

    nodeSel.each(function (d) {
      var g = d3.select(this);
      if (d.category === "formula") {
        var s = formulaS(d);
        g.append("rect")
          .attr("x", -s).attr("y", -s)
          .attr("width", 2 * s).attr("height", 2 * s)
          .attr("rx", 3)
          .attr("transform", "rotate(45)")
          .attr("fill", "#42a5f5");
      } else {
        g.append("circle")
          .attr("r", nodeR(d))
          .attr("fill", d.category === "herb" ? "#4caf50" : "#bdbdbd");
      }
      g.append("text")
        .attr("class", "node-label")
        .attr("dy", d.category === "formula" ? formulaS(d) * 1.6 + 6 : nodeR(d) + 13)
        .text(d.category === "formula" ? d.name_cn : d.id);
    });

    // ---------- 悬停高亮 + 聚焦 ----------
    function clearHighlight() {
      nodeSel.classed("dim", false).classed("hl", false);
      linkSel.classed("dim", false).classed("hl", false);
    }
    function highlight(d) {
      clearHighlight();
      var nb = adj.get(d.id) || new Set();
      nodeSel.classed("hl", function (x) { return x.id === d.id || nb.has(x.id); })
        .classed("dim", function (x) { return !(x.id === d.id || nb.has(x.id)); });
      linkSel.classed("hl", function (e) {
        return e.source.id === d.id || e.target.id === d.id || nb.has(e.source.id) || nb.has(e.target.id);
      }).classed("dim", function (e) {
        return !(e.source.id === d.id || e.target.id === d.id || nb.has(e.source.id) || nb.has(e.target.id));
      });
    }

    function clearPinned() { nodeSel.classed("pinned", false); }

    function focusNode(d) {
      nodeSel.classed("pinned", function (x) { return x.id === d.id; });
      var t = d3.zoomIdentity.translate(w / 2 - d.x * 1.6, h / 2 - d.y * 1.6).scale(1.6);
      svg.transition().duration(450).call(zoom.transform, t);
    }

    // ---------- tooltip ----------
    function showTooltip(html) {
      if (!tooltip) return;
      tooltip.innerHTML = html;
      tooltip.hidden = false;
    }
    function positionTooltip(e) {
      if (!tooltip || tooltip.hidden) return;
      var rect = area.getBoundingClientRect();
      var x = e.clientX - rect.left + 14;
      var y = e.clientY - rect.top + 14;
      tooltip.style.left = Math.min(x, rect.width - tooltip.offsetWidth - 8) + "px";
      tooltip.style.top = Math.min(y, rect.height - tooltip.offsetHeight - 8) + "px";
    }
    function hideTooltip() { if (tooltip) tooltip.hidden = true; }

    function showNodeTip(e, d) {
      var aliases = d.aliases && d.aliases.length
        ? "别名：" + d.aliases.join("、") + "<br>" : "";
      showTooltip(
        "<b>" + d.name_cn + "</b> <span class='tt-tag'>" + (CATEGORY_CN[d.category] || d.category) + "</span><br>" +
        "度数：与 " + d.degree + " 个节点相连<br>" +
        aliases +
        "<span class='tt-hint'>点击查看档案 · 拖拽移动</span>"
      );
      positionTooltip(e);
    }

    function showLinkTip(e, d) {
      if (d.type === "禁忌") {
        // 双源展示：歌诀 + 药典（验收步骤 4「十八反双源」卖点）
        var pharma = d.pharmacopoeia === "认定"
          ? "《中国药典》2020 年版【注意】项认定：不宜同用"
          : "《中国药典》未将该对列入【注意】项（仅见歌诀记载）";
        var note = d.note ? "<br>说明：" + d.note : "";
        showTooltip(
          "<b>" + d.source.name_cn + " × " + d.target.name_cn + "</b>（配伍禁忌）<br>" +
          "歌诀依据：" + (d.verse || "") + "<br>" +
          "药典依据：" + pharma + note
        );
      } else {
        showTooltip(
          d.source.name_cn + " → " + d.target.name_cn +
          "（方剂组成，本品为「" + d.role + "」）"
        );
      }
      positionTooltip(e);
    }

    // ---------- 事件：hover / click / drag / zoom ----------
    nodeSel
      .on("mouseover", function (e, d) { highlight(d); showNodeTip(e, d); })
      .on("mousemove", positionTooltip)
      .on("mouseout", function () { clearHighlight(); hideTooltip(); })
      .on("click", function (e, d) {
        e.stopPropagation();  // 防止冒泡到 svg 背景的「取消聚焦」
        focusNode(d);
        showProfile(d.id);
      });

    linkSel
      .on("mouseover", function (e, d) { showLinkTip(e, d); })
      .on("mousemove", positionTooltip)
      .on("mouseout", hideTooltip);

    var zoom = d3.zoom()
      .scaleExtent([0.3, 4])
      .on("start", hideTooltip)  // 平移/缩放时收起 tooltip，避免位置错位
      .on("zoom", function (e) { root.attr("transform", e.transform); });
    svg.call(zoom);

    // clickDistance(4)：位移 >4px 视为拖拽，松手不再误触 click
    var drag = d3.drag()
      .clickDistance(4)
      .on("start", function (e, d) {
        if (!e.active) sim.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on("drag", function (e, d) { d.fx = e.x; d.fy = e.y; })
      .on("end", function (e, d) {
        if (!e.active) sim.alphaTarget(0);
        d.fx = null; d.fy = null;  // 松手归位，让力场接管
      });
    nodeSel.call(drag);

    // 点击画布空白 → 取消聚焦
    svg.on("click", function (e) {
      if (e.target.classList && e.target.classList.contains("graph-bg")) clearPinned();
    });

    // ---------- 图例筛选（只切显隐，不动 simulation） ----------
    function chipOff(v) {
      var c = document.querySelector('.legend-chip[data-filter="' + v + '"]');
      return c && c.classList.contains("off");
    }
    function updateVisibility() {
      var nodeOn = {
        herb: !chipOff("herb"),
        herb_minor: !chipOff("herb_minor"),
        formula: !chipOff("formula")
      };
      var edgeOn = { 组成: !chipOff("组成"), 禁忌: !chipOff("禁忌") };
      nodeSel.style("display", function (d) { return nodeOn[d.category] ? null : "none"; });
      // 某条边的任一端点被隐藏 → 整边隐藏
      linkSel.style("display", function (d) {
        return (edgeOn[d.type] && nodeOn[nodeCategoryOf(d.source)] && nodeOn[nodeCategoryOf(d.target)])
          ? null : "none";
      });
    }
    d3.selectAll(".legend-chip[data-filter]").on("click", function () {
      d3.select(this).classed("off", !d3.select(this).classed("off"));
      updateVisibility();
    });

    // ---------- 复位按钮 ----------
    var reheat = document.getElementById("reheat-btn");
    if (reheat) reheat.addEventListener("click", function () {
      clearPinned();
      hideTooltip();
      sim.alpha(1).restart();
      svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity);
    });

    // ---------- 窗口尺寸变化 ----------
    window.addEventListener("resize", function () {
      var nw = area.clientWidth || w;
      var nh = area.clientHeight || h;
      svgEl.setAttribute("width", nw);
      svgEl.setAttribute("height", nh);
      svg.select(".graph-bg").attr("width", nw).attr("height", nh);
      sim.force("x", d3.forceX(nw / 2).strength(0.035));
      sim.force("y", d3.forceY(nh / 2).strength(0.035));
      sim.alpha(0.3).restart();
    });
  }

  // ---------- 启动 ----------
  function initGraph() {
    if (typeof d3 === "undefined") { degrade("vendor d3 未加载"); return; }
    if (!area || !svgEl) { degrade("缺少 #graph-area / #graph-svg"); return; }
    fetch("/api/graph")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) {
          throw new Error("载荷格式错误");
        }
        renderForce(data);
      })
      .catch(function (e) { degrade(e && e.message); });
  }

  // 图谱统计（v1 内联脚本平移；断网时静默，与现行为一致）
  fetch("/api/health").then(function (r) { return r.json(); }).then(function (j) {
    var s = document.getElementById("graph-stats");
    if (s) s.textContent = j.graph || "";
  }).catch(function () {});

  window.GraphPage = { showProfile: showProfile };
  initGraph();
})();
