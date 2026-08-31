"""
eval_disambiguate.py — 相似对鉴别闭环评测（2026-08-31）

覆盖三条链路：
  组 A（离线，确定性，零 API）：
    - load_guide：17 对 + 总原则
    - find_similar_pairs 触发检测：两者同现语义、连通分量聚簇、别名归一、无对不触发
    - disambiguate_material 两模式：提问清单（不强答）/ 研判材料（结论等级 + B 档功效差异 + 档案）
  组 B（--llm，端到端 core.run + disambig_ctx）：
    - Router 路由断言：鉴别激活 / 安全·方剂·讲解不抢单 / 无 ctx 不激活
    - 两轮鉴别对话：t1 提问清单工具调用 → t2 传 desc 研判 + 结论等级

用法：
    conda run -n task python eval_disambiguate.py          # 组 A + 组 B Router（离线）
    conda run -n task python eval_disambiguate.py --llm    # 追加端到端两轮对话（需 DEEPSEEK_API_KEY）
"""
import sys

from kg import disambiguate as kdg

PASS = FAIL = 0


def check(name: str, cond: bool):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


# ---------- 组 A：离线确定性 ----------

def group_a():
    print("== 组 A：资料库 / 触发检测 / 材料两模式（离线） ==")
    guide = kdg.load_guide()
    check("guide 17 对（三黄拆 3 条两两）", len(guide.get("pairs", [])) == 17)
    check("guide 总原则非空", bool(guide.get("general_principle")))
    check("guide 来源标注 B 档", "教材汇编" in str(guide.get("provenance_level", "")) or "B" in str(guide.get("provenance_level", "")))

    # ---- 触发检测（两者同现才触发 + 连通分量聚簇） ----
    c1 = kdg.find_similar_pairs(["桃仁", "苦杏仁", "川楝子"])
    check("c1 桃仁/苦杏仁/川楝子 → 1 簇 3 成员", len(c1) == 1 and set(c1[0]["candidates"]) == {"桃仁", "苦杏仁", "川楝子"})
    c2 = kdg.find_similar_pairs(["枸杞子", "五味子", "山楂"])
    check("c2 同现语义：山楂<->金樱子不触发（金樱子不在 top3）",
          len(c2) == 1 and set(c2[0]["candidates"]) == {"枸杞子", "五味子"})
    c3 = kdg.find_similar_pairs(["木瓜", "瓜蒌皮", "菊花"])
    check("c3 无相似对 → 不触发", c3 == [])
    c4 = kdg.find_similar_pairs(["砂仁", "豆蔻", "草豆蔻"])
    check("c4 三成员聚 1 簇", len(c4) == 1 and set(c4[0]["candidates"]) == {"砂仁", "豆蔻", "草豆蔻"})
    c5 = kdg.find_similar_pairs(["连翘", "栀子", "乌梅"])
    check("c5 新对（连翘<->栀子）触发", len(c5) == 1 and set(c5[0]["candidates"]) == {"连翘", "栀子"})
    c6 = kdg.find_similar_pairs(["枸杞", "五味子", "山楂"])
    check("c6 别名归一（枸杞→枸杞子）", len(c6) == 1 and set(c6[0]["candidates"]) == {"枸杞子", "五味子"})
    c7 = kdg.find_similar_pairs([])
    check("c7 空 top3 不触发", c7 == [])

    # ---- 材料两模式 ----
    m1 = kdg.disambiguate_material(["桃仁", "苦杏仁"])
    check("m1 提问清单（不强答 + 四尝维度）", "提问清单" in m1 and "不强答" in m1 and "四尝" in m1)
    m2 = kdg.disambiguate_material(["桃仁", "苦杏仁"], "扁长卵形，表面有小颗粒突起")
    check("m2 研判材料（结论等级 + 可判定）", "研判材料" in m2 and "结论等级" in m2 and "可判定" in m2)
    m3 = kdg.disambiguate_material(["柴胡", "银柴胡"])
    check("m3 MD 专属对维度对照（砂眼）", "砂眼" in m3)
    m4 = kdg.disambiguate_material(["金银花", "山银花"], "花冠筒细长")
    check("m4 白名单外药 fail-soft（未收录档案提示）", "未收录核心档案" in m4)
    m5 = kdg.disambiguate_material(["苦杏仁", "桃仁"], "扁长卵形")
    check("m5 药效差异带 B 档标签", "药效差异" in m5 and "教材汇编" in m5)
    m6 = kdg.disambiguate_material(["桃仁"])
    check("m6 单候选拒绝", "至少两个" in m6)


# ---------- 组 B：Router 路由断言（离线） + 端到端两轮（--llm） ----------

def group_b_router():
    print("== 组 B：Router 路由（离线） ==")
    from agent import core

    check("r1 disambig 激活 → 鉴别（『这是什么』）",
          core.classify_agent("这是什么", disambig_ctx="候选易混") == "鉴别")
    check("r2 disambig 激活 → 鉴别（给特征）",
          core.classify_agent("扁长卵形，表面有颗粒", disambig_ctx="候选易混") == "鉴别")
    check("r3 安全不抢单（『能一起吃吗』→ 安全）",
          core.classify_agent("这两个能一起吃吗", disambig_ctx="候选易混") == "安全")
    check("r4 方剂不抢单（『有什么方子』→ 方剂）",
          core.classify_agent("有什么方子", disambig_ctx="候选易混") == "方剂")
    check("r5 讲解显式不抢（『讲讲桃仁』→ 讲解）",
          core.classify_agent("讲讲桃仁", disambig_ctx="候选易混") == "讲解")
    check("r6 无 ctx 不激活（默认药性）", core.classify_agent("这是什么") == "药性")


def group_b_llm():
    print("== 组 B：端到端两轮鉴别对话（--llm） ==")
    from agent import core

    dctx = "识别候选中『桃仁、苦杏仁、川楝子』为外形易混淆药材，可向用户追问可观察特征"
    msgs: list[dict] = []

    r1 = core.run("这是什么", msgs, disambig_ctx=dctx)
    t1 = [t["name"] for t in r1["trace"]]
    check("t1 调用 disambiguate_similar 取提问清单", "disambiguate_similar" in t1)
    check(f"t1 状态 ok（实际 {r1['status']}）", r1["status"] == "ok")

    r2 = core.run("扁长卵形，表面有小颗粒突起", msgs, disambig_ctx=dctx)
    t2 = [t["name"] for t in r2["trace"]]
    check("t2 传 desc 再调 disambiguate_similar", "disambiguate_similar" in t2)
    verdict = any(k in r2["answer"] for k in ("可判定", "部分信息", "需人工"))
    check(f"t2 回答含结论等级（实际开头：{r2['answer'][:40]!r}）", verdict)
    check(f"t2 状态 ok（实际 {r2['status']}）", r2["status"] == "ok")


def main():
    group_a()
    group_b_router()
    if "--llm" in sys.argv:
        group_b_llm()
    print(f"\n结果：PASS {PASS} / FAIL {FAIL}")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
