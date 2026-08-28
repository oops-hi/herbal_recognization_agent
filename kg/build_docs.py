"""
kg/build_docs.py —— 二期 P1：records → data/tcm_docs/ 版本目录种子语料生成器。

数据流：kg/data/records/*.json（64 味 herb）→ data/tcm_docs/<version>/docs/<id>.json
每条文档 = 一个可检索片段，必带 source（药典/教材）+ source_edition + sha256 哈希，
满足方案 §5.3「召回证据必须带 source，无 source 即丢弃」与「哈希溯源」。

设计要点：
- stdlib-only（不引 transformers/torch，打包 excludes 可排；embedding 由 kg/retrieval.py 承担）
- 幂等：version 目录名固定（MANIFEST.current），内容确定 ⇒ 重跑无 git diff
- 粒度：L2 七字段 + L1 五字段 + similar_herbs 逐对拆分，每段独立可检索（细粒度召回）
- 版本溯源：MANIFEST.json 记录每版本目录的文件级 sha256 指纹 + 来源 records 的 hash
  （records 改了但 version 目录没重生成时，MANIFEST 的 source_records_hash 会失配 → ingest 拒收）

用法：
    conda run -n task python -m kg.build_docs --dry-run     # 预览统计，不写盘
    conda run -n task python -m kg.build_docs --strict      # 生成 data/tcm_docs/<version>/
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from config import BASE_DIR

RECORDS_DIR = BASE_DIR / "kg" / "data" / "records"
TCM_DOCS_DIR = BASE_DIR / "data" / "tcm_docs"

# 版本目录名（固定串，禁止动态时间戳——幂等红线；records 变更后手动 bump 并重跑）
VERSION = "2026-08-28_v1"

# 语料口径：同 P0（2026-08-28 用户拍板维持 2020 版）
EDITION = "《中国药典》2020 年版一部"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_herb_docs(node: dict) -> list[dict]:
    """单个 herb record → 文档条目列表（每条带 source/source_edition/hash）。"""
    hid = node["id"]
    p = node.get("profile", {})
    l1 = p.get("L1", {})
    latin = node.get("latin", "")
    prefix = f"{hid}（{latin}）" if latin else hid
    src_edition = p.get("source_edition", EDITION)
    l1_src = l1.get("来源标注", {})

    def mk(section: str, text: str, source: str) -> dict:
        return {
            "herb": hid,
            "section": section,
            "text": text,
            "source": source,
            "source_edition": src_edition,
            "hash": sha256_text(text),
        }

    docs = [
        mk("性味归经", f"{prefix}性味{p.get('性味', '')}，归{'、'.join(p.get('归经', []))}经。",
           src_edition),
        mk("功效", f"{prefix}功效：{p.get('功效', '')}。", src_edition),
        mk("主治", f"{prefix}主治：{p.get('主治', '')}。", src_edition),
        mk("用量", f"{prefix}用法用量：{p.get('用量', '')}。", src_edition),
        mk("毒性", f"{prefix}毒性：{p.get('毒性', '')}。", src_edition),
        mk("禁忌", f"{prefix}用药禁忌：{p.get('禁忌', '')}。", src_edition),
    ]
    for key, label in (("性状", "性状"), ("炮制", "炮制"), ("产地", "产地"), ("鉴别要点", "鉴别要点")):
        if l1.get(key):
            docs.append(mk(label, f"{prefix}{label}：{l1[key]}", l1_src.get(key, src_edition)))
    for s in l1.get("similar_herbs") or []:
        body = "；".join(s.get("points", []))
        docs.append(mk(
            "相似对比",
            f"{prefix}与「{s['herb']}」鉴别（{s.get('reason', '')}）：{body}",
            l1_src.get("similar_herbs", src_edition),
        ))
    return docs


def generate(docs_dir: Path) -> dict[str, list[dict]]:
    """records → {id: [文档条目]}。"""
    out = {}
    for path in sorted(Path(RECORDS_DIR).glob("*.json")):
        node = json.loads(path.read_text(encoding="utf-8"))
        docs = build_herb_docs(node)
        out[node["id"]] = docs
    return out


def main():
    ap = argparse.ArgumentParser(description="records → data/tcm_docs/ 种子语料生成器")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写盘")
    ap.add_argument("--strict", action="store_true", help="有 warning 即按错误退出")
    ap.add_argument("--version", default=VERSION, help="版本目录名（默认 VERSION 常量）")
    args = ap.parse_args()

    if not Path(RECORDS_DIR).exists():
        raise SystemExit(f"[FATAL] 找不到 records 目录：{RECORDS_DIR}")

    all_docs = generate(RECORDS_DIR)

    # ---- 统计 ----
    n_herb = len(all_docs)
    n_doc = sum(len(v) for v in all_docs.values())
    n_source = sum(1 for v in all_docs.values() for d in v if d.get("source"))
    n_hash = sum(1 for v in all_docs.values() for d in v if d.get("hash"))
    sections = {}
    for v in all_docs.values():
        for d in v:
            sections[d["section"]] = sections.get(d["section"], 0) + 1
    print(
        f"herb {n_herb} 味 / 文档 {n_doc} 条 / 带 source {n_source} / 带 hash {n_hash}"
    )
    print("分节：" + "、".join(f"{k} {v}" for k, v in sorted(sections.items())))
    if n_source != n_doc or n_hash != n_doc:
        raise SystemExit(f"[FATAL] 文档缺失 source/hash（{n_source}/{n_hash} != {n_doc}）")
    if args.strict and n_herb < 60:
        raise SystemExit(f"[FATAL] --strict：records 少于 60 味（{n_herb}），拒收")

    version_dir = TCM_DOCS_DIR / args.version / "docs"
    if args.dry_run:
        print(f"dry-run：将生成 {version_dir}（{n_doc} 条）")
        return

    # ---- 写盘 ----
    os.makedirs(version_dir, exist_ok=True)
    file_hashes = {}
    for hid, docs in sorted(all_docs.items()):
        path = version_dir / f"{hid}.json"
        payload = json.dumps(
            {"herb": hid, "edition": EDITION, "docs": docs},
            ensure_ascii=False, indent=2,
        ) + "\n"
        path.write_text(payload, encoding="utf-8")
        file_hashes[path.name] = sha256_file(path)
    # MANIFEST：版本指纹 + 来源 records 指纹（records 变更后未重生成 ⇒ source_records_hash 失配）
    records_hash = sha256_text(
        "".join(sorted(
            f"{p.name}:{sha256_file(p)}" for p in Path(RECORDS_DIR).glob("*.json")
        ))
    )
    manifest = {
        "schema_version": 1,
        "note": "由 kg/build_docs.py 从 kg/data/records/ 程序化生成，勿手改；重生成 = 重跑 build_docs",
        "current": args.version,
        "versions": [
            {
                "dir": args.version,
                "edition": EDITION,
                "doc_count": n_doc,
                "file_hashes": file_hashes,
                "source_records_hash": records_hash,
            }
        ],
    }
    (TCM_DOCS_DIR / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已写 {version_dir}（{n_doc} 条）+ MANIFEST.json")


if __name__ == "__main__":
    main()
