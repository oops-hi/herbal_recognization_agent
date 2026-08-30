"""
eval_teach.py —— STUDY 讲解子 Agent 验收：Router 判定 + 讲解库召回 + 端到端合规。

评测口径（对齐 eval_rag.py / eval_safety.py 断言模式）：
- 组 A（Router，0 API）：10 正例 must == 讲解 + 6 负例 must != 讲解
  （负例是「什么药」前缀抢单回归探针：剂量/方剂/安全/学习/鉴别/孕妇主题不得路由讲解）
- 组 B（讲解库向量召回）：教学问句 → kg.teach.teach_query（向量路径）top-5 命中期望药材
  （用例按 data/teach_src 定稿实际内容编写后冻结——先写骨架，内容冻结时校准）
- 组 C（--llm，需 DEEPSEEK_API_KEY）：3 例 core.run 端到端断言 status ok、evidence 含出处、
  古籍对象例答案含 quote 原文且不出现自编引文（「未见可靠原文」或原文摘录行必须来自工具）

用法：
    conda run -n task python eval_teach.py             # 组 A + 组 B（无需联网）
    conda run -n task python eval_teach.py --llm       # 加组 C（需密钥 + teach_rag 已入库）
"""
import argparse
import re
import sys

from agent import core
from kg import teach as kteach

PASS_RATE = 0.9

# ---------- 组 A：Router 判定（无 API） ----------
ROUTER_POS = [
    "讲讲苦杏仁",
    "讲一讲枸杞子",
    "我想了解一下五味子",
    "科普一下山楂",
    "讲讲枸杞子和五味子的区别",   # 讲解优先于鉴别（工具集含 similar_compare 仍可答对比）
    "什么药润肺",                 # 教科书式功效问句 → 讲解
    "哪些药能明目",
    "学一下砂仁怎么鉴别",
    "老师，讲讲桃仁和苦杏仁",
    "备考：补骨脂的功效",
]
ROUTER_NEG = [
    "枸杞子一天吃多少合适？",      # 剂量 → 药性
    "每次泡水放几克枸杞子？",      # 剂量 → 药性
    "人参和五灵脂能一起吃吗？",    # 安全
    "六味地黄丸治什么？",          # 方剂
    "这个不是枸杞子是五味子",      # 学习
    "苦杏仁和桃仁怎么区分",        # 鉴别（无讲解词，走鉴别）
    "孕妇能吃桃仁吗？",            # 人群 → 药性（同 eval_safety 既有口径）
    "吃什么药治咳嗽",              # 方剂（「什么药」前缀但命中 FORMULA_KW）
]

# ---------- 组 B：讲解库向量召回（教学问句，组 A 判定为讲解的问法） ----------
# 期望按 20 味讲解课文定稿内容校准后冻结；先给目标药名（课文各节都会含该药名）
TEACH_VEC_CASES = [
    ("什么药润肺止咳", {"苦杏仁", "川贝母"}),
    ("哪些药可以滋补肝肾明目", {"枸杞子"}),
    ("什么药开胃消食化积", {"山楂"}),
    ("补阳温肾的果实类药材", {"补骨脂"}),
    ("收涩固精缩尿的药材", {"金樱子", "覆盆子", "山茱萸"}),
    ("生津止渴敛肺的药材", {"五味子", "乌梅"}),
    ("行气止痛理疝气的药材", {"川楝子", "小茴香"}),
    ("润肠通便的种子类药材", {"桃仁", "苦杏仁"}),
    ("清热泻火除烦的药材", {"栀子"}),
    ("果实种子类的化湿药材", {"砂仁", "豆蔻", "草豆蔻"}),
]

# ---------- 组 C：端到端（--llm） ----------
E2E_CASES = [
    ("讲讲枸杞子", {"枸杞子"}, "整节课文：应含【本草记载】与档位标注"),
    ("讲讲苦杏仁", {"苦杏仁"}, "整节课文：古籍原文摘录行（quote）"),
    ("什么药能润肺", {"苦杏仁", "川贝母"}, "开放问题向量召回"),
]


def run_group_a() -> int:
    print("== 组 A：Router 判定（10 正例 + 8 负例，无 API）==")
    total = passed = 0
    for q in ROUTER_POS:
        total += 1
        ok = core.classify_agent(q) == "讲解"
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] 正例 {q}  → {core.classify_agent(q)}")
    for q in ROUTER_NEG:
        total += 1
        got = core.classify_agent(q)
        ok = got != "讲解"
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] 负例 {q}  → {got}（须非讲解）")
    print(f"  组 A：{passed}/{total}\n")
    return passed, total


def run_group_b() -> tuple[int, int]:
    print("== 组 B：讲解库向量召回（教学问句 → teach_query 输出含期望药名）==")
    total = passed = 0
    for q, expected in TEACH_VEC_CASES:
        total += 1
        out = kteach.teach_query(q, top_k=5)
        # ⚠️ 降级/缺口文案也含药名（如「讲讲枸杞子」示例）→ 出现这些标记一律 FAIL
        degraded = ("暂不可用" in out) or ("知识缺口" in out) or ("知识库未收录" in out)
        hit = None if degraded else next((e for e in expected if e in out), None)
        ok = hit is not None
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {q}  → 期望 {'/'.join(sorted(expected))} | 输出含「{hit or '—'}」")
        if not ok:
            print(f"        （{'检索降级/知识缺口' if degraded else '未命中'}：{out[:80]}…）")
    print(f"  组 B：{passed}/{total}\n")
    return passed, total


def run_group_c() -> tuple[int, int]:
    if not core.is_configured():
        raise SystemExit("[错误] 未配置 DEEPSEEK_API_KEY，组 C 端到端需要真实调用大模型。")
    print("== 组 C：端到端（--llm，讲解 Agent 真实调用链）==")
    total = passed = 0
    for q, expected, note in E2E_CASES:
        total += 1
        r = core.run(q, [], force_agent="讲解")
        answer = r.get("answer", "")
        ev = [e.get("source", "") for e in r.get("evidence", []) or []]
        hit = next((e for e in expected if e in answer), None)
        ok = (r["status"] == "ok") and hit is not None and len(ev) > 0
        # 古籍引文边界：若答案涉及「本草记载」节，其古文必须出自工具给到的 quote（逐字转述）。
        # trace 里工具 result 被截 200 字，够不到第 6/8 节的原文摘录行 → 直接从讲解库重放取 quote
        #（与模型调用同源）。模型可自由排版（引用块/直引/「原文摘录：」行），断言只校验
        # 「quote 开头片段被原样转述」——模型若自编引文，开头片段必然不在 quote 前缀列表里 → FAIL。
        if "本草记载" in answer:
            tool_quotes = re.findall(r"原文摘录：(.{6,40})", kteach.teach_query(q, top_k=5))
            if not tool_quotes:
                ok = False
                print("        （讲解库重放未返回原文摘录行）")
            elif not any(qp[:10] in answer for qp in tool_quotes):
                ok = False
                print(f"        （答案含古籍引文但非工具 quote 原文，疑似自编：{tool_quotes[:2]}）")
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {q}（{note}）→ status={r['status']} 含药名={bool(hit)} 证据链 {len(ev)} 条")
        if not ok:
            print(f"        -> {answer[:1500]}")
        passed += ok
    print(f"  组 C：{passed}/{total}\n")
    return passed, total


def main():
    # Windows 控制台默认 GBK，打印答案里的 ⚠️ 等字符会 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="STUDY 讲解子 Agent 验收（Router + 召回 + 端到端）")
    ap.add_argument("--llm", action="store_true", help="包含组 C 端到端（需密钥 + teach_rag 已入库）")
    args = ap.parse_args()

    passed_a, total_a = run_group_a()
    passed_b, total_b = run_group_b()
    passed, total = passed_a + passed_b, total_a + total_b
    if args.llm:
        passed_c, total_c = run_group_c()
        passed += passed_c
        total += total_c

    rate = passed / total
    print(f"== 结果：{passed}/{total} 通过（{rate:.1%}，要求 ≥{PASS_RATE:.0%}）==")
    if args.llm and total_c and passed_c < total_c:
        print("[FAIL] 组 C 端到端未全过，验收不通过")
        raise SystemExit(1)
    if rate < PASS_RATE:
        print("[FAIL] 通过率未达要求，验收不通过")
        raise SystemExit(1)
    print("[OK] STUDY 讲解子 Agent 验收通过")


if __name__ == "__main__":
    main()
