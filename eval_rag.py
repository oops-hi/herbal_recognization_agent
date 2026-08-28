"""
eval_rag.py —— 二期 P1 验收：混合检索评测（方案 §5.3「eval_rag.py：20 问 top-5 命中 ≥ 0.9」）。

评测口径：
- 组 A（16 问）开放问句 → kg.retrieval.search top-5，期望药材（或替代药材）命中即通过
  （纯向量召回；问句刻意避开纯药名，测语义而非词面）
- 组 B（4 问）混合链路 → kg.query.retrieve_doc 输出包含期望药材名即通过
  （图谱精确路径 2 问 + 含药名开放问句 2 问，测「图谱给准确，RAG 给全面」整体行为）
- 命中率 = 通过 / 20，≥ 0.9（≥18）exit 0，否则 exit 1

用法：
    conda run -n task python eval_rag.py            # 全量评测（需要 kg_rag/ 已入库）
    conda run -n task python eval_rag.py --verbose  # 每题打印召回详情
"""
import argparse

from kg import query as kgq
from kg import retrieval

PASS_RATE = 0.9

# 组 A：开放问句（纯向量召回，top-5 命中判定）
CASES_VEC = [
    ("眼睛干涩，泡什么茶喝比较好", {"枸杞子", "菊花"}),
    ("哪些药材能清热解毒", {"金银花", "黄连", "连翘", "栀子"}),
    ("气虚乏力用什么药补气", {"黄芪", "人参", "白术", "党参"}),
    ("晚上失眠睡不好，什么药安神", {"酸枣仁", "百合", "莲子"}),
    ("大便干结，什么药润肠通便", {"决明子", "桃仁", "郁李仁", "核桃仁"}),
    ("脾胃虚弱、食欲不振用什么", {"白术", "山药", "茯苓", "薏苡仁", "黄芪", "大枣"}),
    ("咳嗽痰多，化痰用什么药", {"桔梗", "半夏", "瓜蒌皮"}),
    ("跌打损伤，什么药活血化瘀", {"桃仁", "川楝子", "当归"}),
    ("哪些药孕妇禁用", {"附子", "半夏", "桃仁", "苦杏仁"}),
    ("哪种药材有毒，需要谨慎使用", {"附子", "川乌", "草乌", "半夏", "细辛"}),
    ("感冒发热用什么药", {"金银花", "连翘", "薄荷", "牛蒡子", "菊花"}),
    ("山楂和金樱子容易混淆，怎么鉴别", {"山楂", "金樱子"}),
    ("宁夏出产的道地药材是什么", {"枸杞子"}),
    ("血虚面色萎黄，什么药补血", {"当归", "熟地黄", "龙眼肉"}),
    ("上火了，泡什么喝降火", {"菊花", "栀子", "金银花", "决明子", "黄连"}),
    ("遗精滑精、小便不禁用什么药", {"金樱子", "山茱萸", "覆盆子", "五味子"}),
]

# 组 B：混合链路（图谱精确优先 → 向量召回）
CASES_MIXED = [
    ("枸杞", {"枸杞子"}, "图谱精确路径（别名解析）"),
    ("决明子", {"决明子"}, "图谱精确路径（收录药名）"),
    ("黄芪泡水喝有什么好处", {"黄芪"}, "含药名开放问句 → 向量召回"),
    ("瓜蒌皮和川乌能一起用吗", {"瓜蒌皮", "川乌"}, "多药名问句 → 向量召回"),
]


def hit_in_topk(hits, expected) -> str | None:
    """top-5 内是否命中期望药材；返回命中药名或 None。"""
    for h in hits or []:
        if h["herb"] in expected:
            return h["herb"]
    return None


def main():
    ap = argparse.ArgumentParser(description="混合检索评测（20 问 top-5 命中 ≥ 0.9）")
    ap.add_argument("--verbose", action="store_true", help="每题打印召回详情")
    args = ap.parse_args()

    total = passed = 0
    rows = []

    print("== 组 A：向量召回（16 问）==")
    for q, expected in CASES_VEC:
        total += 1
        hits = retrieval.search(q, top_k=5)
        hit = hit_in_topk(hits, expected)
        ok = hit is not None
        passed += ok
        rows.append((q, expected, hit, ok, hits))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {q}  → 期望 {'/'.join(sorted(expected))} | "
              f"召回 {'、'.join(h['herb'] for h in (hits or []))}")
        if args.verbose:
            for h in (hits or [])[:3]:
                print(f"        {h['score']:.3f} {h['herb']}·{h['section']}")

    print("\n== 组 B：混合链路（4 问）==")
    for q, expected, note in CASES_MIXED:
        total += 1
        out = kgq.retrieve_doc(q, top_k=5)
        hit = next((e for e in expected if e in out), None)
        ok = hit is not None
        passed += ok
        rows.append((q, expected, hit, ok, out))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {q}（{note}）  → 期望 {'/'.join(sorted(expected))} | "
              f"输出含「{hit or '—'}」")

    rate = passed / total
    print(f"\n== 结果：{passed}/{total} 通过（命中率 {rate:.1%}，要求 ≥{PASS_RATE:.0%}）==")
    for q, expected, hit, ok, _ in rows:
        if not ok:
            print(f"  未通过：{q}（期望 {'/'.join(sorted(expected))}）")
    if rate < PASS_RATE:
        print(f"[FAIL] 命中率未达 {PASS_RATE:.0%}，验收不通过")
        raise SystemExit(1)
    print("[OK] 验收通过")


if __name__ == "__main__":
    main()
