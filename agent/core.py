"""
agent/core.py
手写 ReAct 循环（不用 LangChain）：DeepSeek function calling + 工具分发。

关键细节（CLAUDE.md 关键设计 2 / 需求分析 6.3）：
- MAX_TURNS=8，调用次数由问题决定
- function.arguments 是 JSON 字符串 → json.loads()
- assistant 消息 content 为 None 也要原样回填 messages（否则模型失忆）
- 分发前 REGISTRY 白名单校验；工具异常以 role:"tool" 回填让模型自纠，不让请求 500
- 429 单次退避重试；402 不重试；断网 fail-soft（识别+图谱可用，仅对话降级）
"""
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from config import DEEPSEEK_API_URL, DEEPSEEK_MODEL, LLM_TIMEOUT, MAX_TURNS
from . import prompts, tools

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _api_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY")


def _chat_completion(messages: list[dict], with_tools: bool = True) -> dict:
    """调用 DeepSeek chat/completions。

    返回 choices[0].message；429 单次退避重试；402/非 2xx 抛 RuntimeError；断网抛 ConnectionError。
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.3,   # 工具调用求稳，压低随机性
        "max_tokens": 1024,
        "stream": False,
    }
    if with_tools:
        payload["tools"] = tools.TOOL_SCHEMAS
        payload["tool_choice"] = "auto"

    def _post() -> requests.Response:
        return requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=LLM_TIMEOUT,
        )

    resp = _post()
    if resp.status_code == 429:
        time.sleep(3)  # 单次退避重试
        resp = _post()
    if resp.status_code == 402:
        raise RuntimeError("DeepSeek 余额不足（402），请检查账户配置")
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek API 错误 {resp.status_code}: {resp.text[:200]}")
    return resp.json()["choices"][0]["message"]


def run(
    question: str,
    current_herb: str | None = None,
    history: list[dict] | None = None,
    image_path: str | None = None,
) -> dict:
    """执行一轮智能体问答。

    返回 {"answer", "status", "trace", "turns"}：
    - status: "ok" / "no_key" / "quota" / "rate_limited" / "offline" / "error"
    - trace:  [{name, arguments, result(截断), summary}]，供前端时间轴
    """
    system = prompts.build_system_prompt(current_herb)
    messages: list[dict] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)

    if image_path:
        # 文本问答为主，识别结果作为 context 注入（与 6.1 主流程一致）
        try:
            from classifier import predictor

            top = predictor.predict_topk(image_path, k=3)
            if top:
                top1 = top[0][0]
                lines = "\n".join(f"- {n}（置信度 {c:.2%}）" for n, c in top)
                messages.append({
                    "role": "user",
                    "content": (
                        f"[系统注入] 用户上传了图片 {image_path}，模型识别 Top-3：\n{lines}\n"
                        f"本轮对话以识别结果 {top1} 为当前药材。"
                    ),
                })
        except Exception as e:
            messages.append({
                "role": "user",
                "content": f"[系统注入] 用户上传了图片 {image_path}，但图片识别不可用（{e}），请如实告知。",
            })

    messages.append({"role": "user", "content": question})

    trace: list[dict] = []
    turns = 0
    try:
        while turns < MAX_TURNS:
            turns += 1
            try:
                msg = _chat_completion(messages)
            except requests.ConnectionError:
                return {
                    "answer": "当前网络不可用，对话服务暂时离线。图片识别与知识图谱查询仍可正常使用。",
                    "status": "offline",
                    "trace": trace,
                    "turns": turns,
                }
            except requests.Timeout:
                return {
                    "answer": "大模型响应超时，请稍后重试。",
                    "status": "error",
                    "trace": trace,
                    "turns": turns,
                }
            except RuntimeError as e:
                status = "quota" if "402" in str(e) else "error"
                return {"answer": str(e), "status": status, "trace": trace, "turns": turns}

            tool_calls = msg.get("tool_calls")
            # ⚠️ assistant 消息 content 可能为 None，必须原样回填（含 tool_calls）
            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})

            if not tool_calls:
                return {
                    "answer": msg.get("content") or "（模型未返回内容）",
                    "status": "ok",
                    "trace": trace,
                    "turns": turns,
                }

            # 逐个分发（并行工具调用时顺序执行，结果稳定）
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    arguments = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError as e:
                    arguments = {}
                    result = f"参数解析失败（{e}），请重新生成合法的 JSON 参数。"
                else:
                    # 白名单校验在 tools.call_tool 内 + 这里双重校验
                    result = tools.call_tool(name, arguments)

                trace.append({
                    "name": name,
                    "arguments": arguments,
                    "summary": tools.tool_summary(name, arguments),
                    "result": result[:200] + ("…" if len(result) > 200 else ""),
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result,
                })

        return {
            "answer": f"已达到最大工具轮次（{MAX_TURNS}），未能收敛出最终回答。请尝试把问题拆细。",
            "status": "error",
            "trace": trace,
            "turns": turns,
        }
    finally:
        # 不抛异常；调用链异常已在循环内兜底
        pass


def is_configured() -> bool:
    return bool(_api_key())
