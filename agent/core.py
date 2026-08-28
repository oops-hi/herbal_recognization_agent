"""
agent/core.py
手写 ReAct 循环（不用 LangChain）：DeepSeek function calling + 工具分发 + 六子 Agent 编排。

二期 P2（方案 §8.3）：
- 轻量 Router：意图分类（规则优先，快速失败转追问）→ 分派六子 Agent
- 每个子 Agent = 受限 ReAct 循环（更小工具集 schema + 聚焦 System Prompt + 配额制）
- 五道闸门落点：知识闸门（未收录拒答）+ 业务闸门（剂量/诊断合规模板）+ 输出闸门
  （refuse_reason 四类结构化 + 证据链溯源）
- MAX_TURNS=8 改为按子 Agent 配额（AGENTS[agent].quota），全局 MAX_TURNS 兜底

一期保留（CLAUDE.md 关键设计 2 / 需求分析 6.3）：
- function.arguments 是 JSON 字符串 → json.loads()
- assistant 消息 content 为 None 也要原样回填 messages（否则模型失忆）
- 分发前 REGISTRY 白名单校验；工具异常以 role:"tool" 回填让模型自纠，不让请求 500
- 429 单次退避重试；402 不重试；断网 fail-soft（识别+图谱可用，仅对话降级）

SSE 事件协议（一期兼容 + 二期扩展）：
  {"type": "tool",   "name", "arguments", "summary", "result"(≤200 字), "agent"}
  {"type": "answer", "text", "refuse_reason"(str|None), "evidence": [{source}]}
  {"type": "error",  "text", "status": no_key|quota|rate_limited|offline|error}
  {"type": "turn",   "count"}
"""
import json
import os
import re
import time

import requests

import config  # 间接访问常量：设置页 save_llm_config() 热更新后本模块即时生效
from . import prompts, tools

# .env 由 config.py 统一加载（ENV_PATH 含打包 frozen 分支）；本模块只读 os.environ

HERB_CTX_PREFIX = "【当前识别上下文】"

# ---------- 五道闸门：输出闸门（refuse_reason 四类 + 证据链） ----------

REFUSE_GAP = "知识缺口"    # 问题实体不在知识库
REFUSE_CONF = "置信不足"    # Top-1 < 0.75 / 候选差距过小
REFUSE_DOMAIN = "域外图"   # 非药材图像
REFUSE_COMPLIANCE = "合规边界"  # 涉及诊断/剂量/疗效/真伪/产地

# 剂量建议模式（「药典记载 6～12g」安全，『你应该用 X 克』违规）
DOSAGE_PAT = re.compile(r"你应该用|建议(你|每天).{0,8}\d|一次\s*\d+\s*克|每次\s*\d+\s*克|每天\s*\d+\s*克|剂量为\s*\d")
# 诊断/辨证模式
DIAG_PAT = re.compile(r"你(是|得了|患有)|确诊|你属于.{0,6}(症|病)|辨证为")
# 知识缺口信号（工具返回即模型应转述）
GAP_PAT = re.compile(r"知识库未收录|知识缺口|未收录与「")
# 置信不足信号
CONF_PAT = re.compile(r"补拍建议|识别不确定|置信度较低|无法确认是否为")

COMPLIANCE_TAIL = "\n\n（提示：以上为知识展示，不构成医疗或用药建议；诊断与用药请咨询执业药师。）"

# 证据链来源提取：版本化出处串（《…》＋年份版，含尾随「（A 档）」来源等级）与「出处：…」行
SOURCE_RE = re.compile(r"《[^》]{2,40}》\s*\d{0,4}\s*年版[^；\n。]*|出处：\s*([^\n]+)")


def _api_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY")


def is_configured() -> bool:
    return bool(_api_key())


# ---------- 轻量 Router（方案 §8.3：规则优先 + 快速失败） ----------

LEARN_PAT = re.compile(r"识别错|认错|不是.{1,8}(是|而是)|应该是|其实是.{0,6}是|你记错")
DIFF_KW = ("怎么区分", "区别", "有什么不同", "容易混淆", "易混淆", "分辨", "鉴别")
SAFETY_KW = ("能一起", "一起用", "一起泡", "同用", "合用", "配伍", "禁忌", "相克",
             "同食", "能不能和", "能和", "十八反", "十九畏", "同时吃", "一起吃", "相畏")
FORMULA_KW = ("方剂", "经典方", "什么方", "吃什么药", "方子", "成药", "杞菊地黄",
              "六味地黄", "保和丸", "香砂", "失眠", "咳嗽", "腹胀", "便秘", "补肾", "健脾")


def classify_agent(question: str, image_path: str | None = None) -> str:
    """意图分类：图片 → 识药；修正 → 学习；鉴别 → 鉴别；组合/禁忌 → 安全；方剂 → 方剂；默认药性。

    规则优先（确定性、零成本）；无法分类走药性兜底（带 retrieve_doc 的开放问答）。
    快速失败原则：不无限级联——分类是单跳，子 Agent 内的工具调用链由模型决定。
    """
    if image_path:
        return "识药"
    q = (question or "").strip()
    if not q:
        return "药性"
    if LEARN_PAT.search(q):
        return "学习"
    if any(k in q for k in DIFF_KW):
        return "鉴别"
    if any(k in q for k in SAFETY_KW):
        return "安全"
    if any(k in q for k in FORMULA_KW):
        return "方剂"
    return "药性"


def _gate_answer(text: str) -> tuple[str, str | None]:
    """输出闸门：refuse_reason 四类结构化 + 合规追加（不改原文，命中即附提示）。"""
    if not text:
        return text, None
    if DOSAGE_PAT.search(text) or DIAG_PAT.search(text):
        # 业务闸门兜底：剂量建议/诊断输出 → 附合规提示 + refuse_reason
        return text + COMPLIANCE_TAIL, REFUSE_COMPLIANCE
    if GAP_PAT.search(text):
        return text, REFUSE_GAP
    if CONF_PAT.search(text):
        return text, REFUSE_CONF
    return text, None


def _extract_evidence(tool_results: list[str]) -> list[dict]:
    """证据链：从本轮工具结果提取版本化出处（去重，最多 6 条）。"""
    seen, ev = set(), []
    for r in tool_results:
        for m in SOURCE_RE.finditer(r):
            src = (m.group(1) or m.group(0)).strip()
            if not src or src in seen:
                continue
            seen.add(src)
            ev.append({"source": src})
            if len(ev) >= 6:
                return ev
    return ev


def _chat_completion(messages: list[dict], agent: str | None = None) -> dict:
    """调用 DeepSeek chat/completions（tools 按子 Agent 过滤）。

    返回 choices[0].message；429 单次退避重试；402/非 2xx 抛 RuntimeError；断网抛 ConnectionError。
    """
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")

    payload = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.3,   # 工具调用求稳，压低随机性
        "max_tokens": 1024,
        "stream": False,
        "tools": tools.schemas_for(agent),   # 子 Agent 工具子集（agent=None 全量）
        "tool_choice": "auto",
    }

    def _post() -> requests.Response:
        return requests.post(
            config.DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=config.LLM_TIMEOUT,
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


def _set_agent_system(messages: list[dict], agent: str) -> None:
    """按子 Agent 替换/注入 system prompt（HERB_CTX 独立消息不受影响）。"""
    new_prompt = prompts.build_system_prompt(agent)
    for m in messages:
        if (m.get("role") == "system"
                and not m["content"].startswith(HERB_CTX_PREFIX)):
            m["content"] = new_prompt
            return
    messages.insert(0, {"role": "system", "content": new_prompt})


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
            f"若候选间难以区分（如桃仁/苦杏仁），可调用 similar_compare 输出鉴别要点对比。"
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
      {"type": "tool",   "name", "arguments", "summary", "result"(≤200 字), "agent"}
      {"type": "answer", "text", "refuse_reason", "evidence"}
      {"type": "error",  "text", "status": no_key|quota|rate_limited|offline|error}
      {"type": "turn",   "count"}
    """
    # ---- Router：意图分类 → 子 Agent（工具子集 + System Prompt + 配额）----
    agent = classify_agent(question, image_path)
    quota = tools.AGENTS[agent]["quota"]
    max_turns = min(config.MAX_TURNS, quota)

    _set_agent_system(messages, agent)
    if current_herb:
        _set_herb_context(messages, current_herb)
    if image_path:
        _inject_image_context(messages, image_path)
    messages.append({"role": "user", "content": question})

    if not _api_key():
        yield {"type": "error", "text": "未配置 API 密钥，对话服务不可用。请点击右上角 ⚙️ 设置页配置。", "status": "no_key"}
        return

    tool_results: list[str] = []
    turns = 0
    while turns < max_turns:
        turns += 1
        try:
            msg = _chat_completion(messages, agent=agent)
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
            answer, refuse_reason = _gate_answer(msg.get("content") or "（模型未返回内容）")
            yield {
                "type": "answer",
                "text": answer,
                "refuse_reason": refuse_reason,
                "evidence": _extract_evidence(tool_results),
            }
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

            tool_results.append(result)
            yield {
                "type": "tool",
                "name": name,
                "arguments": arguments,
                "summary": tools.tool_summary(name, arguments),
                "result": result[:200] + ("…" if len(result) > 200 else ""),
                "agent": tools.agent_of(name),
            }
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result,
            })

    yield {
        "type": "error",
        "text": f"已达到最大工具轮次（{max_turns}，{agent} 子 Agent 配额），未能收敛出最终回答。请尝试把问题拆细。",
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
                "agent": ev.get("agent", ""),
            })
        elif t == "answer":
            result["answer"] = ev["text"]
            result["status"] = "ok"
            result["refuse_reason"] = ev.get("refuse_reason")
            result["evidence"] = ev.get("evidence", [])
        elif t == "turn":
            result["turns"] = ev["count"]
        elif t == "error":
            result["answer"] = ev["text"]
            result["status"] = ev.get("status", "error")
    return result
