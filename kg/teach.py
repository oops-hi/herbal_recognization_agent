"""
kg/teach.py —— STUDY 讲解知识库查询（讲解子 Agent 的 LLM 可见唯一入口）。

对齐 kg/query.retrieve_doc 的「精确优先 → 向量」结构，但独立于档案语料：
- 药名解析（builder.resolve 含别名 / 药名子串）→ 返回该味 8 节全量课文（带档位/出处/哈希）
- 开放问题 → teach_rag 向量召回（kg/retrieval.search_teach），top-1 < MIN_SCORE → 知识缺口拒答
- 服务不可用 → fail-soft 降级（图谱档案查询不受影响）

格式约定：每条课文必带「出处：…」行（agent/core.py SOURCE_RE 第二分支直接可提取证据链），
档位标注（A 档药典 / B 档教材 / C 档古籍）直接在源串上，前端证据块零改动。

CLI：
    conda run -n task python -m kg.teach --herb 枸杞子     # 精确路径：全节课文
    conda run -n task python -m kg.teach --query "什么药润肺"  # 向量路径
    conda run -n task python -m kg.teach --ingest           # 重建讲解向量库
    conda run -n task python -m kg.teach --verify           # 重放 MANIFEST 指纹完整性
"""
import argparse
import json
import sys
from pathlib import Path

from config import BASE_DIR

from . import builder, retrieval
from .build_docs import sha256_file, sha256_text
from .build_teach_docs import TEACH_SRC_DIR, KIND_TIERS, SECTION_ORDER

TEACH_DOCS_DIR = BASE_DIR / "data" / "teach_docs"

MIN_SCORE = retrieval.MIN_SCORE  # 0.45（与档案检索同阈值，不因教学放宽引入弱证据）


# ---------- 课文格式化 ----------

def _fmt_entry(e: dict, idx: int = 0) -> str:
    """单条讲解条目 → LLM 可读文本（必含「出处：」行 + 档位标注 + 内容哈希）。"""
    kind = KIND_TIERS.get(e.get("source_kind", ""), e.get("source_kind", "未知"))
    head = f"{idx}. " if idx else ""
    lines = [
        f"{head}【{e['herb']} · {e['section']}】（{kind}）",
        f"课文：{e.get('title', '')}：{e['text']}",
    ]
    if e.get("quote"):
        lines.append(f"原文摘录：{e['quote']}（{e.get('quote_loc', '')}）")
    lines.append(f"出处：{e['source']}（{e.get('source_edition', '')}）；内容哈希 {e.get('hash', '')[:12]}")
    return "\n".join(lines)


def format_lesson(docs: list[dict]) -> str:
    """该味 8 节全量课文（按 SECTION_ORDER 排序，档位/出处齐全）。"""
    ordered = {e["section"]: e for e in docs}
    blocks = [
        _fmt_entry(ordered[s])
        for s in SECTION_ORDER
        if s in ordered
    ]
    return "\n\n".join(blocks)


def format_teach_hits(hits: list[dict]) -> str:
    """向量命中 → LLM 可读文本（对齐 retrieval.format_hits 风格，带档位 + 出处行）。"""
    blocks = []
    for i, h in enumerate(hits, 1):
        kind = KIND_TIERS.get(h.get("source_kind", ""), h.get("source_kind", "未知"))
        lines = [
            f"{i}. 【{h['herb']} · {h['section']}】（{kind}）",
            f"课文：{h.get('title', '')}：{h['text']}",
        ]
        if h.get("quote"):
            lines.append(f"原文摘录：{h['quote']}（{h.get('quote_loc', '')}）")
        lines.append(
            f"出处：{h['source']}（{h.get('source_edition', '')}）；"
            f"相似度 {h['score']}；内容哈希 {h.get('hash', '')[:12]}"
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ---------- 查询 ----------

def _teach_docs_for(herb: str) -> list[dict] | None:
    """读该味当前版讲解课文；版本目录缺失/未生成 → None。"""
    try:
        manifest = json.loads((Path(TEACH_DOCS_DIR) / "MANIFEST.json").read_text(encoding="utf-8"))
        path = Path(TEACH_DOCS_DIR) / manifest["current"] / "docs" / f"{herb}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["docs"]
    except Exception:
        return None


def _resolve_herb_name(query: str) -> str | None:
    """药名解析：整串别名解析优先；否则遍历图谱 herb 节点做子串匹配（最长名优先）。

    返回图谱节点 id（= 药名）；只认图谱已收录（含别名），防把任意词当药名。"""
    r = builder.resolve(query)
    if r is not None:
        return r
    g = builder.load()
    candidates = []
    for nid, attrs in g.nodes(data=True):
        if attrs.get("category") != "herb":
            continue
        names = [attrs.get("name_cn", nid)] + list(attrs.get("aliases") or [])
        for nm in sorted(set(names), key=len, reverse=True):
            if nm and nm in query:
                candidates.append((len(nm), nid))
    if not candidates:
        return None
    candidates.sort(key=lambda kv: -kv[0])
    return candidates[0][1]


def teach_query(query: str, top_k: int = 5) -> str:
    """讲解检索（精确优先 → 向量召回）。返回可直接给 LLM 的课文文本。"""
    q = (query or "").strip()
    if not q:
        return "请提供要讲解的药材名或教学问题，如「讲讲枸杞子」「什么药润肺」。"

    # ---- 路径 1：药名解析 → 全量课文 ----
    herb = _resolve_herb_name(q)
    if herb is not None:
        docs = _teach_docs_for(herb)
        if docs is None:
            return (
                f"知识库未收录「{herb}」的讲解课程（知识缺口）。"
                f"图谱档案查询不受影响，可改用 get_herb_profile 获取药典口径档案。"
            )
        return format_lesson(docs)

    # ---- 路径 2：向量召回 ----
    hits = retrieval.search_teach(q, top_k=top_k)
    if hits is None:
        return (
            "讲解知识库暂不可用（本地向量库或模型缺失），未能生成讲解。"
            "可改用已收录药材名（如「讲讲枸杞子」）或图谱档案查询。"
        )
    if not hits or hits[0]["score"] < MIN_SCORE:
        return f"知识库未收录与「{q}」相关的讲解内容（知识缺口，拒绝凭记忆作答）。"
    return f"「{q}」的讲解检索结果（依据讲解知识库语料）：\n" + format_teach_hits(hits)


def verify() -> dict:
    """重放指纹完整性：版本目录存在、docs 文件哈希与 MANIFEST 一致、源稿指纹一致。"""
    try:
        manifest = json.loads((Path(TEACH_DOCS_DIR) / "MANIFEST.json").read_text(encoding="utf-8"))
        cur = manifest["current"]
        ver = next(v for v in manifest["versions"] if v["dir"] == cur)
        docs_dir = Path(TEACH_DOCS_DIR) / cur / "docs"
        if not docs_dir.exists():
            return {"ok": False, "reason": f"版本目录缺失：{docs_dir}"}
        file_hashes = {p.name: sha256_file(p) for p in sorted(docs_dir.glob("*.json"))}
        if file_hashes != ver["file_hashes"]:
            return {"ok": False, "reason": "docs 文件哈希与 MANIFEST 不一致（内容被改动，需重跑 build_teach_docs）"}
        src_hash = sha256_text(
            "".join(sorted(
                f"{p.name}:{sha256_file(p)}" for p in Path(TEACH_SRC_DIR).glob("*.json")
            ))
        )
        if src_hash != ver["source_records_hash"]:
            return {"ok": False, "reason": "teach_src 与 MANIFEST 指纹失配（源稿已改动，需重跑 build_teach_docs）"}
        return {"ok": True, "dir": cur, "docs": len(file_hashes)}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def main():
    ap = argparse.ArgumentParser(description="讲解知识库查询 CLI（STUDY 子 Agent 工具底层）")
    ap.add_argument("--herb", help="按药名输出整节课文（精确路径）")
    ap.add_argument("--query", help="开放问题语义检索（向量路径）")
    ap.add_argument("--ingest", action="store_true", help="重建讲解向量库（teach_rag）")
    ap.add_argument("--verify", action="store_true", help="重放 MANIFEST 指纹完整性")
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()

    if args.ingest:
        r = retrieval.ingest_teach()
        print(json.dumps(r, ensure_ascii=False))
    elif args.verify:
        print(json.dumps(verify(), ensure_ascii=False))
    elif args.herb:
        docs = _teach_docs_for(args.herb)
        if docs is None:
            print(f"知识库未收录「{args.herb}」的讲解课程（知识缺口）。")
            sys.exit(1)
        print(format_lesson(docs))
    elif args.query:
        print(teach_query(args.query, top_k=args.top_k))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
