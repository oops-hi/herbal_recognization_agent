"""
kg/disambiguate.py
易混淆中药饮片鉴别闭环（2026-08-31）：
外部资料库 + 识别触发检测 + 提问清单/研判材料两模式（鉴别 Agent 工具底层）。

- load_guide()：kg/data/similar_guide.json 懒加载单例（fail-soft，缺文件 → 空库）
- find_similar_pairs(top3_names)：识别触发检测——图谱互录对 ∪ guide 对，
  两成员都 ∈ top3 才入选，连通分量聚簇（砂仁/豆蔻/草豆蔻三成员聚 1 组）
- disambiguate_material(candidates, desc)：工具底层，两模式——
  desc 空 = 提问清单（维度对照表，代码只供材料不评分，LLM 主裁决红线）
  desc 有 = 研判材料（对照表 + 档案节选 + 来源档位 + 核对状态 + B 档功效差异）

用法：
    conda run -n task python -m kg.disambiguate --pairs-check --top3 桃仁,苦杏仁,川楝子
    conda run -n task python -m kg.disambiguate --candidates 桃仁,苦杏仁
    conda run -n task python -m kg.disambiguate --candidates 桃仁,苦杏仁 --desc "扁长卵形，表面颗粒突起"
"""
import argparse
import json

from config import BASE_DIR
from . import builder

GUIDE_PATH = BASE_DIR / "kg" / "data" / "similar_guide.json"
GUIDE_LABEL = "《易混淆中药饮片鉴别汇总》（教材汇编 B 档，非药典原文）"

_guide: dict | None = None


def load_guide() -> dict:
    """懒加载鉴别资料库（fail-soft：文件缺失/损坏 → 空库，不抛异常）。"""
    global _guide
    if _guide is None:
        try:
            _guide = json.loads(GUIDE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _guide = {"pairs": [], "general_principle": ""}
    return _guide


def _norm(name: str) -> str:
    """药名归一化：图谱别名/标准名 → 节点 id（标准名）；未收录保持原名。"""
    r = builder.resolve(name)
    return r if r else name.strip()


def _pair_members() -> list[tuple[str, str, dict]]:
    """全部相似对（图谱互录 ∪ guide），去重返回 (a, b, 元信息)。"""
    pairs: dict[frozenset[str], dict] = {}
    g = builder.load()
    # ① 图谱互录（L1.similar_herbs，21 对；含 4 组新对）
    for nid, attrs in g.nodes(data=True):
        for s in attrs.get("profile", {}).get("L1", {}).get("similar_herbs", []):
            t = builder.resolve(s.get("herb", ""))
            if t is None or t == nid:
                continue
            pairs.setdefault(
                frozenset((nid, t)),
                {"reason": s.get("reason", "外形易混"), "source": "知识图谱 L1 相似对（药典【性状】归纳）"},
            )
    # ② 外部资料库（MD 17 条两两条目，含白名单外药材）
    for p in load_guide().get("pairs", []):
        a, b = p.get("a"), p.get("b")
        if not a or not b or a == b:
            continue
        pairs.setdefault(
            frozenset((a, b)),
            {"reason": f"「{a}」「{b}」外形易混（教材汇编）", "source": GUIDE_LABEL},
        )
    return [(a, b, m) for (a, b), m in pairs.items()]


def find_similar_pairs(top3_names: list[str]) -> list[dict]:
    """识别触发检测：两成员都 ∈ top3 的相似对 → 连通分量聚簇。

    返回 [{candidates:[...], pairs:[{a,b,reason,source}], tip}]；无命中返回 []。
    只做「同现」判定，是否真正模糊交给智能体（代码不评分，红线）。
    """
    norm = {_norm(n) for n in top3_names if n.strip()}
    if not norm:
        return []
    hit = [
        {"a": a, "b": b, "reason": m["reason"], "source": m["source"]}
        for a, b, m in _pair_members()
        if a in norm and b in norm
    ]
    if not hit:
        return []

    # 连通分量聚类（成员共享即合并：砂仁/豆蔻/草豆蔻 → 1 组）
    by_herb: dict[str, set[int]] = {}
    for i, p in enumerate(hit):
        by_herb.setdefault(p["a"], set()).add(i)
        by_herb.setdefault(p["b"], set()).add(i)
    seen: set[int] = set()
    clusters = []
    for i in range(len(hit)):
        if i in seen:
            continue
        stack, comp = [i], []
        seen.add(i)
        while stack:
            j = stack.pop()
            comp.append(j)
            for h in (hit[j]["a"], hit[j]["b"]):
                for k in by_herb.get(h, ()):
                    if k not in seen:
                        seen.add(k)
                        stack.append(k)
        comp.sort()
        candidates = sorted({h for j in comp for h in (hit[j]["a"], hit[j]["b"])})
        clusters.append({
            "candidates": candidates,
            "pairs": [hit[j] for j in comp],
            "tip": f"识别候选中「{'、'.join(candidates)}」为外形易混淆药材，可向用户追问可观察特征",
        })
    return clusters


def _guide_entries(name: str) -> list[dict]:
    """候选参与的全部 guide 两两条目。"""
    return [p for p in load_guide().get("pairs", []) if p.get("a") == name or p.get("b") == name]


def _profile_snippet(name: str) -> list[str]:
    """图谱档案节选（研判材料用）：L1 性状/鉴别要点 + L2 性味/用量/毒性 + 来源 + 核对状态。"""
    node = builder.get_node(builder.load(), name)
    if node is None or node.get("category") != "herb":
        return [f"「{name}」未收录核心档案（仅教材 B 档鉴别资料可用）"]
    p = node.get("profile", {})
    l1 = p.get("L1", {})
    meta = node.get("meta", {})
    lines = [
        f"「{name}」档案（{node.get('source', '未标注')}）：",
        f"  性味：{p.get('性味', '')}；用量：{p.get('用量', '')}；毒性：{p.get('毒性', '')}",
    ]
    if l1.get("性状"):
        lines.append(f"  性状：{l1['性状']}")
    if l1.get("鉴别要点"):
        lines.append(f"  鉴别要点：{l1['鉴别要点']}")
    if meta.get("review_status") == "reviewed":
        lines.append(f"  核对状态：已人工核对（{meta.get('reviewed_by', '')} {meta.get('review_date', '')}）")
    else:
        lines.append("  核对状态：L1 鉴别参考为 LLM 起草（未人工核对，以药典为准）")
    return lines


def disambiguate_material(candidates: list[str], desc: str = "") -> str:
    """鉴别 Agent 工具底层：提问清单（desc 空）/ 研判材料（desc 有）。

    代码只组织材料（维度对照/档案/来源/核对状态），不评分不给结论——
    裁决权在 LLM（方案红线：不做纯代码打分）。
    """
    cands = [_norm(c) for c in candidates if c]
    cands = [c for c in cands if c]
    if len(cands) < 2:
        return "鉴别需要至少两个候选药材名称。"

    # 维度对照（guide 条目两两对齐）
    dims: dict[str, dict[str, str]] = {}
    for c in cands:
        for p in _guide_entries(c):
            for it in p.get("items", []):
                d = it.get("dim", "性状")
                dims.setdefault(d, {})[p["a"]] = it.get("a", "")
                dims.setdefault(d, {})[p["b"]] = it.get("b", "")
    diff_rows = []
    for d, per in dims.items():
        row = [f"{cc}：{per[cc]}" for cc in cands if cc in per]
        if len(row) >= 2:
            diff_rows.append(f"- {d}：{'；'.join(row)}")

    if not desc:
        # —— 提问清单模式 ——
        lines = [
            f"【相似对鉴别提问清单】候选：{'、'.join(cands)}",
            f"（依据：{GUIDE_LABEL}；可观察特征按『一看二摸三闻四尝』组织，逐项询问用户，不强答）",
        ]
        lines.extend(diff_rows if diff_rows else ["- 资料库暂无该对的维度对照，请让用户描述形状/颜色/质地/气味中的可观察差异"])
        lines.append("提示：提问时只引用以上维度；用户描述后请再次调用本工具（附上描述）获取研判材料。")
        return "\n".join(lines)

    # —— 研判材料模式 ——
    lines = [
        f"【相似对鉴别研判材料】候选：{'、'.join(cands)}；用户描述：{desc}",
    ]
    lines.extend(diff_rows)
    # 功效差异（B 档标签，知识展示不指导用药）
    for c in cands:
        for p in _guide_entries(c):
            other = p["b"] if p["a"] == c else p["a"]
            if other in cands and p.get("efficacy_diff"):
                lines.append(f"药效差异（{GUIDE_LABEL}，仅供参考，非用药指导）：{p['efficacy_diff']}")
                break
    # 档案节选
    for c in cands:
        lines.extend(_profile_snippet(c))
    lines.append("结论由你（模型）综合用户描述与以上材料作出，给出结论等级（可判定/部分信息/需人工）+ 依据；把握不足时如实说明。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="易混淆饮片鉴别闭环 CLI")
    ap.add_argument("--pairs-check", action="store_true", help="触发检测：--top3 里是否命中相似对并聚簇")
    ap.add_argument("--top3", help="识别 top3 名称，逗号分隔（配合 --pairs-check）")
    ap.add_argument("--candidates", help="鉴别候选，逗号分隔，如 桃仁,苦杏仁")
    ap.add_argument("--desc", default="", help="用户描述的可观察特征（有 = 研判材料；无 = 提问清单）")
    args = ap.parse_args()

    if args.pairs_check:
        names = [n.strip() for n in (args.top3 or "").split(",") if n.strip()]
        clusters = find_similar_pairs(names)
        if not clusters:
            print("无相似对命中。")
            return
        for cl in clusters:
            print(f"候选组：{'、'.join(cl['candidates'])}")
            for p in cl["pairs"]:
                print(f"  - {p['a']}↔{p['b']}（{p['reason']}；来源：{p['source']}）")
        return
    if args.candidates:
        cands = [n.strip() for n in args.candidates.split(",") if n.strip()]
        print(disambiguate_material(cands, args.desc))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
