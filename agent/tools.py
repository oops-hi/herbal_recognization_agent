"""
agent/tools.py
智能体 6 个工具：函数实现 + JSON Schema（function calling 格式）。

工具 → 底层映射：
  recognize_herb         → classifier.predictor.predict_topk
  get_herb_profile       → kg.query.get_profile
  check_compatibility    → kg.query.check_compatibility
  find_formulas_by_herb  → kg.query.formulas_by_herb
  find_formulas_by_symptom → kg.query.formulas_by_symptom
  search_herbs           → kg.query.search_herbs

分发前必须经 REGISTRY 白名单校验（CLAUDE.md 关键设计 2）。
"""
import json
from pathlib import Path

from kg import query as kq
from classifier import predictor


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


# ---------- 工具注册表（名称 + 描述 + JSON Schema + 实现） ----------

TOOLS = [
    {
        "name": "recognize_herb",
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
        "name": "get_herb_profile",
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
        "name": "check_compatibility",
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
]

# 白名单分发表：name → 实现
REGISTRY = {t["name"]: t["fn"] for t in TOOLS}

# 供 core.py 组装的 LLM 可见 schema（OpenAI 格式：type="function" 包裹 + 去掉 fn 字段）
TOOL_SCHEMAS = [
    {"type": "function", "function": {k: v for k, v in t.items() if k != "fn"}}
    for t in TOOLS
]

ALLOWED_TOOL_NAMES = set(REGISTRY)


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
