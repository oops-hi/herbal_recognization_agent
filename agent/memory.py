"""
agent/memory.py
跨对话记忆（LLM 自动画像提取，Doubao 式）：对话流结束后后台用 DeepSeek 从长对话中
提炼**用户画像**——身份（identity）/ 目标（goal）/ 健康关注（health，仅用户自述事实）/
偏好（preference）/ 重要事实与纠错（fact）——合并进 memory.json；新对话开头由 app.py 取
build_context() 注入本轮 user 消息尾部（稳定前缀架构：不进 system、不改历史），
注入文本带使用指引（画像可影响回答角度，如学生→考点视角）。

红线（合规）：记忆只存用户层面信息，**禁止**记药性/功效/用量/禁忌等知识结论；
health 只记用户自述事实（「我眼睛干」），禁止辨证结论（「眼睛干=肝阴虚」）。

落盘：config.MEMORY_FILE（与 sessions.json 同源：FROZEN 写 exe 旁），
原子写（.tmp + os.replace）+ fail-soft，任何异常静默，绝不影响对话链路。
"""
import json
import os
import re
import threading
import time

import requests

import config
from . import core

# 模块级状态（app.py make_server threaded=True → 读改写必须持锁）
_lock = threading.Lock()
_state = {"version": 2, "enabled": True, "updated": 0.0, "entries": []}
# entry: {"id": "mem-<time_ns>", "ts": <epoch秒float>, "text": "<一句话条目>",
#         "herbs": ["<药材名>", ...], "kind": "<五类之一>", "src_session": "<client_id>"}

# 画像五类（schema v2）：摘要输出的 kind 白名单 + 注入文本的标签名 + 排序权重（越靠前越重要）
MEMORY_KINDS = ["identity", "goal", "health", "preference", "fact"]
KIND_LABELS = {"identity": "身份", "goal": "目标", "health": "健康", "preference": "偏好", "fact": "事实"}

# 身份自述信号（「我是医学生」「我在备考」「我是老年人」）：单条消息也立即触发画像提取，
# 不等攒满 MEMORY_DIGEST_MIN_NEW 条（豆包式身份提取的即时性）
IDENTITY_SIGNAL_PAT = re.compile(
    r"我是.{0,12}(学生|医|药|中医|西医|医生|药师|护士|老师|从业|宝妈|家长|老年人|老人|退休)|"
    r"医学生|学医|备考|考研|执业(医师|药师)|老年人|老人|退休|大爷|大妈|年纪大"
)


def _load() -> None:
    """启动读取 memory.json；缺失/损坏/字段非法 → 兜底空值，静默。"""
    global _state
    try:
        data = json.loads(config.MEMORY_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        entries = data.get("entries")
        if not isinstance(entries, list):
            return
        clean = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            text = e.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            clean.append({
                "id": e["id"] if isinstance(e.get("id"), str) else f"mem-{time.time_ns()}",
                "ts": e["ts"] if isinstance(e.get("ts"), (int, float)) else 0.0,
                "text": text.strip()[:60],
                "herbs": [h for h in e.get("herbs", []) if isinstance(h, str)][:8],
                # schema v2：旧条目缺 kind 一律归 preference（零迁移）
                "kind": e.get("kind") if e.get("kind") in MEMORY_KINDS else "preference",
                "src_session": e.get("src_session") if isinstance(e.get("src_session"), str) else "",
            })
        _state = {
            "version": 1,
            "enabled": bool(data.get("enabled", True)),
            "updated": data.get("updated") if isinstance(data.get("updated"), (int, float)) else 0.0,
            "entries": clean[: config.MEMORY_MAX_ENTRIES],
        }
    except (OSError, ValueError, TypeError):
        pass


def _save() -> None:
    """原子写（.tmp + os.replace）+ fail-soft（磁盘满/只读不阻塞对话）。"""
    try:
        tmp = config.MEMORY_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, config.MEMORY_FILE)
    except (OSError, TypeError):
        pass


def snapshot() -> dict:
    """当前记忆快照（GET/POST/DELETE API 统一返回；深拷贝防外部改写）。"""
    with _lock:
        return {
            "enabled": _state["enabled"],
            "updated": _state["updated"],
            "entries": [dict(e) for e in _state["entries"]],
        }


def is_enabled() -> bool:
    with _lock:
        return _state["enabled"]


def set_enabled(enabled: bool) -> None:
    """开关跨对话记忆（设置页）；立即生效并落盘。"""
    with _lock:
        _state["enabled"] = bool(enabled)
        _state["updated"] = time.time()
        _save()


def delete_entry(entry_id: str) -> bool:
    """删除单条记忆；命中 True 落盘，未命中 False（调用方转 404）。"""
    with _lock:
        before = len(_state["entries"])
        _state["entries"] = [e for e in _state["entries"] if e["id"] != entry_id]
        if len(_state["entries"]) == before:
            return False
        _state["updated"] = time.time()
        _save()
        return True


def build_context() -> str | None:
    """用户画像概要（≤ MEMORY_CTX_MAX_CHARS），供 app.py 注入本轮 user 消息。

    头部自带：① 合规声明（画像不是知识来源）；② 使用指引（画像可影响回答角度，
    如学生→考点视角——这是「记忆重要」的关键：注入必须带来可感知的行为差异）。
    条目按 kind 权重排序（身份/目标在前），带中文标签。开关关闭或无条目 → None。
    """
    with _lock:
        if not _state["enabled"]:
            return None
        entries = sorted(
            _state["entries"],
            key=lambda e: MEMORY_KINDS.index(e["kind"]) if e["kind"] in MEMORY_KINDS else 99,
        )
    if not entries:
        return None
    head = (
        "【跨对话记忆】以下是该用户的长期画像（从历史对话自动提炼，仅反映用户层面信息；"
        "不是知识来源：药性/功效/用量/禁忌必须以知识图谱工具实时查询为准）：\n"
    )
    tail = (
        "（画像使用指引：回答时可结合画像调整角度与深度——如身份为学生则侧重学习/考点视角，"
        "健康关注可自然关联提醒；画像只影响表达方式，不改变任何药性结论与合规红线。）"
    )
    body = ""
    for e in entries:
        label = KIND_LABELS.get(e["kind"], "偏好")
        line = f"- [{label}] {e['text']}"
        if e["herbs"]:
            line += f"（常问药材：{'、'.join(e['herbs'])}）"
        line += "\n"
        if len(head) + len(body) + len(line) + len(tail) > config.MEMORY_CTX_MAX_CHARS:
            body += "…\n"
            break
        body += line
    return (head + body + tail).strip()


def maybe_digest(sess: dict, client_id: str) -> None:
    """对话流结束后调用（/chat finally）：节流判定 + spawn 后台 daemon 摘要。

    触发条件：记忆开启 + 已配置密钥 + 本会话累计 user 消息较上次摘要新增
    ≥ MEMORY_DIGEST_MIN_NEW 条；**或**最新一条 user 消息是身份自述
    （IDENTITY_SIGNAL_PAT，如「我是医学生，要备考」）——身份单句即时进画像，
    不等攒满条数（豆包式身份提取的即时性）。先置计数防并发重复触发；
    主线程内做字符串快照，worker 不触碰 sess 对象。任何异常静默。
    """
    try:
        if not core.is_configured():
            return
        with _lock:
            if not _state["enabled"]:
                return
        msgs = sess.get("messages") or []
        n = sum(1 for m in msgs if m.get("role") == "user")
        seen = sess.get("digest_seen", 0)
        if not isinstance(seen, int) or seen < 0:
            seen = 0
        seen = min(seen, n)          # /upload 重置历史后 n 变小：回校准防负差
        last_user = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                last_user = core.strip_user_wrapper(str(m.get("content", "")))
                break
        identity_hit = bool(IDENTITY_SIGNAL_PAT.search(last_user))
        if n - seen < config.MEMORY_DIGEST_MIN_NEW and not identity_hit:
            return
        sess["digest_seen"] = n      # 先置计数再 spawn，防并发重复触发
        transcript = _extract_transcript(msgs)
        if not transcript.strip():
            return
        threading.Thread(target=_digest_worker, args=(transcript, client_id), daemon=True).start()
    except Exception:
        pass


def _extract_transcript(messages: list[dict]) -> str:
    """取料：user 消息剥指派头部（跳过空串与 [系统注入]）+ 最后一条最终回答。

    与 app.py title 共用 core.strip_user_wrapper 单一来源；tool 消息一律丢弃。
    """
    lines = []
    for m in messages:
        if m.get("role") == "user":
            t = core.strip_user_wrapper(str(m.get("content", "")))
            if not t or t.startswith("[系统注入]"):
                continue
            lines.append(f"user: {t}")
    last_answer = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and not m.get("tool_calls"):
            last_answer = str(m.get("content") or "").strip()
            break
    if last_answer:
        lines.append(f"assistant: {last_answer}")
    return "\n".join(lines)[:6000]


DIGEST_PROMPT_TMPL = """你是跨对话用户画像提炼助手。请阅读对话历史，像优秀助理一样从长对话中提炼「用户画像」——身份、目标、健康关注、偏好、重要事实，输出更新后的画像清单 JSON。

硬性规则：
1. 提炼维度（kind 取值，只允许这五类）：
   - identity 身份：用户是谁、什么阶段（如「中医专业大二学生，正在备考」）。最重要，优先提炼。
   - goal 目标：近期目标/待办（如「备考方剂学期末考试」）。
   - health 健康关注：用户自述的身体情况或关注的健康主题（如「本人长期眼睛干涩」）。只记用户自述事实，禁止辨证结论（禁止「眼睛干=肝阴虚」这类推断）。
   - preference 偏好：回答风格/用法偏好（如「喜欢带出处的回答」「常问泡水搭配」）。
   - fact 重要事实/纠错：用户纠正过的错误、值得长期记住的事实。
2. 重要性过滤：只保留对未来对话有实际帮助的信息；一次性话题（如「今天问了X药的功效」）不记录，除非反映长期关注。
3. 禁止编造：所有条目必须能在对话历史或既有清单中找到依据；**禁止出现对话中从未提到的药材、人物或内容**（幻觉条目会污染画像）。
4. 禁止记录任何药性、功效、用量、禁忌、配伍结论或知识内容——那些必须由主智能体实时查知识图谱，画像绝不背知识。
5. 输出 JSON 对象：{"entries": [{"text": "一句话中文条目（≤40字）", "herbs": ["涉及的药材名（无则 []）"], "kind": "identity|goal|health|preference|fact"}]}
6. entries 是「合并既有清单后的最终清单」：新信息并入、重复合并、过时信息可删除，总数 ≤ 20 条；无新信息则原样返回既有清单。
7. 身份更正（重要）：identity/goal 条目长期保留，**但**当对话中出现与既有身份矛盾的新身份自述时
   （如先称「医学生备考」、后称「老年人经常买药」），视为用户**更正身份**：删除旧的 identity/goal 条目，
   只保留最新身份——互相矛盾的身份不得并存。若新旧身份可兼容（如「中医学生」+「同时经营药店」）才允许并存。

既有清单：
{existing}

对话历史（最近几轮）：
{transcript}"""


def _digest_worker(transcript: str, client_id: str) -> None:
    """后台线程体：DeepSeek 无 tools 摘要 → 校验合并 → 持锁落盘。

    单次尝试零重试；任何异常（断网/超时/非200/JSON解析失败/结构非法）→ 静默 return，
    绝不写盘、绝不抛出。
    """
    try:
        with _lock:
            existing = json.dumps(
                [{"text": e["text"], "herbs": e["herbs"], "kind": e["kind"]} for e in _state["entries"]],
                ensure_ascii=False,
            )
        # ⚠️ 用 replace 而不是 str.format：模板里含 JSON 示例花括号（{"entries":...}），
        #    .format 会把它们当占位符解析直接抛异常
        prompt = (DIGEST_PROMPT_TMPL
                  .replace("{existing}", existing)
                  .replace("{transcript}", transcript))
        resp = requests.post(
            config.DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {core._api_key()}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": config.MEMORY_DIGEST_TEMP,
                "max_tokens": config.MEMORY_DIGEST_MAX_TOKENS,
                "stream": False,
                "response_format": {"type": "json_object"},   # 提示词已含「JSON」字样（API 要求）
            },
            timeout=config.LLM_TIMEOUT,
        )
        if resp.status_code != 200:
            return
        data = resp.json()
        # json_object 模式下结构化 JSON 在 choices[0].message.content（字符串）里，
        # 不在 HTTP 顶层——先解外层再 json.loads 内层
        try:
            inner = data["choices"][0]["message"].get("content", "")
            parsed = json.loads(inner) if isinstance(inner, str) else inner
        except (KeyError, IndexError, TypeError, ValueError):
            return
        entries = parsed.get("entries") if isinstance(parsed, dict) else None
        if not isinstance(entries, list):
            return
        now = time.time()
        clean = []
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            text = e.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            herbs = [h for h in (e.get("herbs") or []) if isinstance(h, str)]
            clean.append({
                # ⚠️ time.time_ns() 在同一循环迭代内可能返回同值（Windows 时钟粒度）
                #   → id 撞车（实测 DELETE 一条删两条）；追加序号保证唯一
                "id": f"mem-{time.time_ns()}-{i}",
                "ts": now,
                "text": text.strip()[:60],
                "herbs": herbs[:8],
                # kind 白名单校验（schema v2）；模型输出非法值归 preference
                "kind": e.get("kind") if e.get("kind") in MEMORY_KINDS else "preference",
                "src_session": client_id,
            })
        if not clean:
            return
        with _lock:
            _state["entries"] = clean[: config.MEMORY_MAX_ENTRIES]
            _state["updated"] = now
            _save()
    except Exception:
        pass


_load()
