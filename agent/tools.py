"""
agent/tools.py
智能体工具：函数实现 + JSON Schema（function calling 格式）+ 六子 Agent 分组。

二期 P2：6 工具 → 9 工具，按六子 Agent 重组（方案 §8.2）：
  识药 Agent（recognize_herb）
  鉴别 Agent（similar_herbs + similar_compare 新增）
  药性 Agent（get_herb_profile + search_herbs + retrieve_doc 新增）
  方剂 Agent（find_formulas_by_herb + find_formulas_by_symptom）
  安全 Agent（check_compatibility + get_herb_profile）
  学习 Agent（record_feedback 新增）

STUDY 二期（讲解子 Agent）：teach_query 新增（讲解知识库入口，kg/teach.py）。

工具 → 底层映射：
  recognize_herb         → classifier.predictor.predict_topk
  get_herb_profile       → kg.query.get_profile
  similar_herbs          → kg.query.similar_herbs
  similar_compare        → kg.query.similar_compare
  disambiguate_similar   → kg.disambiguate.disambiguate_material（相似对鉴别：提问清单/研判材料）
  check_compatibility    → kg.query.check_compatibility
  find_formulas_by_herb  → kg.query.formulas_by_herb
  find_formulas_by_symptom → kg.query.formulas_by_symptom
  search_herbs           → kg.query.search_herbs
  retrieve_doc           → kg.query.retrieve_doc（混合检索，药性 Agent 增强）
  record_feedback        → 学习台账 kg/data/feedback.json（错误样例入核对队列）
  teach_query            → kg.teach.teach_query（讲解知识库：精确课文 / 向量召回）

分发前必须经 REGISTRY 白名单校验（CLAUDE.md 关键设计 2）；core.py 按 AGENTS 过滤工具子集。
"""
import json
import time
from pathlib import Path

from config import BASE_DIR
from kg import query as kq
from kg import disambiguate as kdg  # 相似对鉴别闭环（外部鉴别资料库 similar_guide.json）
from classifier import predictor
from . import vision  # 二期 V2-A6：云端 VLM 辅助验证（可选增强，默认关闭）

FEEDBACK_PATH = BASE_DIR / "kg" / "data" / "feedback.json"


# ---------- 函数实现 ----------

def _must_str(name: str, value) -> str:
    """工具入参校验：模型偶尔传错类型，回填清晰提示而不是内部异常。"""
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"参数 {name} 必须是非空字符串")
    return value


def _recognize_herb(image_path: str) -> str:
    try:
        results = predictor.predict_topk(image_path, k=3)
    except FileNotFoundError as e:
        return f"图片识别不可用：{e}"
    except Exception as e:  # 任何推理异常都回填给模型，不让请求 500
        return f"图片识别失败：{type(e).__name__}: {e}"
    if not results:
        return "图片识别失败：模型未返回结果。"
    lines = [f"{name}（置信度 {conf:.2%}）" for name, conf in results]
    return "Top-3 识别结果：\n" + "\n".join(lines)


def _get_herb_profile(herb: str) -> str:
    return kq.get_profile(_must_str("herb", herb))


def _similar_herbs(herb: str) -> str:
    return kq.similar_herbs(_must_str("herb", herb))


def _similar_compare(herb_a: str, herb_b: str) -> str:
    return kq.similar_compare(
        _must_str("herb_a", herb_a), _must_str("herb_b", herb_b)
    )


def _disambiguate_similar(candidates: list[str], desc: str = "") -> str:
    """相似对鉴别（识别命中易混候选后追问特征用）：提问清单 / 研判材料两模式。

    代码只组织材料（维度对照/档案/来源档位），不评分不裁决——结论等级由 LLM 综合给出。
    """
    if not isinstance(candidates, list) or len(candidates) < 2:
        return "disambiguate_similar 需要至少两个候选药材名（list）。"
    cands = [_must_str("candidates[i]", c) for c in candidates]
    desc = desc if isinstance(desc, str) else ""
    return kdg.disambiguate_material(cands, desc)


def _check_compatibility(herbs: list[str]) -> str:
    if not isinstance(herbs, list) or len(herbs) < 2:
        return "check_compatibility 需要至少两个药材名（list）。"
    return kq.check_compatibility([_must_str("herbs[i]", h) for h in herbs])


def _find_formulas_by_herb(herb: str) -> str:
    return kq.formulas_by_herb(_must_str("herb", herb))


def _find_formulas_by_symptom(symptom: str) -> str:
    return kq.formulas_by_symptom(_must_str("symptom", symptom))


def _search_herbs(keys: str) -> str:
    return kq.search_herbs(_must_str("keys", keys))


def _retrieve_doc(query: str) -> str:
    return kq.retrieve_doc(_must_str("query", query))


def _teach_query(query: str) -> str:
    """讲解知识库查询（STUDY 子 Agent 唯一入口）：精确课文优先，未覆盖开放问题向量召回。"""
    from kg import teach as kteach  # 延迟导入：teach 依赖 retrieval（bge 模型），不进识药等路径
    return kteach.teach_query(_must_str("query", query))


def _vlm_verify(image_path: str) -> str:
    """云端 VLM 复核（二期 V2-A6）：图片归类的第二通道裁决（仅候选+verdict，禁药性功效）。

    未启用/断网/超时 → 返回不可用说明（fail-soft），模型以本地识别结果为准。
    """
    return vision.vlm_verify_request(_must_str("image_path", image_path))


def _record_feedback(herb: str, corrected_to: str, note: str = "") -> str:
    """学习 Agent：错误样例入核对队列（方案 §8.2 / 演示用例 12）。

    追加写 kg/data/feedback.json（版本化台账思路的反馈侧），供人工核对后回流再训练/再核对。
    """
    herb = _must_str("herb", herb)
    corrected_to = _must_str("corrected_to", corrected_to)
    entry = {
        "id": f"fb-{time.time_ns()}",  # 纳秒时间戳保证唯一（同秒多次调用不撞 id）
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "herb": herb,                    # 原识别/原知识结果
        "corrected_to": corrected_to,    # 修正意见
        "note": note or "",
        "status": "pending",             # pending → reviewed（人工核对后改）
    }
    entries = []
    if FEEDBACK_PATH.exists():
        try:
            entries = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8")).get("entries", [])
        except (json.JSONDecodeError, OSError):
            entries = []
    entries.append(entry)
    payload = {
        "note": "学习 Agent 反馈台账：错误样例入核对队列（LLM 起草 ≠ 定稿，人工核对后回流）",
        "entries": entries,
    }
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return (
        f"已记录修正意见：{herb} → {corrected_to}"
        + (f"（备注：{note}）" if note else "")
        + "。已进入人工核对队列（feedback.json），后续会回流知识库/模型再核对。"
    )


# ---------- 工具注册表（名称 + 描述 + JSON Schema + 实现 + 子 Agent 归属） ----------

TOOLS = [
    {
        "name": "recognize_herb",
        "agent": "识药",
        "description": "识别一张中药饮片图片，返回 Top-3 中文名与置信度。图片识别不可用或失败时返回说明。",
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "待识别图片的服务器本地路径",
                }
            },
            "required": ["image_path"],
        },
        "fn": _recognize_herb,
    },
    {
        "name": "vlm_verify",
        "agent": "识药",
        "description": (
            "云端视觉大模型复核一张中药饮片图片（仅当本地识别候选难以区分时调用）："
            "返回第二通道的候选归类与裁决（verdict=none/non_herb），不含药性内容。"
            "未启用或云端不可用时返回说明。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "待复核图片的服务器本地路径",
                }
            },
            "required": ["image_path"],
        },
        "fn": _vlm_verify,
    },
    {
        "name": "get_herb_profile",
        "agent": "药性",
        "description": (
            "查询单味中药的完整档案：性味、归经、功效、主治、用量、毒性、禁忌与出处"
            "（《中国药典》2020 年版一部）。图谱未收录时返回明确说明。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "herb": {
                    "type": "string",
                    "description": "中药中文名或别名，如 枸杞子、枸杞",
                }
            },
            "required": ["herb"],
        },
        "fn": _get_herb_profile,
    },
    {
        "name": "similar_herbs",
        "agent": "鉴别",
        "description": "返回与某味药同方共现的相似药材（按共现数排序）。相似鉴别类问题的第一步。",
        "parameters": {
            "type": "object",
            "properties": {
                "herb": {"type": "string", "description": "中药中文名或别名"},
            },
            "required": ["herb"],
        },
        "fn": _similar_herbs,
    },
    {
        "name": "similar_compare",
        "agent": "鉴别",
        "description": (
            "两味药的鉴别要点对比：混淆风险、本品/对比药差异点（来自 L1 相似对知识）、"
            "双方性状摘录，并给出结论等级（可判定/部分信息/需人工）。"
            "『怎么区分 / 有什么不同 / 容易混淆』类问题必须调用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "herb_a": {"type": "string", "description": "第一味中药名"},
                "herb_b": {"type": "string", "description": "第二味中药名"},
            },
            "required": ["herb_a", "herb_b"],
        },
        "fn": _similar_compare,
    },
    {
        "name": "disambiguate_similar",
        "agent": "鉴别",
        "description": (
            "相似对鉴别（识别命中易混候选、需向用户追问特征时用）："
            "不传 desc = 返回该对候选的维度对照提问清单（按『一看二摸三闻四尝』组织可观察特征，"
            "用于逐项询问用户，信息不足不强答）；"
            "传 desc（用户描述的特征）= 返回研判材料（对照表 + 档案节选 + 来源档位），"
            "供综合给出结论等级（可判定/部分信息/需人工）与依据。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "待区分的候选药材名（2 个以上，通常来自识别 Top-3）",
                },
                "desc": {
                    "type": "string",
                    "description": "用户描述的可观察特征（可选：省略 = 提问清单模式，提供 = 研判材料模式）",
                },
            },
            "required": ["candidates"],
        },
        "fn": _disambiguate_similar,
    },
    {
        "name": "check_compatibility",
        "agent": "安全",
        "description": (
            "检查两味以上药材之间的传统配伍禁忌（十八反/十九畏，区分歌诀来源与药典认定）、"
            "妊娠禁忌与毒性提示。任何组合类问题都必须调用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "herbs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要检查的药材中文名列表，至少两个",
                }
            },
            "required": ["herbs"],
        },
        "fn": _check_compatibility,
    },
    {
        "name": "find_formulas_by_herb",
        "agent": "方剂",
        "description": "按药材反查经典方剂：返回该药参与的方剂、在方中的君臣佐使角色、主治与出处。",
        "parameters": {
            "type": "object",
            "properties": {
                "herb": {"type": "string", "description": "中药中文名或别名"},
            },
            "required": ["herb"],
        },
        "fn": _find_formulas_by_herb,
    },
    {
        "name": "find_formulas_by_symptom",
        "agent": "方剂",
        "description": (
            "按症状/证候关键词推荐经典方剂（匹配方剂主治）。"
            "如『肝肾阴虚 目昏』→ 杞菊地黄丸。返回主治、组成与出处。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "symptom": {
                    "type": "string",
                    "description": "症状或证候描述，如 食积腹胀 / 肝肾阴虚,目昏",
                }
            },
            "required": ["symptom"],
        },
        "fn": _find_formulas_by_symptom,
    },
    {
        "name": "search_herbs",
        "agent": "药性",
        "description": "按性味/归经/功效反向检索药材，如『寒,肺经,清热』。逗号分隔多个关键词。",
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "逗号分隔的检索关键词"},
            },
            "required": ["keys"],
        },
        "fn": _search_herbs,
    },
    {
        "name": "retrieve_doc",
        "agent": "药性",
        "description": (
            "混合检索知识库文档（图谱精确优先，未覆盖的开放问题走向量召回）："
            "返回权威文档片段，每条带出处与内容哈希。图谱未覆盖的开放问题（如"
            "『枸杞子对眼睛的作用原理』）用本工具；无证据时返回知识缺口说明。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要检索的问题或关键词"},
            },
            "required": ["query"],
        },
        "fn": _retrieve_doc,
    },
    {
        "name": "record_feedback",
        "agent": "学习",
        "description": (
            "记录用户/专家对识别结果或知识回答的修正意见（如『这个不是枸杞子是五味子』），"
            "写入人工核对队列（feedback.json）供后续回流。用户指出错误时调用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "herb": {"type": "string", "description": "被指认错误的药材名/识别结果"},
                "corrected_to": {"type": "string", "description": "用户给出的正确药材名"},
                "note": {"type": "string", "description": "备注（可选，用户原话或补充说明）"},
            },
            "required": ["herb", "corrected_to"],
        },
        "fn": _record_feedback,
    },
    {
        "name": "teach_query",
        "agent": "讲解",
        "description": (
            "查询讲解知识库的课文内容（面向中草药学生/学徒的教学讲解）。"
            "传药材名（含别名）返回该味 8 节全量课文（概览/基原与性状/性味归经/功效与主治/"
            "应用举例/本草记载/用法用量与注意/易混与鉴别）；传开放问题（如『什么药润肺』）"
            "做语义检索。返回内容已含档位标注（A 档药典/B 档教材/C 档古籍）与出处。"
            "⚠️ 涉及古籍（本草记载）时只可原样转述工具给到的『原文摘录』quote 及其出处，"
            "禁止自行引述任何古文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "药材中文名/别名（要整节课），或教学问题（要语义检索）",
                }
            },
            "required": ["query"],
        },
        "fn": _teach_query,
    },
]

# 白名单分发表：name → 实现
REGISTRY = {t["name"]: t["fn"] for t in TOOLS}

# 供 core.py 组装的 LLM 可见 schema（OpenAI 格式：type="function" 包裹 + 去掉 fn/agent 字段）
TOOL_SCHEMAS = [
    {"type": "function", "function": {k: v for k, v in t.items() if k not in ("fn", "agent")}}
    for t in TOOLS
]

ALLOWED_TOOL_NAMES = set(REGISTRY)

# ---- 六子 Agent（方案 §8.2）：名称 / 触发说明 / 工具集 / ReAct 配额 ----
AGENTS = {
    "识药": {
        "tools": ["recognize_herb", "vlm_verify"],
        "quota": 3,
        "trigger": "用户上传图片时",
    },
    "鉴别": {
        "tools": ["similar_herbs", "similar_compare", "disambiguate_similar"],
        "quota": 4,
        "trigger": "『怎么区分/有什么不同/容易混淆/相似』类问题 + 识别命中易混对后的追问（disambig_ctx 激活）",
    },
    "药性": {
        "tools": ["get_herb_profile", "search_herbs", "retrieve_doc"],
        "quota": 4,
        "trigger": "问功效/性味/作用/用量等单药知识（默认路由）",
    },
    "方剂": {
        "tools": ["find_formulas_by_herb", "find_formulas_by_symptom"],
        "quota": 4,
        "trigger": "问经典方/配方/症状找方",
    },
    "安全": {
        "tools": ["check_compatibility", "get_herb_profile"],
        "quota": 4,
        "trigger": "涉及两药以上同用/配伍禁忌/毒性/孕妇人群",
    },
    "学习": {
        "tools": ["record_feedback", "get_herb_profile"],
        "quota": 3,
        "trigger": "用户指出识别/知识错误（『不是X是Y』『识别错了』）",
    },
    "讲解": {
        "tools": ["teach_query", "get_herb_profile", "search_herbs", "similar_compare"],
        "quota": 4,
        "max_tokens": 4096,  # 讲解长文（8 节课文组装）易被 1024/2048 截断在【本草记载】节之前，实测提到 4096
        "trigger": "讲讲/科普/学习某味药、功效归纳（什么药X）、识别后一键讲解",
    },
}

AGENT_TOOLS = {aid: a["tools"] for aid, a in AGENTS.items()}


def schemas_for(agent: str | None = None) -> list[dict]:
    """按子 Agent 过滤的 LLM schema 子集；None = 全量（一期行为兼容）。"""
    if agent is None:
        return TOOL_SCHEMAS
    allowed = set(AGENT_TOOLS.get(agent, []))
    return [s for s in TOOL_SCHEMAS if s["function"]["name"] in allowed]


def call_tool(name: str, arguments: dict) -> str:
    """白名单校验 + 调用 + 异常兜底（异常回填给模型自纠）。"""
    if name not in REGISTRY:
        return f"未知工具「{name}」，请从可用工具中选择。"
    try:
        return REGISTRY[name](**arguments)
    except TypeError as e:
        # 参数不匹配（模型传错参数）→ 回填提示让模型自纠
        return f"工具「{name}」参数错误：{e}。请核对工具参数后重试。"
    except Exception as e:
        return f"工具「{name}」执行异常：{type(e).__name__}: {e}"


def tool_summary(name: str, arguments: dict) -> str:
    """工具调用摘要（前端时间轴展示用，SSE 推给页面）。"""
    try:
        arg = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        arg = str(arguments)
    return f"{name}({arg})"


def agent_of(tool_name: str) -> str:
    """工具 → 归属子 Agent（时间轴展示用）。"""
    for t in TOOLS:
        if t["name"] == tool_name:
            return t["agent"]
    return ""
