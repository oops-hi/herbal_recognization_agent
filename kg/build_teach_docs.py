"""
kg/build_teach_docs.py —— STUDY 讲解知识库语料生成器（企业级：版本化/溯源/多源分级/幂等）。

数据流：data/teach_src/<herb>.json（人写源稿，LLM 起草 + 人工核对 + 古籍真实引文）
        → data/teach_docs/<version>/docs/<herb>.json + MANIFEST.json
        → kg/retrieval.py --teach-ingest（向量库 teach_rag/，参数化复用）

与 kg/build_docs.py（records → tcm_docs）同构：
- stdlib-only；幂等（VERSION 固定串，禁动态时间戳）；MANIFEST 版本指纹 + source_records_hash
  （teach_src 改了但版本目录没重生成 → 指纹失配 → ingest 拒收）

教学硬约束（--strict，任一违反即拒收）：
① 味名单 == models/class_map.json 20 个中文名（0 缺 0 多）
② 每味固定 8 节（SECTION_ORDER）；text ≤ 400 汉字；source/source_kind/source_edition 带全率 100%
③ source_kind ∈ {药典(A 档), 教材(B 档), 古籍(C 档)}；各节只许 SECTION_KINDS 允许的档位
   （性味归经 / 用法用量与注意 = 药典 A 档专属；本草记载 = 古籍 C 档专属）
④ 古籍条目必填 quote + quote_loc（工程强制：LLM 不得凭记忆引文）；非古籍禁止携带 quote
⑤ A 档（药典）条目正文必须逐字包含 kg/data/records/<herb>.json 对应字段值
   （records 人工核对后的任何修正都会在下次构建时"引爆"，防讲稿口径漂移）

用法：
    conda run -n task python -m kg.build_teach_docs --dry-run     # 预览统计，不写盘
    conda run -n task python -m kg.build_teach_docs --strict      # 生成 data/teach_docs/<version>/
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from config import BASE_DIR

TEACH_SRC_DIR = BASE_DIR / "data" / "teach_src"
TEACH_DOCS_DIR = BASE_DIR / "data" / "teach_docs"
RECORDS_DIR = BASE_DIR / "kg" / "data" / "records"
CLASS_MAP = BASE_DIR / "models" / "class_map.json"

# 版本目录名（固定串，禁止动态时间戳——幂等红线；源稿变更后手动 bump 并重跑）
VERSION = "2026-09-01_v1"

# 讲解口径（同 P0：2026-08-28 用户拍板维持 2020 版；古籍 = 本草记载，仅学术参考）
EDITION = "《中国药典》2020 年版一部"

# 固定 8 节结构（缺位=拒收）与各节允许的档位（档位 = 知识源分级 §6.1）
SECTION_ORDER = ("概览", "基原与性状", "性味归经", "功效与主治",
                 "应用举例", "本草记载", "用法用量与注意", "易混与鉴别")
SECTION_KINDS = {
    "概览": {"教材"},            # 导语式综述（B 档）
    "基原与性状": {"药典", "教材"},
    "性味归经": {"药典"},        # A 档专属（剂量知识条目只出自药典）
    "功效与主治": {"药典", "教材"},
    "应用举例": {"教材"},        # 方剂/茶饮举例（B 档）
    "本草记载": {"古籍"},        # C 档专属（真实原文摘录 + 白话解读）
    "用法用量与注意": {"药典"},  # A 档专属
    "易混与鉴别": {"教材", "药典"},
}
KIND_TIERS = {"药典": "A 档", "教材": "B 档", "古籍": "C 档"}
MAX_TEXT_LEN = 400  # 汉字上限（保 embedding max_length=512 不截断，超长拆节）

# A 档 diff：各节必须逐字包含的 records 字段
A_DIFF_FIELDS = {
    "性味归经": ["性味"],
    "功效与主治": ["功效", "主治"],
    "用法用量与注意": ["用量", "毒性", "禁忌"],
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_herbs() -> set[str]:
    """讲解库味名单 = 分类器 20 类中文名（models/class_map.json）。"""
    cm = json.loads(Path(CLASS_MAP).read_text(encoding="utf-8"))
    return {v["name_cn"] for v in cm.values()}


def generate() -> dict[str, list[dict]]:
    """teach_src → {herb: [讲解条目]}。每条带档位/出处/quote/哈希。"""
    out = {}
    for path in sorted(Path(TEACH_SRC_DIR).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        herb = payload["herb"]
        entries = []
        for s in payload["sections"]:
            quote = s.get("quote", "") or ""
            quote_loc = s.get("quote_loc", "") or ""
            entries.append({
                "herb": herb,
                "section": s["section"],
                "title": s.get("title", ""),
                "text": s["text"],
                "source": s["source"],
                "source_kind": s["source_kind"],
                "source_edition": s.get("source_edition", ""),
                "quote": quote,
                "quote_loc": quote_loc,
                # quote 纳入哈希：文本与引文任一改动都使条目失效
                "hash": sha256_text(s["text"] + quote + quote_loc),
            })
        out[herb] = entries
    return out


def _a_diff_errors(section: str, text: str, herb: str) -> list[str]:
    """A 档条目正文 vs records 对应字段值：缺哪个子串报哪个（防口径漂移）。"""
    rec_path = Path(RECORDS_DIR) / f"{herb}.json"
    if not rec_path.exists():
        return [f"records 缺失 {herb}.json（无法做 A 档 diff）"]
    p = json.loads(rec_path.read_text(encoding="utf-8")).get("profile", {})
    missing = []
    for field in A_DIFF_FIELDS.get(section, ()):
        val = p.get(field, "")
        if not val:
            continue
        if field == "性味":
            if val not in text:
                missing.append(f"性味「{val}」")
        elif val not in text:
            missing.append(f"{field}「{val[:30]}…」")
    if section == "性味归经":
        for mer in p.get("归经", []):
            if mer and mer not in text:
                missing.append(f"归经「{mer}」")
    return missing


def validate(all_docs: dict, strict: bool = False) -> None:
    """教学硬约束校验：违反 → SystemExit（--strict 下 warning 同样拒收）。"""
    errors: list[str] = []
    warnings: list[str] = []
    expected = _expected_herbs()

    # ① 味名单 == class_map 20 名
    got = set(all_docs)
    if got != expected:
        errors.append(
            f"味名单与 class_map 不一致：缺 {sorted(expected - got)} / 多 {sorted(got - expected)}"
        )
    if len(got) != len(expected):
        errors.append(f"味数 {len(got)} != {len(expected)}")

    n_doc = n_source = n_kind = n_edition = 0
    for herb, entries in sorted(all_docs.items()):
        sections = [e["section"] for e in entries]
        # ② 每味固定 8 节
        if sorted(sections) != sorted(SECTION_ORDER):
            errors.append(f"{herb}：节不齐（{sections}）")
        if len(entries) != len(SECTION_ORDER):
            errors.append(f"{herb}：条目数 {len(entries)} != {len(SECTION_ORDER)}")
        for e in entries:
            n_doc += 1
            text = e["text"]
            if not (0 < len(text) <= MAX_TEXT_LEN):
                errors.append(f"{herb}·{e['section']}：text 长度 {len(text)} 超限（1~{MAX_TEXT_LEN}）")
            if e.get("source"):
                n_source += 1
            kind = e.get("source_kind", "")
            if kind in KIND_TIERS:
                n_kind += 1
            else:
                errors.append(f"{herb}·{e['section']}：source_kind={kind!r} 不在 {{药典,教材,古籍}}")
            if e.get("source_edition"):
                n_edition += 1
            if kind:
                allowed = SECTION_KINDS.get(e["section"], set())
                if allowed and kind not in allowed:
                    errors.append(
                        f"{herb}·{e['section']}：档位 {KIND_TIERS.get(kind, kind)}"
                        f" 不被允许（{ '、'.join(KIND_TIERS.get(k, k) for k in sorted(allowed)) }）"
                    )
            # ④ 古籍必填 quote+quote_loc；非古籍禁止携带
            quote, quote_loc = e.get("quote", ""), e.get("quote_loc", "")
            if kind == "古籍":
                if not quote or not quote_loc:
                    errors.append(f"{herb}·{e['section']}：古籍条目必填 quote 与 quote_loc（严禁 LLM 凭记忆引文）")
                if quote and quote in text:
                    warnings.append(f"{herb}·{e['section']}：解读中重复包含原文摘录（冗余，应只放 quote 字段）")
            elif quote or quote_loc:
                errors.append(f"{herb}·{e['section']}：quote/quote_loc 只允许古籍条目携带")
            # ⑤ A 档 diff
            if kind == "药典":
                for m in _a_diff_errors(e["section"], text, herb):
                    errors.append(f"{herb}·{e['section']}：A 档 diff 未通过——{m}（records 已变更，需同步改讲稿）")

    print(
        f"herb {len(all_docs)} 味 / 文档 {n_doc} 条 / 带 source {n_source} / "
        f"带 source_kind {n_kind} / 带 source_edition {n_edition}"
    )
    sections = {}
    for entries in all_docs.values():
        for e in entries:
            sections[e["section"]] = sections.get(e["section"], 0) + 1
    print("分节：" + "、".join(f"{k} {v}" for k, v in sorted(sections.items())))
    kinds = {}
    for entries in all_docs.values():
        for e in entries:
            kinds[KIND_TIERS.get(e.get("source_kind", ""), e.get("source_kind", "?"))] = \
                kinds.get(KIND_TIERS.get(e.get("source_kind", ""), e.get("source_kind", "?")), 0) + 1
    print("档位：" + "、".join(f"{k} {v}" for k, v in sorted(kinds.items())))

    if errors:
        print("\n".join(f"[ERROR] {e}" for e in errors), file=sys.stderr)
        raise SystemExit(f"[FATAL] {len(errors)} 处校验错误，拒收（--strict 口径）")
    if warnings and strict:
        print("\n".join(f"[WARN] {w}" for w in warnings), file=sys.stderr)
        raise SystemExit(f"[FATAL] --strict：{len(warnings)} 处 warning，拒收")
    for w in warnings:
        print(f"[WARN] {w}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="teach_src → data/teach_docs/ 讲解语料生成器")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写盘")
    ap.add_argument("--strict", action="store_true", help="有 warning 即按错误退出")
    ap.add_argument("--version", default=VERSION, help="版本目录名（默认 VERSION 常量）")
    args = ap.parse_args()

    if not Path(TEACH_SRC_DIR).exists():
        raise SystemExit(f"[FATAL] 找不到 teach_src 目录：{TEACH_SRC_DIR}")

    all_docs = generate()
    validate(all_docs, strict=args.strict)

    version_dir = Path(TEACH_DOCS_DIR) / args.version / "docs"
    if args.dry_run:
        print(f"dry-run：将生成 {version_dir}（{sum(len(v) for v in all_docs.values())} 条）")
        return

    # ---- 写盘 ----
    os.makedirs(version_dir, exist_ok=True)
    file_hashes = {}
    for herb, entries in sorted(all_docs.items()):
        path = version_dir / f"{herb}.json"
        payload = json.dumps(
            {"herb": herb, "kind": "teach", "docs": entries},
            ensure_ascii=False, indent=2,
        ) + "\n"
        path.write_text(payload, encoding="utf-8")
        file_hashes[path.name] = sha256_file(path)
    # MANIFEST：版本指纹 + 来源源稿指纹（teach_src 改了但没重生成 ⇒ source_records_hash 失配拒收）
    src_hash = sha256_text(
        "".join(sorted(
            f"{p.name}:{sha256_file(p)}" for p in Path(TEACH_SRC_DIR).glob("*.json")
        ))
    )
    manifest = {
        "schema_version": 1,
        "note": "由 kg/build_teach_docs.py 从 data/teach_src/ 程序化生成，勿手改；重生成 = 重跑 build_teach_docs",
        "current": args.version,
        "versions": [
            {
                "dir": args.version,
                "edition": f"多源：{EDITION} / 教材 / 古籍（本草记载仅学术参考）",
                "doc_count": sum(len(v) for v in all_docs.values()),
                "file_hashes": file_hashes,
                "source_records_hash": src_hash,
            }
        ],
    }
    (Path(TEACH_DOCS_DIR) / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写 {version_dir}（{sum(len(v) for v in all_docs.values())} 条）+ MANIFEST.json")


if __name__ == "__main__":
    main()
