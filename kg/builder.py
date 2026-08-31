"""
kg/builder.py
kg.json（nodes[] + edges[]）→ networkx.MultiDiGraph。

- 节点：herb（有档案）/ herb_minor（只存名字）/ formula（方剂）
- 边：组成（formula→herb，attr.role=君臣佐使）/ 禁忌（herb_a↔herb_b，无向，双向各建一条，
  attr：verse / pharmacopoeia / source / note）
- 相似边（2026-08-31）：由节点档案 L1.similar_herbs **派生**（数据不动 schema），无向，双向各建一条，
  attr：reason / points（鉴别对照）
- 别名解析：节点 aliases 全量索引，供 query.py 按别名查名
- 图构建结果模块级缓存（Flask 多线程下只建一次）
"""
import json

import networkx as nx

from config import BASE_DIR

KG_PATH = BASE_DIR / "kg" / "data" / "kg.json"

_graph: nx.MultiDiGraph | None = None
_alias_index: dict[str, str] | None = None   # 别名/中文名 → 节点 id


def load() -> nx.MultiDiGraph:
    """构建（或复用缓存）知识图谱 MultiDiGraph。"""
    global _graph, _alias_index
    if _graph is not None:
        return _graph

    data = json.loads(KG_PATH.read_text(encoding="utf-8"))
    g = nx.MultiDiGraph()

    for node in data["nodes"]:
        attrs = {k: v for k, v in node.items() if k not in ("id", "category")}
        g.add_node(node["id"], category=node["category"], **attrs)

    _alias_index = {}
    for node in data["nodes"]:
        _alias_index[node["id"]] = node["id"]
        for alias in node.get("aliases", []):
            _alias_index.setdefault(alias, node["id"])  # 首次出现优先，防别名撞名

    for edge in data["edges"]:
        if edge["type"] == "组成":
            # 端点先过别名索引（如 kg.json 边里写「杏仁」→ 节点「苦杏仁」），
            # 防止产生无 category 的幽灵节点
            formula = _alias_index.get(edge["formula"], edge["formula"])
            herb = _alias_index.get(edge["herb"], edge["herb"])
            g.add_edge(formula, herb, type="组成", role=edge["role"])
        elif edge["type"] == "禁忌":
            # 无向禁忌 → 双向边，查询时任一方向都能命中
            attrs = {
                "type": "禁忌",
                "verse": edge.get("verse", ""),
                "pharmacopoeia": edge.get("pharmacopoeia", ""),
                "source": edge.get("source", ""),
                "note": edge.get("note", ""),
            }
            g.add_edge(edge["herb_a"], edge["herb_b"], **attrs)
            g.add_edge(edge["herb_b"], edge["herb_a"], **attrs)

    # 相似边：从节点档案 L1.similar_herbs 派生（互录两方向去重为一条，双向各建一条）
    seen_similar: set[frozenset[str]] = set()
    for node in data["nodes"]:
        for sim in node.get("profile", {}).get("L1", {}).get("similar_herbs", []):
            target = _alias_index.get(sim.get("herb", ""))
            if target is None or target == node["id"]:
                continue
            key = frozenset((node["id"], target))
            if key in seen_similar:
                continue
            seen_similar.add(key)
            attrs = {
                "type": "相似",
                "reason": sim.get("reason", ""),
                "points": sim.get("points", []),
            }
            g.add_edge(node["id"], target, **attrs)
            g.add_edge(target, node["id"], **attrs)

    _graph = g
    return g


def resolve(name: str) -> str | None:
    """按中文名/别名解析节点 id；解析失败返回 None。"""
    load()
    return _alias_index.get(name.strip())


def get_node(g: nx.MultiDiGraph, name: str) -> dict | None:
    """按别名解析并返回节点属性 dict；未收录返回 None。"""
    node_id = resolve(name)
    if node_id is None:
        return None
    attrs = dict(g.nodes[node_id])
    attrs["id"] = node_id
    return attrs
