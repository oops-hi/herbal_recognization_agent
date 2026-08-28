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
from . import retrieval

# 歌诀全文（溯源时附带）
VERSE_18 = "十八反歌诀：半蒌贝蔹及攻乌，藻戟遂芫俱战草，诸参辛芍叛藜芦"
VERSE_19 = "十九畏歌诀：硫黄原是火中精，朴硝一见便相争；水银莫与砒霜见，狼毒最怕密陀僧；巴豆性烈最为上，偏与牵牛不顺情；丁香莫与郁金见，牙硝难合京三棱；川乌草乌不顺犀，人参最怕五灵脂；官桂善能调冷气，若逢石脂便相欺"


def _resolve_or_unknown(herb: str) -> str | None:
    """解析药名（含别名），返回节点 id；未收录返回 None。"""
    return builder.resolve(herb)


def get_profile(herb: str) -> str:
    """FR-04 单味药完整档案：性味/归经/功效/主治/用量/毒性/禁忌 + 出处 + L1 鉴别参考。"""
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
    l1 = p.get("L1")
    if l1:
        lines.append("")
        lines.append("【鉴别参考】")
        for key in ("性状", "炮制", "产地", "鉴别要点"):
            if l1.get(key):
                lines.append(f"{key}：{l1[key]}")
        for s in l1.get("similar_herbs") or []:
            lines.append(
                f"相似药材「{s['herb']}」（{s['reason']}）：" + "；".join(s["points"])
            )
        srcs = l1.get("来源标注")
        if srcs:
            lines.append(
                "来源等级：" + "；".join(f"{k}（{v}）" for k, v in srcs.items())
            )
        meta = node.get("meta", {})
        edition = p.get("source_edition", "《中国药典》2020 年版一部")
        if meta.get("review_status") == "reviewed":
            lines.append(
                f"核对状态：已人工核对（{meta.get('reviewed_by', '')}，"
                f"{meta.get('review_date', '')}）；口径={edition}"
            )
        else:
            lines.append(
                f"核对状态：L1 鉴别参考为 LLM 起草（未人工核对，以{edition}为准）"
            )
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


def similar_compare(herb_a: str, herb_b: str) -> str:
    """FR-13 相似药材鉴别对比：L1 相似对知识（互录 points）+ 双方性状摘录。

    输出「鉴别要点对比 + 结论等级（可判定/部分信息/需人工）」，给用户判断依据，
    不做二次硬猜（方案 §7.3：『给用户判断依据，而不是二次硬猜』）。
    """
    g = builder.load()
    ra, rb = builder.resolve(herb_a), builder.resolve(herb_b)
    if ra is None or rb is None:
        missing = [h for h, r in ((herb_a, ra), (herb_b, rb)) if r is None]
        return f"知识库未收录：{'、'.join(missing)}，无法进行鉴别对比（知识缺口）。"
    if ra == rb:
        return f"「{ra}」与「{rb}」为同一药材，无需鉴别。"

    pa = dict(g.nodes[ra]).get("profile", {})
    pb = dict(g.nodes[rb]).get("profile", {})
    la, lb = pa.get("L1", {}), pb.get("L1", {})

    # 互录相似对：A 的 similar_herbs 里有 B（或反向）
    pair_a = next((s for s in la.get("similar_herbs") or [] if s.get("herb") == rb), None)
    pair_b = next((s for s in lb.get("similar_herbs") or [] if s.get("herb") == ra), None)

    lines = [f"【鉴别】「{ra}」vs「{rb}」"]
    if pair_a or pair_b:
        src = pair_a or pair_b
        lines.append(f"混淆风险：{src.get('reason', '外观相近')}")
        for pt in src.get("points", []):
            lines.append(f"- {pt}")
        if pair_a and pair_b:
            level = "可判定"
        else:
            level = "部分信息"
        lines.append(f"结论等级：{level}（双方互录的鉴别知识完整，可据此人工判断）")
    else:
        lines.append("知识库未收录这两味的直接鉴别对比条目（非 required 相似对）。")

    # 性状摘录（L1 有则附上，帮助对照）
    for label, l in (("性状", la), ("性状", lb)):
        pass
    for name, l in ((ra, la), (rb, lb)):
        if l.get("性状"):
            lines.append(f"「{name}」性状：{l['性状']}")
        elif l.get("鉴别要点"):
            lines.append(f"「{name}」鉴别要点：{l['鉴别要点']}")

    has_pair = bool(pair_a or pair_b)
    if not has_pair and not any(l.get("性状") or l.get("鉴别要点") for l in (la, lb)):
        lines.append("结论等级：需人工（库内无对比知识，请对照实物或药典核对）")
    elif not has_pair:
        lines.append("结论等级：部分信息（无互录条目，仅附双方性状供对照，需人工判断）")
    return "\n".join(lines)


def retrieve_doc(query: str, top_k: int = 5) -> str:
    """FR-12 混合检索：图谱精确优先 → 向量模糊召回（方案 §5.2「图谱给准确，RAG 给全面」）。

    两条路径：
    1. 图谱精确：query 直接解析到已收录药材（含别名）→ 返回完整档案（get_profile，带溯源）
    2. 未覆盖开放问题：bge-small-zh 向量召回 top-k（kg/retrieval.py），命中带 source/hash/score
    —— top-1 低于 MIN_SCORE 视为无证据（知识缺口拒答，不硬答）；检索服务不可用则友好降级。
    """
    q = (query or "").strip()
    if not q:
        return "请提供检索问题，如「哪种药能明目」。"

    # ---- 路径 1：图谱精确（含别名解析）----
    if builder.resolve(q) is not None:
        return get_profile(q)

    # ---- 路径 2：向量模糊召回 ----
    hits = retrieval.search(q, top_k=top_k)
    if hits is None:
        return (
            f"检索服务暂不可用（本地向量库或模型缺失），未能覆盖「{q}」。"
            f"图谱精确检索不受影响，可换用已收录药材名查询。"
        )
    if not hits or hits[0]["score"] < retrieval.MIN_SCORE:
        return f"知识库未收录与「{q}」相关的内容，无法提供依据（知识缺口，拒绝凭记忆作答）。"
    return (
        f"「{q}」的检索结果（依据知识库语料，非药典原文）：\n"
        + retrieval.format_hits(hits)
    )


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
    ap.add_argument("--retrieve", help="混合检索（图谱精确优先 → 向量召回）")
    ap.add_argument("--compare", help="相似药材鉴别对比，逗号分隔两味，如 桃仁,苦杏仁")
    ap.add_argument("--top-k", type=int, default=5, help="向量召回条数（配合 --retrieve）")
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
    elif args.retrieve:
        print(retrieve_doc(args.retrieve, top_k=args.top_k))
    elif args.compare:
        a, b = [x.strip() for x in args.compare.split(",")]
        print(similar_compare(a, b))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
