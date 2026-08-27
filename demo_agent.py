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
        print(f"  [{i}] {step['summary']}")
        preview = step["result"].replace("\n", "\n      ")
        print(f"      -> {preview}")
    print("=" * 56 + "\n")


def ask(question: str, image_path: str | None, history: list[dict]) -> dict:
    """单轮问答；返回 result，同时把对话写回 history（供指代消解）。"""
    result = core.run(question, image_path=image_path, history=history)
    print_trace(result["trace"])
    print(result["answer"])
    if result["status"] != "ok":
        print(f"\n[状态: {result['status']}]")
    # 回填 history：assistant 回答对多轮指代消解是关键
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": result["answer"]})
    return result


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
    history: list[dict] = []

    if args.q:
        ask(args.q, args.image, history)
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
        ask(q, args.image, history)


if __name__ == "__main__":
    main()
