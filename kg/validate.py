"""
kg/validate.py —— 二期 P0 校验脚本（验收口径 12.1 的脚本统计）。

两种模式：
  --records      只验 kg/data/records/ 目录（起草期每批跑，不依赖 kg.json 存在）
  默认          验证合并后的 kg.json v2 + records + whitelist + review_ledger 四方一致性

统计输出（验收口径 12.1）：
  档案药数 / L2 覆盖(7×64) / L1 覆盖(4×64，扣除 l1_exemptions) / 相似对（9 对双侧互录）
  source 带全率 / 「待人工核对」残留 / ledger 一致性

设计要点：
- stdlib-only（import config 仅取 BASE_DIR）
- 别名索引独立实现（不 import builder，避免模块级缓存污染）
- records↔kg.json herb 节点 deep-equal（防"改了 records 忘 merge"分裂脑）
- 跨 record L1 文本 ≥40 字公共子串告警（防 LLM 起草串味/模板化）

用法：
    conda run -n task python -m kg.validate --records --strict     # 起草期每批
    conda run -n task python -m kg.validate --strict               # 合并后全量
"""
import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from config import BASE_DIR

RECORDS_DIR   = BASE_DIR / "kg" / "data" / "records"
MANIFEST_PATH = BASE_DIR / "kg" / "data" / "whitelist.json"
LEDGER_PATH   = BASE_DIR / "kg" / "data" / "review_ledger.json"
KG_PATH       = BASE_DIR / "kg" / "data" / "kg.json"

PENDING_RE = re.compile(r"（待人工核对）|\(待人工核对\)")

L2_KEYS   = ["性味", "归经", "功效", "主治", "用量", "毒性", "禁忌"]
L1_TEXT_KEYS = ["性状", "炮制", "产地", "鉴别要点"]
META_KEYS = ["version", "updated", "review_status", "reviewed_by", "review_date", "provenance_level"]
VALID_REVIEW_STATUS = {"draft", "reviewed"}
VALID_CATEGORIES = {"herb", "herb_minor", "formula"}
SOURCE_EDITION_ALLOWED = {"《中国药典》2020 年版一部"}
TEXT_DUP_THRESHOLD = 40  # 跨 record 文本公共子串 ≥40 字 → 告警（串味）
# 相似对 reason 禁区：相似 ≠ 药效同等（NFR 红线），禁止"功效相近"类表述
FORBIDDEN_REASON = re.compile(r"功效相近|功效类似|疗效相近|药效相近|作用相近")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.counters = {}

    def add_error(self, msg):
        self.errors.append(msg)

    def add_warning(self, msg):
        self.warnings.append(msg)

    def ok(self):
        return not self.errors


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_records() -> dict[str, dict]:
    records = {}
    for path in sorted(Path(RECORDS_DIR).glob("*.json")):
        node = json.loads(path.read_text(encoding="utf-8"))
        if node["id"] != path.stem:
            raise SystemExit(f"[FATAL] records/{path.name}：文件名与 id 不一致")
        records[node["id"]] = node
    return records


def build_alias_index(nodes: list[dict]) -> dict[str, str]:
    """别名/中文名 → 节点 id（与 builder.py 同语义：首次出现优先）。"""
    index = {}
    for node in nodes:
        index.setdefault(node["id"], node["id"])
        for alias in node.get("aliases", []):
            index.setdefault(alias, node["id"])
    return index


# ---------------- records 校验（--records 模式 / 全量模式共用） ----------------

def validate_records(records: dict, manifest: dict) -> Report:
    rep = Report()
    herbs = {h["id"]: h for h in manifest["herbs"]}
    exemptions = manifest.get("l1_exemptions", {})
    required_pairs = [tuple(p) for p in manifest.get("required_similar_pairs", [])]

    # 集合一致性：起草期"白名单有、records 缺"是正常态（warning）；
    # 全量模式由 validate_kg 的 herb 集合一致检查（error）把关
    missing = [h for h in herbs if h not in records]
    extra = [h for h in records if h not in herbs]
    if missing:
        rep.add_warning(f"白名单有、records 缺（{len(missing)} 味，起草期正常态）：{'、'.join(missing)}")
    if extra:
        rep.add_error(f"records 有、白名单缺（多余文件）：{'、'.join(extra)}")

    for hid, rec in records.items():
        if hid not in herbs:
            continue  # 多余文件错误已报，不再细查

        # shape
        for key in ("id", "category", "name_cn", "latin", "source", "meta", "profile"):
            if key not in rec:
                rep.add_error(f"{hid}：缺字段 {key}")
                continue
        if rec.get("category") != "herb":
            rep.add_error(f"{hid}：records 只承载 herb，实际 category={rec.get('category')}")
        if PENDING_RE.search(rec.get("source", "")):
            rep.add_error(f"{hid}：source 残留「（待人工核对）」（核对状态由 meta.review_status 表达）")

        # meta
        meta = rec.get("meta", {})
        for key in META_KEYS:
            if key not in meta:
                rep.add_error(f"{hid}：meta 缺字段 {key}")
        if meta.get("review_status") not in VALID_REVIEW_STATUS:
            rep.add_error(f"{hid}：meta.review_status 非法：{meta.get('review_status')}")
        if meta.get("review_status") == "reviewed":
            if not meta.get("reviewed_by") or not meta.get("review_date"):
                rep.add_error(f"{hid}：reviewed 必须带 reviewed_by/review_date")
        if not isinstance(meta.get("version"), int) or meta["version"] < 1:
            rep.add_error(f"{hid}：meta.version 非法：{meta.get('version')}")

        # profile 7 扁平键 + source_edition
        p = rec.get("profile", {})
        for key in L2_KEYS:
            if key not in p or p[key] in (None, "", []):
                rep.add_error(f"{hid}：profile 缺/空 {key}")
        if p.get("source_edition") not in SOURCE_EDITION_ALLOWED:
            rep.add_error(f"{hid}：source_edition 非法：{p.get('source_edition')}（允许：{SOURCE_EDITION_ALLOWED}）")

        # L1
        l1 = p.get("L1")
        if not isinstance(l1, dict):
            rep.add_error(f"{hid}：profile.L1 缺失（schema v2 必须）")
            continue
        exempt = set(exemptions.get(hid, []))
        for key in L1_TEXT_KEYS:
            if key in exempt:
                continue
            if not l1.get(key):
                rep.add_error(f"{hid}：L1.{key} 为空（豁免登记于 whitelist.l1_exemptions）")
        src_map = l1.get("来源标注", {})
        for key in L1_TEXT_KEYS + ["similar_herbs"]:
            if key in exempt:
                continue
            if l1.get(key) and key not in src_map:
                rep.add_error(f"{hid}：L1.{key} 有内容但「来源标注」缺该项")
        for key, val in src_map.items():
            if not ("（" in val and "档）" in val):
                rep.add_warning(f"{hid}：L1.来源标注.{key} 格式应为「书名·条目（档位）」：{val}")

        # similar_herbs 结构
        sims = l1.get("similar_herbs", [])
        if not isinstance(sims, list):
            rep.add_error(f"{hid}：L1.similar_herbs 必须是数组")
            sims = []
        for i, s in enumerate(sims):
            if s.get("herb") not in herbs:
                rep.add_error(f"{hid}：similar_herbs[{i}].herb 不在白名单：{s.get('herb')}")
            if FORBIDDEN_REASON.search(s.get("reason", "")):
                rep.add_error(f"{hid}：similar_herbs[{i}] reason 含功效类表述（红线：相似≠药效同等）：{s.get('reason')}")
            pts = s.get("points", [])
            if len(pts) < 2:
                rep.add_error(f"{hid}：similar_herbs[{i}] points 至少 2 条（本品/对比药对称格式）")
            elif not (pts[0].startswith("本品") and (pts[1].startswith("对比药") or pts[1].startswith("对方"))):
                rep.add_error(f"{hid}：similar_herbs[{i}] points 首条须「本品…」、次条「对比药…」")

    # required_similar_pairs 双侧互录
    for a, b in required_pairs:
        ra, rb = records.get(a), records.get(b)
        if ra is None or rb is None:
            continue  # 缺味已报错
        sims_a = {(s["herb"]) for s in ra["profile"]["L1"].get("similar_herbs", [])}
        sims_b = {(s["herb"]) for s in rb["profile"]["L1"].get("similar_herbs", [])}
        if b not in sims_a:
            rep.add_error(f"相似对 {a}↔{b}：{a} 未互录 {b}")
        if a not in sims_b:
            rep.add_error(f"相似对 {a}↔{b}：{b} 未互录 {a}")

    # 跨 record 文本重复（串味告警）
    texts = {hid: {} for hid in records}
    for hid, rec in records.items():
        l1 = rec.get("profile", {}).get("L1", {})
        for key in L1_TEXT_KEYS:
            texts[hid][key] = l1.get(key, "")
    for i, (hid_a, ha) in enumerate(texts.items()):
        for hid_b, hb in list(texts.items())[i + 1:]:
            for key in L1_TEXT_KEYS:
                ta, tb = ha[key], hb[key]
                if not ta or not tb:
                    continue
                m = SequenceMatcher(None, ta, tb).find_longest_match(0, len(ta), 0, len(tb))
                if m.size >= TEXT_DUP_THRESHOLD:
                    rep.add_warning(
                        f"串味嫌疑：{hid_a} 与 {hid_b} 的 L1.{key} 公共子串 {m.size} 字："
                        f"「{ta[m.a:m.a + m.size]}」"
                    )

    # 覆盖率统计（records 视角）
    n = len(records)
    l2_total, l2_hit = n * len(L2_KEYS), 0
    l1_total, l1_hit = 0, 0
    sim_total, sim_hit = n, 0
    src_total, src_hit = n, 0
    for hid, rec in records.items():
        p = rec.get("profile", {})
        for key in L2_KEYS:
            if p.get(key) not in (None, "", []):
                l2_hit += 1
        l1 = p.get("L1", {})
        exempt = set(exemptions.get(hid, []))
        for key in L1_TEXT_KEYS:
            if key in exempt:
                continue
            l1_total += 1
            if l1.get(key):
                l1_hit += 1
        if l1.get("similar_herbs"):
            sim_hit += 1
        if rec.get("source"):
            src_hit += 1
    rep.counters.update({
        "records": n,
        "l2": f"{l2_hit}/{l2_total}",
        "l1": f"{l1_hit}/{l1_total}",
        "similar": f"{sim_hit}/{sim_total}",
        "source": f"{src_hit}/{src_total}",
        "required_pairs": f"{len(required_pairs)}",
    })
    return rep


# ---------------- 全量校验（合并后 kg.json v2） ----------------

def validate_kg(data: dict, records: dict, manifest: dict, ledger: dict) -> Report:
    rep = Report()
    nodes = data["nodes"]
    herbs = [h["id"] for h in manifest["herbs"]]
    required_pairs = [tuple(p) for p in manifest.get("required_similar_pairs", [])]

    if data.get("schema_version") != 2:
        rep.add_error(f"schema_version != 2：{data.get('schema_version')}")

    # id 唯一 + category 域 + 数量
    ids = [n["id"] for n in nodes]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        rep.add_error(f"节点 id 重复：{'、'.join(sorted(dup))}")
    cats = {}
    for n in nodes:
        if n.get("category") not in VALID_CATEGORIES:
            rep.add_error(f"节点 {n.get('id')} category 非法：{n.get('category')}")
        cats[n["category"]] = cats.get(n["category"], 0) + 1
    herb_in_kg = {n["id"] for n in nodes if n["category"] == "herb"}
    if set(herbs) != herb_in_kg:
        rep.add_error(f"kg.json herb 集合与白名单不一致：缺 {'、'.join(set(herbs) - herb_in_kg)}，"
                      f"多 {'、'.join(herb_in_kg - set(herbs))}")
    if cats.get("herb_minor", 0) != 22:
        rep.add_warning(f"herb_minor 数量 {cats.get('herb_minor', 0)} != 22（白名单调整后需同步 v2_merge 预期）")

    # 别名全局唯一性
    index = {}
    for n in nodes:
        index.setdefault(n["id"], []).append(n["id"])
        for alias in n.get("aliases", []):
            index.setdefault(alias, []).append(n["id"])
    conflict = {k: v for k, v in index.items() if len(v) > 1 and len(set(v)) > 1}
    for alias, owners in conflict.items():
        rep.add_error(f"别名撞名：「{alias}」→ {'、'.join(sorted(set(owners)))}")

    # records ↔ kg.json herb 节点 deep-equal（防分裂脑）
    for hid in herbs:
        rec = records.get(hid)
        kg_node = next((n for n in nodes if n["id"] == hid), None)
        if rec is None or kg_node is None:
            continue
        if rec != kg_node:
            rep.add_error(f"records/{hid}.json 与 kg.json 节点不一致（改了 records 忘 merge？）")

    # 边：端点可解析 + 禁忌双向成对
    node_ids = set(ids)
    alias_index = build_alias_index(nodes)
    for i, e in enumerate(data.get("edges", [])):
        if e["type"] == "组成":
            frm, herb = alias_index.get(e["formula"]), alias_index.get(e["herb"])
            if frm is None or frm not in node_ids:
                rep.add_error(f"edges[{i}] 组成边 formula 端点不可解析：{e['formula']}")
            if herb is None or herb not in node_ids:
                rep.add_error(f"edges[{i}] 组成边 herb 端点不可解析：{e['herb']}")
        elif e["type"] == "禁忌":
            a, b = alias_index.get(e["herb_a"]), alias_index.get(e["herb_b"])
            if a is None or a not in node_ids:
                rep.add_error(f"edges[{i}] 禁忌边端点不可解析：{e['herb_a']}")
            if b is None or b not in node_ids:
                rep.add_error(f"edges[{i}] 禁忌边端点不可解析：{e['herb_b']}")
            if e.get("verse") not in ("十八反", "十九畏"):
                rep.add_error(f"edges[{i}] verse 非法：{e.get('verse')}")
        else:
            rep.add_error(f"edges[{i}] type 非法：{e['type']}")

    # ledger 一致性
    if ledger:
        ledger_entries = {e["herb_id"]: e for e in ledger.get("entries", [])}
        for hid in herbs:
            rec = records.get(hid)
            le = ledger_entries.get(hid)
            if rec is None:
                continue
            if le is None:
                rep.add_error(f"ledger 缺 {hid} 的行（跑 v2_merge 重新生成）")
                continue
            if le.get("version") != rec["meta"].get("version"):
                rep.add_error(f"ledger/{hid} 版本 {le.get('version')} != records {rec['meta'].get('version')}")
            if le.get("review_status") == "reviewed" and not (le.get("reviewed_by") and le.get("review_date")):
                rep.add_error(f"ledger/{hid}：reviewed 但缺 reviewed_by/review_date")

    # 残留扫描（所有节点 source + profile 全文本）
    for n in nodes:
        if PENDING_RE.search(n.get("source", "")):
            rep.add_error(f"节点 {n['id']} source 残留「（待人工核对）」")
        p = n.get("profile", {})
        for key in L2_KEYS:
            if PENDING_RE.search(str(p.get(key, ""))):
                rep.add_error(f"节点 {n['id']} profile.{key} 残留「（待人工核对）」")

    # 覆盖率（以 records 为准，与 --records 模式同统计）
    r_rep = validate_records(records, manifest)
    rep.errors.extend(r_rep.errors)
    rep.warnings.extend(r_rep.warnings)
    rep.counters.update(r_rep.counters)

    # 图规模
    rep.counters["nodes"] = f"{len(nodes)} (herb {cats.get('herb', 0)} / minor {cats.get('herb_minor', 0)} / formula {cats.get('formula', 0)})"
    rep.counters["edges"] = str(len(data.get("edges", [])))
    return rep


def print_report(rep: Report) -> None:
    c = rep.counters
    lines = [
        f"档案药数:      {c.get('records', '?')} 味（白名单 {c.get('expected', '?')}）",
        f"L2 覆盖(7字段): {c.get('l2', '?')}",
        f"L1 覆盖(4字段): {c.get('l1', '?')}",
        f"similar_herbs:  {c.get('similar', '?')} 味含相似对；必需对 {c.get('required_pairs', '?')}",
        f"source 带全率:  {c.get('source', '?')}",
        f"nodes/edges:    {c.get('nodes', '?')} / {c.get('edges', '?')}",
    ]
    print("\n".join(lines))
    for w in rep.warnings:
        print(f"[WARN] {w}")
    for e in rep.errors:
        print(f"[ERROR] {e}")
    print(f"ERRORS: {len(rep.errors)}  WARNINGS: {len(rep.warnings)}")


def main():
    ap = argparse.ArgumentParser(description="二期 P0 kg 数据校验（验收口径 12.1）")
    ap.add_argument("--records", action="store_true", help="只验 records 目录（起草期用）")
    ap.add_argument("--strict", action="store_true", help="有 warning 也按错误退出（exit 1）")
    ap.add_argument("--kg", default=str(KG_PATH), help="kg.json 路径（默认 kg/data/kg.json）")
    args = ap.parse_args()

    manifest = load_json(MANIFEST_PATH)
    records = load_records()

    if args.records:
        rep = validate_records(records, manifest)
        rep.counters["expected"] = manifest.get("expected_herb_count", len(manifest["herbs"]))
    else:
        data = load_json(Path(args.kg))
        ledger = load_json(LEDGER_PATH) if LEDGER_PATH.exists() else {}
        rep = validate_kg(data, records, manifest, ledger)
        rep.counters["expected"] = manifest.get("expected_herb_count", len(manifest["herbs"]))

    print_report(rep)
    if not rep.ok():
        raise SystemExit(2)
    if rep.warnings and args.strict:
        print("[ERROR] --strict：存在 warning", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
