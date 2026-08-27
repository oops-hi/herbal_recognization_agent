"""
kg/viz.py
知识图谱静态可视化 → PNG（matplotlib，离线可用，不依赖任何 CDN）。

- 节点：herb（绿色圆）/ herb_minor（浅灰圆）/ formula（蓝色方框）
- 边：组成（灰色细线，标君臣佐使）/ 禁忌（红色虚线，标十八反/十九畏）
- Windows 中文：Microsoft YaHei / SimHei + unicode_minus=False，否则全方框
- 输出：static/kg_graph.png（Flask /graph 页引用）
"""
import matplotlib

matplotlib.use("Agg")  # 无 GUI 环境也能渲染

import matplotlib.pyplot as plt
import networkx as nx

from config import BASE_DIR
from . import builder

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT_PATH = BASE_DIR / "static" / "kg_graph.png"

NODE_COLORS = {
    "herb": "#4caf50",
    "herb_minor": "#bdbdbd",
    "formula": "#42a5f5",
}


def render(output: str | None = None) -> str:
    """渲染并保存 PNG，返回输出路径。"""
    g = builder.load()
    pos = nx.spring_layout(g, seed=42, k=0.9, iterations=80)

    fig, ax = plt.subplots(figsize=(20, 14))
    ax.set_title(
        "多模态中草药识别智能体 · 知识图谱"
        f"（{g.number_of_nodes()} 节点 / {g.number_of_edges()} 边）",
        fontsize=18, pad=16,
    )
    ax.axis("off")

    # 按类别分层绘制节点
    for cat, color in NODE_COLORS.items():
        nodes = [n for n, a in g.nodes(data=True) if a.get("category") == cat]
        if not nodes:
            continue
        nx.draw_networkx_nodes(
            g, pos, nodelist=nodes, node_color=color,
            node_size=2600 if cat == "formula" else 1500,
            alpha=0.9, ax=ax,
        )

    # 边：组成（灰实线，标注角色）/ 禁忌（红虚线）
    comp = [(u, v) for u, v, a in g.edges(data=True) if a.get("type") == "组成"]
    taboo = [(u, v) for u, v, a in g.edges(data=True) if a.get("type") == "禁忌"]
    if comp:
        nx.draw_networkx_edges(g, pos, edgelist=comp, edge_color="#9e9e9e", width=1.0, ax=ax)
    if taboo:
        nx.draw_networkx_edges(g, pos, edgelist=taboo, edge_color="#e53935", width=1.6, style="dashed", ax=ax)

    # 禁忌边标签：十八反/十九畏
    labels = {
        (u, v): a.get("verse", "")
        for u, v, a in g.edges(data=True)
        if a.get("type") == "禁忌" and u < v
    }
    nx.draw_networkx_edge_labels(g, pos, edge_labels=labels, font_size=10, font_color="#c62828", ax=ax)

    # 节点标签（方剂用 name_cn，其余用 id）
    labels = {
        n: (a.get("name_cn") if a.get("category") == "formula" else n)
        for n, a in g.nodes(data=True)
    }
    nx.draw_networkx_labels(g, pos, labels=labels, font_size=10,
                            font_family="Microsoft YaHei", ax=ax)

    # 图例
    legend_items = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#4caf50", markersize=14, label="带档案药材（20 类 + 扩展）"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#bdbdbd", markersize=14, label="次要节点（只存名字）"),
        plt.Line2D([0], [0], marker="s", color="w", markerfacecolor="#42a5f5", markersize=12, label="经典方剂"),
        plt.Line2D([0], [0], color="#e53935", lw=1.6, ls="--", label="配伍禁忌（十八反/十九畏）"),
        plt.Line2D([0], [0], color="#9e9e9e", lw=1.0, label="方剂组成"),
    ]
    ax.legend(handles=legend_items, loc="lower left", fontsize=11, framealpha=0.9)

    out = output or str(OUT_PATH)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


if __name__ == "__main__":
    print(f"已渲染：{render()}")
