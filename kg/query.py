"""
kg/query.py
知识图谱查询 —— 智能体 6 个工具的统一底层。

全部函数返回「可直接给 LLM 看的字符串」，图谱未收录的信息明确写「知识库未收录」，
禁止模型凭记忆补充（NFR-08 / 合规第 5 条）。

用法：
    conda run -n task python -m kg.query --herb 枸杞子
    conda run -n task python -m kg.query --compat 瓜蒌皮,川乌
    conda run -n task python -m kg.query --formulas 山楂
    conda run -n task python -m kg.query --search 寒,肺经
"""
import argparse
import json

from . import builder

# 歌诀全文（溯源时附带）
VERSE_18 = "十八反歌诀：半蒌贝蔹及攻乌，藻戟遂芫俱战草，诸参辛芍叛藜芦"
VERSE_19 = "十九畏歌诀：硫黄原是火中精，朴硝一见便相争；水银莫与砒霜见，狼毒最怕密陀僧；巴豆性烈最为上，偏与牵牛不顺情；丁香莫与郁金见，牙硝难合京三棱；川乌草乌不顺犀，人参最怕五灵脂；官桂善能调冷气，若逢石脂便相欺"


def _resolve_or_unknown(herb: str) -> str | None:
    """解析药名（含别名），返回节点 id；未收录返回 None。"""
    return builder.resolve(herb)


def get_profile(herb: str) -> str:
    """FR-04 单味药完整档案：性味/归经/功效/主治/用量/毒性/禁忌 + 出处。"""
    node = builder.get_node(builder.load(), herb)
    if node is None:
        return f"知识库未收录「{herb}」，无法提供档案。"
    if node["category"] not in ("herb",):
        return f"「{herb}」为方剂/禁忌网络节点，仅收录于组合中，无独立档案。"
    p = node["profile"]
    lines = [
        f"【{node['name_cn']}】{node.get('latin', '')}",
        f"性味：{p['性味']}",
        f"归经：{'、'.join(p['归经'])}",
        f"功效：{p['功效']}",
        f"主治：{p['主治']}",
        f"用量：{p['用量']}",
        f"毒性：{p['毒性']}",
        f"禁忌：{p['禁忌']}",
        f"出处：{node.get('source', '未标注')}",
    ]
    return "\n".join(lines)


def check_compatibility(herbs: list[str]) -> str:
    """FR-05 配伍禁忌检查：十八反/十九畏（歌诀+药典双源）、妊娠禁忌、毒性。

    返回格式：每个冲突对一段，pharmacopoeia=认定/未收录 分别展示两种依据。
    """
    g = builder.load()
    resolved = [(h, builder.resolve(h)) for h in herbs]
    unknown = [h for h, rid in resolved if rid is None]
    if unknown:
        return (
            f"知识库未收录以下药材：{'、'.join(unknown)}，无法检查其配伍禁忌。"
            f"（仅能基于已收录药材判断）"
        )

    ids = [rid for _, rid in resolved]
    conflicts = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            # 禁忌边已建成双向，只查 a→b 方向即可
            for _, v, attrs in g.edges(a, data=True):
                if v == b and attrs.get("type") == "禁忌":
                    conflicts.append((a, b, attrs))

    if not conflicts:
        # 无冲突边 → 也给出毒性/妊娠提示，供模型完整汇报
        warns = []
        for h, rid in resolved:
            node = dict(g.nodes[rid])
            if node.get("category") == "herb":
                p = node.get("profile", {})
                if p.get("毒性", "无毒") != "无毒":
                    warns.append(f"{h}（{p['毒性']}）")
                if "孕妇" in p.get("禁忌", ""):
                    warns.append(f"{h}（孕妇慎用）")
        tail = f"\n注意：{'、'.join(warns)}，均为知识展示，非用药建议。" if warns else ""
        return (
            f"「{'」与「'.join(herbs)}」之间未检索到十八反/十九畏配伍禁忌记录。{tail}"
            f"\n（仅代表知识库收录范围内无冲突记录，不构成用药安全结论）"
        )

    blocks = []
    for a, b, attrs in conflicts:
        pharma = {
            "认定": "《中国药典》2020 年版【注意】项认定：不宜同用",
            "未收录": "《中国药典》未将该对列入【注意】项（仅见歌诀记载）",
        }.get(attrs["pharmacopoeia"], attrs["pharmacopoeia"])
        note = f"\n说明：{attrs['note']}" if attrs.get("note") else ""
        blocks.append(
            f"【禁忌】配伍禁忌：「{a}」×「{b}」\n"
            f"歌诀依据：{attrs['verse']}（{VERSE_18 if attrs['verse'] == '十八反' else VERSE_19}）\n"
            f"药典依据：{pharma}\n"
            f"出处：{attrs['source']}{note}"
        )
    return "\n\n".join(blocks)


def formulas_by_herb(herb: str) -> str:
    """FR-08 按药材反查经典方剂（含君臣佐使角色）。"""
    g = builder.load()
    herb_id = builder.resolve(herb)
    if herb_id is None:
        return f"知识库未收录「{herb}」，无法反查方剂。"
    hits = []
    for pred, _, attrs in g.in_edges(herb_id, data=True):
        if attrs.get("type") == "组成":
            fnode = dict(g.nodes[pred])
            hits.append(
                f"{fnode['name_cn']}（{fnode['formula']['主治']}，出处：{fnode['formula']['出处']}）"
                f"—— 本品在方中为「{attrs['role']}」"
            )
    if not hits:
        return f"知识库收录的方剂中未检索到含「{herb}」的方剂。"
    return f"「{herb}」参与的经典方剂：\n" + "\n".join(f"- {h}" for h in hits)


def formulas_by_symptom(symptom: str) -> str:
    """FR-09 按症状/证候推荐方剂（匹配 主治 + keywords 同义词表）。"""
    g = builder.load()
    keys = [k.strip() for k in symptom.replace("，", ",").split(",") if k.strip()] or [symptom]
    hits = []
    for nid, attrs in g.nodes(data=True):
        if attrs.get("category") != "formula":
            continue
        f = attrs.get("formula", {})
        # keywords 在节点层（与 formula 同级），不在 formula 字典内
        hay = f.get("主治", "") + " " + " ".join(attrs.get("keywords", []))
        if any(k in hay for k in keys):
            members = "、".join(m["herb"] for m in f.get("组成", []))
            hits.append(
                f"{attrs['name_cn']}：主治「{f['主治']}」；组成：{members}；出处：{f['出处']}"
            )
    if not hits:
        return f"知识库未收录与「{symptom}」匹配的方剂，请换一组症状关键词试试。"
    return f"与「{symptom}」相关的经典方剂：\n" + "\n".join(f"- {h}" for h in hits)


def search_herbs(keys: str) -> str:
    """FR-11 按性味/归经/功效反向检索药材。

    检索范围仅限性味/归经/功效（不含主治，避免"寒湿"误中"寒"）；逗号分隔的
    多个关键词为 AND 语义（「寒,肺经」= 寒性且归肺经，→ 连翘、栀子等）。
    """
    g = builder.load()
    keys = [k.strip() for k in keys.replace("，", ",").split(",") if k.strip()]
    if not keys:
        return "请提供检索关键词，如「寒,肺经,清热」。"
    hits = []
    for nid, attrs in g.nodes(data=True):
        if attrs.get("category") != "herb":
            continue
        p = attrs.get("profile", {})
        hay = " ".join(
            [p.get("性味", ""), " ".join(p.get("归经", [])), p.get("功效", "")]
        )
        # 「肺经」归一化为「肺」再匹配归经
        if all(k in hay or k.rstrip("经") in hay for k in keys):
            hits.append(f"{attrs['name_cn']}（{p.get('性味')}，归{'、'.join(p.get('归经', []))}经）")
    if not hits:
        return f"未检索到同时符合「{'、'.join(keys)}」的药材。"
    return f"符合「{'、'.join(keys)}」的药材：\n" + "\n".join(f"- {h}" for h in hits)


def similar_herbs(herb: str) -> str:
    """FR-10 相似药推荐：共同出现于 ≥1 首方剂的药材，按共现数排序。"""
    g = builder.load()
    herb_id = builder.resolve(herb)
    if herb_id is None:
        return f"知识库未收录「{herb}」，无法推荐相似药。"
    # 该药参与的所有方剂
    formulas = {pred for pred, _, attrs in g.in_edges(herb_id, data=True) if attrs.get("type") == "组成"}
    if not formulas:
        return f"「{herb}」未收录于任何方剂，无法推荐相似药。"
    counter = {}
    for frm in formulas:
        for _, member, attrs in g.out_edges(frm, data=True):
            if attrs.get("type") == "组成" and member != herb_id:
                counter[member] = counter.get(member, 0) + 1
    if not counter:
        return f"「{herb}」参与的方剂中无其他已收录药材。"
    top = sorted(counter.items(), key=lambda kv: -kv[1])[:5]
    names = [f"{g.nodes[n]['name_cn']}（共现 {c} 首）" for n, c in top]
    return f"与「{herb}」同方共现的药材（可作同方类比参考）：\n" + "\n".join(f"- {x}" for x in names)


def graph_stats() -> str:
    """图规模统计（前端/调试用）。"""
    g = builder.load()
    herbs = sum(1 for _, a in g.nodes(data=True) if a.get("category") == "herb")
    formulas = sum(1 for _, a in g.nodes(data=True) if a.get("category") == "formula")
    return (
        f"知识图谱：{g.number_of_nodes()} 节点"
        f"（含 {herbs} 味带档案药材、{formulas} 首方剂），"
        f"{g.number_of_edges()} 条有向边。"
    )


def dump_for_json(name: str) -> str:
    """把任意查询结果序列化为 JSON 字符串（前端 /api/herb/<name> 复用）。"""
    node = builder.get_node(builder.load(), name)
    if node is None:
        return json.dumps({"found": False, "name": name}, ensure_ascii=False)
    return json.dumps({"found": True, **node}, ensure_ascii=False)


def graph_dataset() -> dict:
    """前端 /api/graph 用：全量拓扑 JSON（节点带度数/是否含档案，禁忌双向边去重为一条）。

    单一数据源：直接遍历 builder.load() 的 nx 图，与 6 个查询工具所见一致。
    degree = 唯一邻居数（禁忌双向只算 1）；未知端点（防旧缓存）跳过不抛错。
    """
    g = builder.load()
    node_ids = set(g.nodes)
    nodes, edges = [], []
    for nid, attrs in g.nodes(data=True):
        if attrs.get("category") not in ("herb", "herb_minor", "formula"):
            continue  # 防御：幽灵节点无 category，不发给前端
        neighbors = set(g.predecessors(nid)) | set(g.successors(nid))
        nodes.append({
            "id": nid,
            "category": attrs["category"],
            "name_cn": attrs.get("name_cn", nid),
            "latin": attrs.get("latin", ""),
            "aliases": attrs.get("aliases", []),
            "degree": len(neighbors),
            "has_profile": attrs.get("category") == "herb" and "profile" in attrs,
            "source": attrs.get("source", ""),
        })
    node_ids = {n["id"] for n in nodes}
    seen_tabu = set()
    for u, v, attrs in g.edges(data=True):
        if u not in node_ids or v not in node_ids:
            continue
        if attrs.get("type") == "禁忌":
            key = frozenset((u, v))
            if key in seen_tabu:
                continue  # 禁忌边在 nx 里是双向两条，前端只渲染一条
            seen_tabu.add(key)
            edges.append({
                "source": u, "target": v, "type": "禁忌",
                "verse": attrs.get("verse", ""),
                "pharmacopoeia": attrs.get("pharmacopoeia", ""),
                "note": attrs.get("note", ""),
            })
        elif attrs.get("type") == "组成":
            edges.append({"source": u, "target": v, "type": "组成", "role": attrs.get("role", "")})
    meta = {
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "herb": sum(1 for n in nodes if n["category"] == "herb"),
        "herbMinor": sum(1 for n in nodes if n["category"] == "herb_minor"),
        "formula": sum(1 for n in nodes if n["category"] == "formula"),
    }
    return {"meta": meta, "nodes": nodes, "edges": edges}


def main():
    ap = argparse.ArgumentParser(description="知识图谱查询 CLI")
    ap.add_argument("--herb", help="查单味药档案")
    ap.add_argument("--compat", help="查配伍禁忌，逗号分隔，如 瓜蒌皮,川乌")
    ap.add_argument("--formulas", help="按药材反查方剂")
    ap.add_argument("--symptom", help="按症状推荐方剂")
    ap.add_argument("--search", help="按性味/归经/功效反向检索")
    ap.add_argument("--similar", help="相似药推荐")
    ap.add_argument("--graph", action="store_true", help="输出全量拓扑 JSON（/api/graph 冒烟）")
    ap.add_argument("--stats", action="store_true", help="图规模统计")
    args = ap.parse_args()

    if args.graph:
        print(json.dumps(graph_dataset(), ensure_ascii=False, indent=2))
    elif args.stats:
        print(graph_stats())
    elif args.herb:
        print(get_profile(args.herb))
    elif args.compat:
        print(check_compatibility([h.strip() for h in args.compat.split(",")]))
    elif args.formulas:
        print(formulas_by_herb(args.formulas))
    elif args.symptom:
        print(formulas_by_symptom(args.symptom))
    elif args.search:
        print(search_herbs(args.search))
    elif args.similar:
        print(similar_herbs(args.similar))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
