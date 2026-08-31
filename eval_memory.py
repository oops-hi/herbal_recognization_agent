"""
eval_memory.py —— 跨对话记忆（长对话用户画像提取）评测。

断言口径（对齐「豆包式画像提取」目标）：
1. 从长对话中提炼出 identity（身份）条目（文本含「学生」）；
2. 提炼出 health（健康关注）条目（文本含「眼睛」）；
3. 条目总数 ≤ 20；
4. 合规红线：条目文本不含知识结论（「性味/归经/药典」字样不得出现）；
5. build_context 注入文本带 kind 标签与使用指引（记忆能影响回答行为）。

实现说明：直接调 agent/memory._digest_worker（daemon 线程的同步内核），
并把 _save 替换为 no-op——评测只在内存中验证，绝不触碰用户真实 memory.json。

用法（需 DEEPSEEK_API_KEY，联网）：
    conda run -n task python eval_memory.py
"""
import re
import sys

from agent import core, memory

# 8 轮长对话模拟：身份 + 健康关注 + 偏好 穿插普通药问（assistant 为占位文本）
ROUNDS = [
    ("我是中医学专业大二学生，期末要考方剂学，平时用这个软件复习",
     "好的，我会结合你的备考需求来回答。"),
    ("枸杞子性味归经是什么",
     "枸杞子味甘性平，归肝、肾经（《中国药典》2020 年版一部，知识展示）。"),
    ("我最近眼睛经常干涩，看屏幕比较多",
     "用眼疲劳建议适度休息、控制屏幕时间（不诊断、不辨证，建议就医咨询）。"),
    ("金银花和连翘怎么区分",
     "金银花疏散风热偏于清透，连翘长于散结（知识展示）。"),
    ("我喜欢回答里带出处，复习要背考点",
     "好的，后续回答我会保持溯源并提示考点。"),
    ("讲讲枸杞子",
     "枸杞子课文化讲解（考点提示：归经与功效常考）。"),
    ("瓜蒌皮能和川乌一起用吗",
     "不能。十八反：瓜蒌反乌头（歌诀有载，药典认定）。"),
    ("山楂有哪些经典方剂",
     "保和丸、山楂丸等（知识展示）。"),
]

FORBIDDEN_KNOWLEDGE = re.compile(r"性味|归经|药典|功效|禁忌")


def run_deterministic() -> tuple[int, int]:
    """⑦ 身份自述单句即时触发 + ⑧ 画像驱动路由（确定性，无 API 调用）。"""
    total = passed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal total, passed
        total += 1
        passed += ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")

    print("== 确定性检查（身份即时触发 + 画像驱动路由）==")
    # ⑦ 身份自述单句立即触发摘要（worker 置 no-op，只验证触发判定与计数）
    real_worker = memory._digest_worker
    memory._digest_worker = lambda *a, **k: None
    try:
        sess = {
            "messages": [
                {"role": "user", "content": core.compose_user_message("讲解", "我是一名医学生，要备考方剂学")},
                {"role": "assistant", "content": "好的，我会结合你的备考需求回答。", "tool_calls": None},
            ],
            "digest_seen": 0,
        }
        memory.maybe_digest(sess, "eval-ident")
        check("⑦ 身份自述单句触发画像提取（不等攒满 2 条）",
              sess.get("digest_seen") == 1, f"digest_seen={sess.get('digest_seen')}")
    finally:
        memory._digest_worker = real_worker

    # ⑧ 画像驱动路由：备考身份 → 兜底问题偏好讲解；剂量/诊断类不抢
    ctx = "【跨对话记忆】…：\n- [身份] 中医学专业大二学生，正在备考\n"
    check("⑧a 画像备考身份 → 单药知识问题路由讲解",
          core.classify_agent("枸杞子性味归经是什么", memory_ctx=ctx) == "讲解")
    check("⑧b 无画像 → 同一问题保持药性",
          core.classify_agent("枸杞子性味归经是什么") == "药性")
    check("⑧c 剂量类不抢（画像在场也走药性）",
          core.classify_agent("枸杞子一天吃多少合适？", memory_ctx=ctx) == "药性")
    check("⑧d 诊断类不抢（画像在场也走药性）",
          core.classify_agent("我最近头晕，是不是肾虚了", memory_ctx=ctx) == "药性")
    check("⑧e 纯老年身份句匹配身份信号（不靠「买药」碰巧命中）",
          bool(memory.IDENTITY_SIGNAL_PAT.search("我是一名老年人")))
    print(f"  确定性检查：{passed}/{total}\n")
    return passed, total


def main() -> None:
    if not core.is_configured():
        raise SystemExit("[错误] 未配置 DEEPSEEK_API_KEY，画像提取评测需要真实调用大模型。")
    det_passed, det_total = run_deterministic()

    # 评测隔离：清空内存态 + _save 置 no-op（绝不写用户真实 memory.json）
    real_save = memory._save
    memory._state["entries"] = []
    memory._save = lambda: None
    try:
        messages = []
        for i, (q, a) in enumerate(ROUNDS, 1):
            messages.append({"role": "user", "content": core.compose_user_message("药性", q)})
            messages.append({"role": "assistant", "content": a, "tool_calls": None})
            # 模拟真实触发节奏：每 2 条用户消息摘要一次（与 maybe_digest 节流一致）
            if i % 2 == 0:
                transcript = memory._extract_transcript(messages)
                memory._digest_worker(transcript, f"eval-round-{i}")

        snap = memory.snapshot()
        entries = snap["entries"]
        texts = [e["text"] for e in entries]
        kinds = {e["kind"] for e in entries}

        print("== 画像提取评测（8 轮长对话）==")
        print("最终画像清单：")
        for e in entries:
            print(f"  [{e['kind']}] {e['text']}  herbs={e['herbs']}")

        passed, failed = 0, 0

        def check(name: str, ok: bool, detail: str = "") -> None:
            nonlocal passed, failed
            if ok:
                passed += 1
                print(f"  [PASS] {name}")
            else:
                failed += 1
                print(f"  [FAIL] {name} {detail}")

        ident = [t for e, t in zip(entries, texts) if e["kind"] == "identity"]
        check("① 提炼出身份（identity）条目且含「学生」",
              any("学生" in t for t in ident),
              f"identity 条目={ident}")

        health = [t for e, t in zip(entries, texts) if e["kind"] == "health"]
        check("② 提炼出健康关注（health）条目且含「眼睛」",
              any("眼睛" in t for t in health),
              f"health 条目={health}")

        check("③ 条目总数 ≤ 20", len(entries) <= 20, f"实际 {len(entries)}")

        bad = [t for t in texts if FORBIDDEN_KNOWLEDGE.search(t)]
        check("④ 无知识结论（性味/归经/药典/功效/禁忌）", not bad, f"违规条目={bad}")

        check("⑤ kind 全部在五类白名单内", kinds <= set(memory.MEMORY_KINDS), f"kinds={kinds}")

        ctx = memory.build_context() or ""
        check("⑥ 注入文本带标签与使用指引",
              "[身份]" in ctx and "画像使用指引" in ctx,
              f"ctx 前 120 字={ctx[:120]!r}")

        # ---- 场景 2：身份切换（用户实测反馈：换身份后旧身份残留、路由不随身份变）----
        print("== 场景 2：身份切换（医学生备考 → 老年人买药鉴别）==")
        memory._state["entries"] = [
            {"id": "seed-1", "ts": 1.0, "text": "医学生，正在备考方剂学",
             "herbs": [], "kind": "identity", "src_session": "seed"},
            {"id": "seed-2", "ts": 1.0, "text": "备考方剂学期末考试",
             "herbs": [], "kind": "goal", "src_session": "seed"},
        ]
        transcript2 = ("user: 我是一个老年人经常买药，需要鉴别\n"
                       "assistant: 好的，需要鉴别时我可以帮您区分易混药材。")
        memory._digest_worker(transcript2, "eval-switch")
        entries2 = memory.snapshot()["entries"]
        texts2 = [e["text"] for e in entries2]
        check("⑦ 身份切换：旧身份（学生/备考）被删除",
              not any(("学生" in t or "备考" in t) for t in texts2),
              f"残留={texts2}")
        check("⑧ 身份切换：新身份（老年人）存在",
              any(e["kind"] == "identity" and "老年" in e["text"] for e in entries2),
              f"entries={texts2}")
        ctx2 = memory.build_context() or ""
        got2 = core.classify_agent("枸杞子性味归经是什么", memory_ctx=ctx2)
        check("⑨ 身份切换：学生路由 boost 关闭（回到药性兜底）",
              got2 == "药性", f"实际={got2}")

        total = det_passed + passed
        total_all = det_total + passed + failed
        print(f"\n== 结果：{total}/{total_all} 通过（确定性 {det_passed}/{det_total} + LLM {passed}/{passed + failed}）==")
        if failed or det_passed < det_total:
            raise SystemExit(1)
        print("[OK] 画像提取评测通过：身份/健康关注被提炼，无知识结论，注入带使用指引，身份即时触发 + 画像驱动路由")
    finally:
        memory._state["entries"] = []
        memory._save = real_save


if __name__ == "__main__":
    main()
