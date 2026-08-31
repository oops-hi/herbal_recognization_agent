"""
demo_agent.py
命令行智能体（先于 Flask 跑通调试）：文本问答 + 可选图片识别上下文。

用法：
    conda run -n task python demo_agent.py --q "瓜蒌皮能和川乌一起用吗"
    conda run -n task python demo_agent.py --image <图片> --q "这个能和菊花一起泡水吗？我最近眼睛干"
    conda run -n task python demo_agent.py            # 进入交互模式（多轮，支持指代消解）
"""
import argparse
import sys

from agent import core
from agent import prompts


def print_trace(trace: list[dict]) -> None:
    """打印工具调用链（= 答辩展示的"智能体自主编排"证据）。"""
    if not trace:
        return
    print("\n" + "=" * 56)
    print("  工具调用链（由问题驱动，共 %d 次）" % len(trace))
    print("=" * 56)
    for i, step in enumerate(trace, 1):
        agent_tag = f"（{step.get('agent', '')}）" if step.get("agent") else ""
        print(f"  [{i}]{agent_tag} {step['summary']}")
        preview = step["result"].replace("\n", "\n      ")
        print(f"      -> {preview}")
    print("=" * 56 + "\n")


def print_evidence(evidence: list[dict], refuse_reason: str | None) -> None:
    """证据链 + 拒识原因（二期 P2 展示）。"""
    if refuse_reason:
        print(f"  [拒识原因] {refuse_reason}")
    if evidence:
        print("  证据链（回答依据来源）：")
        for e in evidence:
            print(f"    - {e['source']}")
    print()


def print_cache(stats: dict) -> None:
    """KV 前缀缓存命中统计（稳定前缀架构验证用；累计口径与前端状态栏一致）。"""
    hit, miss = stats.get("cache_hit", 0), stats.get("cache_miss", 0)
    tot = hit + miss
    if tot:
        print(f"  [KV缓存] hit={hit} miss={miss} 命中率={hit / tot:.1%}（累计）")
    else:
        print("  [KV缓存] 无数据")


def main():
    ap = argparse.ArgumentParser(description="中草药识别智能体 · 命令行演示")
    ap.add_argument("--image", help="中药饮片图片路径（可选，注入识别上下文）")
    ap.add_argument("--q", help="单轮提问；缺省进入交互模式")
    args = ap.parse_args()

    if not core.is_configured():
        print(
            "[错误] 未配置 DEEPSEEK_API_KEY。\n"
            "   请复制 .env.example 为 .env 并填入密钥（建议直接从 pet_recognition 复制 .env）。"
        )
        sys.exit(1)

    print(prompts.get_graph_stats())
    # 会话消息由 core.run 原地更新（含 system/assistant/tool 全量消息，支持指代消解）
    messages: list[dict] = []
    stats: dict = core._new_stats()   # 会话级上下文统计（跨轮次累计，含 KV 缓存命中）

    if args.q:
        result = core.run(args.q, messages, image_path=args.image, session_stats=stats)
        print_trace(result["trace"])
        print(result["answer"])
        print_evidence(result.get("evidence", []), result.get("refuse_reason"))
        print_cache(stats)
        if result["status"] != "ok":
            print(f"\n[状态: {result['status']}]")
        return

    print("进入交互模式（输入 exit 退出）。支持多轮追问与指代消解。")
    while True:
        try:
            q = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", "q"):
            print("再见。")
            break
        # 图片只在首轮注入（messages 首轮为空，run 后原地填充）；每轮重复注入会
        # 追加一条 [系统注入] user 消息破坏稳定前缀（KV 缓存架构）
        result = core.run(q, messages,
                          image_path=args.image if not messages else None,
                          session_stats=stats)
        print_trace(result["trace"])
        print(result["answer"])
        print_evidence(result.get("evidence", []), result.get("refuse_reason"))
        print_cache(stats)
        if result["status"] != "ok":
            print(f"\n[状态: {result['status']}]")


if __name__ == "__main__":
    main()
