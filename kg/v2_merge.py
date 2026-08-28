"""
kg/v2_merge.py —— 二期 P0：records（每味一文件）→ kg.json v2 合并器。

数据流：kg/data/records/*.json + kg/data/whitelist.json + 现 kg.json(v1)
        → kg/data/kg.json (schema_version=2) + kg/data/review_ledger.json（版本台账，派生产物）

设计要点：
- stdlib-only（仅 import config 取 BASE_DIR，config 无 torch 等重依赖），打包 excludes 可排
- 幂等：kg.json/ledger 禁止动态时间戳；--check-idempotent 双跑比对 sha256
- tier=existing 的 L2 七字段与 v1 逐字段 diff（防"重构式抄写"改词，--strict 下报错）
- upgraded 的 aliases 必须 ⊇ v1（只增不删，防别名断链 → 「红参」等别名解析失效）
- herb_minor / formula / edges 从 v1 逐字透传（仅 source 清理「（待人工核对）」残留）
- 原子写：同目录临时文件 + os.replace

用法：
    conda run -n task python -m kg.v2_merge --dry-run --strict   # 预览摘要，不写盘
    conda run -n task python -m kg.v2_merge --check-idempotent   # 双跑比对 hash
    conda run -n task python -m kg.v2_merge --strict             # 落盘 kg.json v2 + review_ledger.json
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from config import BASE_DIR

RECORDS_DIR   = BASE_DIR / "kg" / "data" / "records"
MANIFEST_PATH = BASE_DIR / "kg" / "data" / "whitelist.json"
LEDGER_PATH   = BASE_DIR / "kg" / "data" / "review_ledger.json"
KG_PATH       = BASE_DIR / "kg" / "data" / "kg.json"

PENDING_RE = re.compile(r"（待人工核对）|\(待人工核对\)")

L2_KEYS = ["性味", "归经", "功效", "主治", "用量", "毒性", "禁忌"]

KNOWN_OPEN_ITEMS = [
    "香砂六君子汤出处（《古今名医方论》另说《医方集解》）待定稿",
    "甘草+海藻：歌诀有、药典【注意】实列不宜同用；本项目口径 pharmacopoeia=未收录（沿用深圳判例口径），答辩前定夺",
]

LEDGER_REVIEW_ITEMS = [
    "高危 8 味（附子/细辛/川乌/草乌/半夏/苦杏仁/桃仁/人参）的 用量/毒性/禁忌 需双人复核（本人 + 导师/评审）",
    "产地字段为 B 档教材口径，抽检 20%",
    "甘草+海藻：歌诀有、药典【注意】实列不宜同用；本项目口径 pharmacopoeia=未收录（沿用深圳判例口径），答辩前定夺",
    "香砂六君子汤出处（《古今名医方论》另说《医方集解》）待定稿",
]

# 24 味 v1 已有档案（L2 已于 2026-08-27 人工核对，见 v1 data_status）——台账 note 继承话术
REVIEWED_L2_HERBS = {
    "枸杞子", "补骨脂", "草豆蔻", "川楝子", "地肤子", "豆蔻", "覆盆子", "瓜蒌皮",
    "金樱子", "苦杏仁", "连翘", "木瓜", "砂仁", "山楂", "山茱萸", "桃仁", "乌梅",
    "五味子", "小茴香", "栀子", "菊花", "甘草", "川乌", "草乌",
}


def load_records(records_dir=RECORDS_DIR) -> dict[str, dict]:
    """读取 records 目录：{id: 节点 dict}。文件名必须与 id 一致。"""
    records = {}
    for path in sorted(Path(records_dir).glob("*.json")):
        node = json.loads(path.read_text(encoding="utf-8"))
        if node["id"] != path.stem:
            raise SystemExit(
                f"[FATAL] records/{path.name}：文件名与节点 id 不一致（{path.stem} != {node['id']}）"
            )
        if node["id"] in records:
            raise SystemExit(f"[FATAL] records 重复节点 id：{node['id']}")
        records[node["id"]] = node
    return records


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def strip_pending(text: str) -> str:
    """清理「（待人工核对）」残留（有意统一：核对状态由 meta.review_status 表达）。"""
    return PENDING_RE.sub("", text) if text else text


# v1 起草遗留的有意纠偏（diff 跳过，审计留痕）：
#  菊花/甘草：v1 缺「毒性」键（当时起草遗漏）→ v2 补全（药典均记无毒）
#  甘草禁忌：清理 v1 临时口径注记 → 正式「歌诀有、药典未认定」表述（口径 2026-08-27 已拍板）
KNOWN_L2_DRIFT = {
    "菊花": {"毒性"},
    "甘草": {"毒性", "禁忌"},
}


def diff_l2_vs_v1(record: dict, v1_node: dict) -> list[str]:
    """tier=existing：7 个扁平键与 v1 逐字段 diff，返回差异说明列表（空 = 无差异）。
    跳过 KNOWN_L2_DRIFT 中的已知纠偏键。"""
    diffs = []
    v1_p = v1_node.get("profile", {})
    p = record["profile"]
    for key in L2_KEYS:
        if key in KNOWN_L2_DRIFT.get(record["id"], set()):
            continue
        a, b = v1_p.get(key), p.get(key)
        if a != b:
            diffs.append(f"  {key}: v1={a!r} → v2={b!r}")
    return diffs


def merge(records: dict, manifest: dict, v1_data: dict) -> dict:
    """纯函数：records + manifest + v1 → kg.json v2 dict。不写盘。"""
    warnings, errors = [], []
    herbs = manifest["herbs"]
    manifest_ids = [h["id"] for h in herbs]

    # ---- 集合严格相等 ----
    missing = [h for h in manifest_ids if h not in records]
    extra = [h for h in records if h not in manifest_ids]
    if missing:
        errors.append(f"白名单有、records 缺（{len(missing)} 味未起草）：{'、'.join(missing)}")
    if extra:
        errors.append(f"records 有、白名单缺（{len(extra)} 个多余文件）：{'、'.join(extra)}")

    tier_map = {h["id"]: h["tier"] for h in herbs}

    # ---- 节点 ----
    v1_by_id = {n["id"]: n for n in v1_data["nodes"]}
    herb_nodes, minor_nodes, formula_nodes = [], [], []
    for node in v1_data["nodes"]:
        (formula_nodes if node.get("category") == "formula" else
         minor_nodes if node.get("category") in ("herb_minor",) else
         herb_nodes).append(node)

    herb_ids_in_v1 = {n["id"] for n in herb_nodes}

    # 1) v1 有 herb 节点、records 无 → error（不允许静默丢档案）
    for hid in herb_ids_in_v1:
        if hid not in records:
            errors.append(f"v1 现有档案药「{hid}」在 records 中缺失（不允许静默丢档案）")

    # 2) existing：L2 逐字段 diff
    for h in herbs:
        if h["tier"] != "existing":
            continue
        if h["id"] not in records:
            continue  # missing 已在集合校验报错，避免 KeyError
        v1_node = v1_by_id.get(h["id"])
        if v1_node is None:
            errors.append(f"tier=existing 但 v1 无该 herb 节点：{h['id']}")
            continue
        diffs = diff_l2_vs_v1(records[h["id"]], v1_node)
        if diffs:
            msg = f"tier=existing L2 与 v1 有差异（防抄写漂移）：{h['id']}\n" + "\n".join(diffs)
            errors.append(msg)

    # 3) upgraded：aliases ⊇ v1
    for h in herbs:
        if h["tier"] != "upgraded":
            continue
        if h["id"] not in records:
            continue  # missing 已在集合校验报错
        v1_node = v1_by_id.get(h["id"])
        if v1_node is None:
            errors.append(f"tier=upgraded 但 v1 无该节点（{h['id']}）——白名单 tier 标错或节点不存在")
            continue
        v1_aliases = set(v1_node.get("aliases", []))
        rec_aliases = set(records[h["id"]].get("aliases", []))
        lost = v1_aliases - rec_aliases
        if lost:
            errors.append(f"upgraded「{h['id']}」别名缺失（只增不删）：{'、'.join(sorted(lost))}")

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "data": None}

    # ---- 组装 nodes：herb(按白名单) → minor(v1 序) → formula(v1 序) ----
    manifest_id_set = set(manifest_ids)
    nodes = []
    nodes += [records[hid] for hid in manifest_ids]  # herb 全部来自 records
    for node in minor_nodes:
        if node["id"] in manifest_id_set:
            continue  # upgraded 26 味已从 minor 升级为 herb（由 records 生成），跳过防重复
        node = dict(node)
        if node.get("source"):
            node["source"] = strip_pending(node["source"])
        nodes.append(node)
    for node in formula_nodes:
        node = dict(node)
        if node.get("source"):
            node["source"] = strip_pending(node["source"])
        nodes.append(node)

    # ---- edges 逐字透传 ----
    edges = v1_data.get("edges", [])

    data = {
        "schema_version": 2,
        "data_status": {
            "generator": "kg/v2_merge.py（从 kg/data/records/ 程序化生成，勿手改 herb 节点）",
            "review_hint": "逐味核对状态以 kg/data/review_ledger.json 为准；records 为唯一事实来源",
            "whitelist": "kg/data/whitelist.json（64 味）",
            "known_open_items": list(KNOWN_OPEN_ITEMS),
        },
        "nodes": nodes,
        "edges": edges,
    }
    return {"ok": True, "errors": [], "warnings": warnings, "data": data}


def build_ledger(records: dict, manifest: dict, old_ledger: dict) -> tuple[dict, list[str]]:
    """从 records meta 生成/增量更新版本台账（追加式历史，幂等）。"""
    warnings = []
    old_entries = old_ledger.get("entries", [])
    by_herb: dict[str, list[dict]] = {}
    for e in old_entries:
        by_herb.setdefault(e["herb_id"], []).append(e)

    new_entries = []
    for h in manifest["herbs"]:
        hid = h["id"]
        record = records[hid]
        meta = record["meta"]
        if meta["review_status"] == "reviewed":
            note = f"已人工核对（{meta.get('reviewed_by', '')}，{meta.get('review_date', '')}）"
        elif hid in REVIEWED_L2_HERBS:
            note = "L2 药效字段已于 2026-08-27 人工核对（v1 data_status 核定）；本版本新增 L1（draft）"
        else:
            note = "首稿（LLM 起草，待人工核对）"
        row = {
            "herb_id": hid,
            "name_cn": record["name_cn"],
            "version": meta["version"],
            "review_status": meta["review_status"],
            "reviewed_by": meta.get("reviewed_by", ""),
            "review_date": meta.get("review_date", ""),
            "provenance_level": meta.get("provenance_level", ""),
            "updated": meta.get("updated", ""),
            "note": note,
        }
        hist = by_herb.get(hid, [])
        if not hist:
            new_entries.append(row)
        elif hist[-1]["version"] == row["version"]:
            hist[-1] = row          # 同版本 → 同步更新最后一行
            new_entries.extend(hist)
        elif hist[-1]["version"] < row["version"]:
            hist.append(row)        # 新版本 → 追加历史行
            new_entries.extend(hist)
        else:
            warnings.append(f"台账版本高于 records（{hid}：ledger {hist[-1]['version']} > records {row['version']}），已同步最后一行")
            hist[-1] = row
            new_entries.extend(hist)

    ledger = {
        "schema_version": 1,
        "note": "由 kg/v2_merge.py --sync-ledger 从 records meta 生成；追加式历史，勿手改",
        "entries": new_entries,
        "review_items": list(LEDGER_REVIEW_ITEMS),
    }
    return ledger, warnings


def dump_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser(description="records → kg.json v2 合并器")
    ap.add_argument("--dry-run", action="store_true", help="只打印摘要，不写盘")
    ap.add_argument("--strict", action="store_true", help="有 warning 即按错误退出（exit 1）")
    ap.add_argument("--v1", default=str(KG_PATH), help="v1 输入 kg.json 路径")
    ap.add_argument("--out", default=str(KG_PATH), help="v2 输出 kg.json 路径")
    ap.add_argument("--no-ledger", action="store_true", help="不生成 review_ledger.json")
    ap.add_argument("--check-idempotent", action="store_true", help="dry-run 双跑比对产物 sha256")
    ap.add_argument("--json", action="store_true", help="摘要以 JSON 输出")
    args = ap.parse_args()

    records = load_records()
    manifest = load_manifest()
    v1_data = json.loads(Path(args.v1).read_text(encoding="utf-8"))

    result = merge(records, manifest, v1_data)
    if not result["ok"]:
        for e in result["errors"]:
            print(f"[ERROR] {e}", file=sys.stderr)
        raise SystemExit(2)

    data = result["data"]
    data_json = dump_json(data)

    ledger, ledger_warns = build_ledger(records, manifest, json.loads(
        LEDGER_PATH.read_text(encoding="utf-8") if LEDGER_PATH.exists() else "{}"
    ))
    ledger_json = dump_json(ledger)

    # 摘要
    counts = {"herb": 0, "herb_minor": 0, "formula": 0}
    for n in data["nodes"]:
        counts[n["category"]] = counts.get(n["category"], 0) + 1
    summary = {
        "schema_version": data["schema_version"],
        "herb": counts["herb"],
        "herb_minor": counts["herb_minor"],
        "formula": counts["formula"],
        "edges": len(data["edges"]),
        "ledger_entries": len(ledger["entries"]),
        "warnings": result["warnings"] + ledger_warns,
        "sha256": sha256_of(data_json + ledger_json),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"nodes: {sum(counts.values())} (herb {counts['herb']} / "
            f"herb_minor {counts['herb_minor']} / formula {counts['formula']}), "
            f"edges: {len(data['edges'])}, ledger: {len(ledger['entries'])} 行"
        )
    for w in result["warnings"] + ledger_warns:
        print(f"[WARN] {w}", file=sys.stderr)

    if args.check_idempotent:
        records2 = load_records()          # 重新加载（模拟二次运行）
        result2 = merge(records2, manifest, v1_data)
        assert result2["ok"]
        data2 = result2["data"]
        ledger2, _ = build_ledger(records2, manifest, ledger)
        json2 = dump_json(data2) + dump_json(ledger2)
        same = sha256_of(data_json + ledger_json) == sha256_of(json2)
        print(f"idempotent: {'OK（二次运行产物一致）' if same else 'FAIL（产物漂移）'}")
        if not same:
            raise SystemExit(2)

    if result["warnings"] or ledger_warns:
        if args.strict:
            print("[ERROR] --strict：存在 warning，拒绝落盘", file=sys.stderr)
            raise SystemExit(1)

    if not args.dry_run:
        atomic_write(Path(args.out), data_json)
        if not args.no_ledger:
            atomic_write(LEDGER_PATH, ledger_json)
        print(f"已写 {args.out}" + ("" if args.no_ledger else f" + {LEDGER_PATH}"))
    else:
        print("dry-run：未写盘")


if __name__ == "__main__":
    main()
