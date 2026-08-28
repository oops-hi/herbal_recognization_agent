"""
agent/core.py
手写 ReAct 循环（不用 LangChain）：DeepSeek function calling + 工具分发。

关键细节（CLAUDE.md 关键设计 2 / 需求分析 6.3）：
- MAX_TURNS=8，调用次数由问题决定
- function.arguments 是 JSON 字符串 → json.loads()
- assistant 消息 content 为 None 也要原样回填 messages（否则模型失忆）
- 分发前 REGISTRY 白名单校验；工具异常以 role:"tool" 回填让模型自纠，不让请求 500
- 429 单次退避重试；402 不重试；断网 fail-soft（识别+图谱可用，仅对话降级）

两种入口：
- stream_run()：生成器，逐事件产出（Flask SSE 前端时间轴实时展示）
- run()：stream_run 的收集包装，返回 {answer, status, trace, turns}（CLI 用）

两者共用同一循环；messages 由调用方持有并在原地更新（Web 会话/CLI 交互都靠它做指代消解）。
"""
import json
import os
import time

import requests
from dotenv import load_dotenv

from config import DEEPSEEK_API_URL, DEEPSEEK_MODEL, ENV_PATH, LLM_TIMEOUT, MAX_TURNS
from . import prompts, tools

# 打包后 .env 在 exe 旁（frozen 分支）；开发态在项目根 —— 统一走 config.ENV_PATH
load_dotenv(ENV_PATH)

HERB_CTX_PREFIX = "【当前识别上下文】"


def _api_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY")


def is_configured() -> bool:
    return bool(_api_key())


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


def _set_herb_context(messages: list[dict], herb: str) -> None:
    """写入/更新【当前识别上下文】（上传新图时替换旧上下文）。"""
    ctx = (
        f"{HERB_CTX_PREFIX}用户已上传一张中药饮片图片，识别结果为：{herb}。"
        f"用户后续提问中的『这个/它/这味药』默认指 {herb}，无需重复询问。"
    )
    for m in messages:
        if m.get("role") == "system" and m["content"].startswith(HERB_CTX_PREFIX):
            m["content"] = ctx
            return
    messages.append({"role": "system", "content": ctx})


def _inject_image_context(messages: list[dict], image_path: str) -> None:
    """注入图片识别结果（--image 场景，与 6.1 主流程一致）。"""
    try:
        from classifier import predictor

        top = predictor.predict_topk(image_path, k=3)
    except Exception as e:
        messages.append({
            "role": "user",
            "content": f"[系统注入] 用户上传了图片 {image_path}，但图片识别不可用（{e}），请如实告知。",
        })
        return
    if not top:
        messages.append({
            "role": "user",
            "content": f"[系统注入] 用户上传了图片 {image_path}，但模型未返回结果，请如实告知。",
        })
        return
    lines = "\n".join(f"- {n}（置信度 {c:.2%}）" for n, c in top)
    messages.append({
        "role": "user",
        "content": (
            f"[系统注入] 用户上传了图片 {image_path}，模型识别 Top-3：\n{lines}\n"
            f"本轮对话以识别结果 {top[0][0]} 为当前药材。"
        ),
    })


def stream_run(
    question: str,
    messages: list[dict],
    image_path: str | None = None,
    current_herb: str | None = None,
):
    """ReAct 循环生成器：逐事件产出，messages 原地更新。

    events:
      {"type": "tool",   "name", "arguments", "summary", "result"(≤200 字)}
      {"type": "answer", "text"}
      {"type": "error",  "text", "status": no_key|quota|rate_limited|offline|error}
      {"type": "turn",   "count"}   # 每次 LLM 调用后发一次（前端可显示进度）
    """
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": prompts.build_system_prompt()})
    if current_herb:
        _set_herb_context(messages, current_herb)
    if image_path:
        _inject_image_context(messages, image_path)
    messages.append({"role": "user", "content": question})

    if not _api_key():
        yield {"type": "error", "text": "未配置 DEEPSEEK_API_KEY，对话服务不可用（请配置 .env）。", "status": "no_key"}
        return

    turns = 0
    while turns < MAX_TURNS:
        turns += 1
        try:
            msg = _chat_completion(messages)
        except requests.ConnectionError:
            yield {
                "type": "error",
                "text": "当前网络不可用，对话服务暂时离线。图片识别与知识图谱查询仍可正常使用。",
                "status": "offline",
            }
            return
        except requests.Timeout:
            yield {"type": "error", "text": "大模型响应超时，请稍后重试。", "status": "error"}
            return
        except RuntimeError as e:
            status = "quota" if "402" in str(e) else "error"
            yield {"type": "error", "text": str(e), "status": status}
            return

        yield {"type": "turn", "count": turns}

        tool_calls = msg.get("tool_calls")
        # ⚠️ assistant 消息 content 可能为 None，必须原样回填（含 tool_calls）
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})

        if not tool_calls:
            yield {"type": "answer", "text": msg.get("content") or "（模型未返回内容）"}
            return

        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError as e:
                arguments = {}
                result = f"参数解析失败（{e}），请重新生成合法的 JSON 参数。"
            else:
                result = tools.call_tool(name, arguments)

            yield {
                "type": "tool",
                "name": name,
                "arguments": arguments,
                "summary": tools.tool_summary(name, arguments),
                "result": result[:200] + ("…" if len(result) > 200 else ""),
            }
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result,
            })

    yield {
        "type": "error",
        "text": f"已达到最大工具轮次（{MAX_TURNS}），未能收敛出最终回答。请尝试把问题拆细。",
        "status": "error",
    }


def run(
    question: str,
    messages: list[dict],
    image_path: str | None = None,
    current_herb: str | None = None,
) -> dict:
    """stream_run 的收集包装（CLI/demo_agent 用）。messages 同样原地更新。"""
    result: dict = {"answer": "", "status": "error", "trace": [], "turns": 0}
    for ev in stream_run(question, messages, image_path, current_herb):
        t = ev["type"]
        if t == "tool":
            result["trace"].append({
                "name": ev["name"],
                "arguments": ev["arguments"],
                "summary": ev["summary"],
                "result": ev["result"],
            })
        elif t == "answer":
            result["answer"] = ev["text"]
            result["status"] = "ok"
        elif t == "turn":
            result["turns"] = ev["count"]
        elif t == "error":
            result["answer"] = ev["text"]
            result["status"] = ev.get("status", "error")
    return result
