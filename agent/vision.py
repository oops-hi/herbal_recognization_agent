"""
agent/vision.py
云端 VLM 辅助验证通道（二期 V2-A6，方案 §8.5，可选增强，默认关闭）。

目的：给本地 yolo8n-cls 识别加第二通道，补一期两个已知漏洞——
① 语义级相似混淆（桃仁 recall 0.30 → 苦杏仁/川楝子、草豆蔻 0.45 → 砂仁）；
② 域外图高置信误判（宠物图最高 0.94，单阈值无法全分离）。

架构：OpenAI 兼容 chat/completions（image_url + base64），与聊天通道同构，
供应商可随时换（设置页 VISION 组配置，默认 qwen-vl-max / 阿里云百炼）。

合规边界（红线）：
- VLM 只输出候选名单内药材名 + verdict（none/non_herb），绝不生成药性/功效/用量/产地；
- 返回做结构白名单校验（20 类名 ∪ 别名；含药性功效文本整段丢弃）；
- VLM 结果不进知识问答路径——只影响"识别结论怎么呈现（提信/拒答/域外图）"；
- 图片仅临时 base64 发送，不落库；VISION_ENABLED=False 默认关闭。

fail-soft：断网/超时/未配置/异常 → state=unavailable，回退本地结论，绝不抛 500。
"""
import base64
import io
import json
import os
import re
import time
from pathlib import Path

import requests
from PIL import Image

import config  # 设置页 save_vision_config() 热更新后本模块即时生效（现读模块属性）

# ---- 灰区 / 高风险混淆对（方案 §8.5 触发条件） ----
LOW_CONF_BOUND = (0.60, 0.90)          # T1 低置信区间
HIGH_RISK_PAIRS = {                    # T2 一期已知弱类/混淆组
    "桃仁", "苦杏仁", "川楝子",         # 桃仁 recall 0.30 主流向
    "砂仁", "豆蔻", "草豆蔻",           # 草豆蔻 recall 0.45 → 砂仁
    "山楂", "金樱子",                   # 跨域混淆组
}
TOP_GAP = 0.05                         # T3 候选差距阈值

# verdict 白名单（方案 §8.5：仅两种强裁定；正常识别走 candidates 非空）
VERDICTS = {"none", "non_herb"}
# 越权输出扫描：VLM 文本含这些词段 → 整段丢弃（防幻觉/越权输出药性功效）
BANNED_FRAG = ("功效", "性味", "归经", "用量", "主治", "毒性", "禁忌", "产地", "治疗")


def _load_class_names() -> list[str]:
    """20 类中文名（class_map.json；失败给空表 → 白名单校验整体不可用）。"""
    try:
        data = json.loads(config.CLASS_MAP.read_text(encoding="utf-8"))
        return [v["name_cn"] for v in data.values()]
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def _load_aliases() -> dict[str, str]:
    """别名 → 标准名 归一表（镜像目录名拼写兜底，如 枸杞 → 枸杞子）。"""
    aliases = {}
    try:
        data = json.loads(config.CLASS_MAP.read_text(encoding="utf-8"))
        for v in data.values():
            aliases[v["name_cn"]] = v["name_cn"]
            for a in v.get("aliases", []):
                aliases[a] = v["name_cn"]
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return aliases


_CLASS_NAMES = _load_class_names()
_ALIASES = _load_aliases()


def is_enabled() -> bool:
    """VLM 通道是否开启（设置页开关 + .env，热更新后立即生效）。"""
    return config.VISION_ENABLED and bool(os.environ.get("VISION_API_KEY"))


def should_use_vision(top3: list[tuple[str, float]]) -> bool:
    """灰区三条件（T1 低置信 / T2 高风险混淆对 / T3 候选差距小），任一命中才调用云端。

    - conf < 0.60 直接拒答不走云端（省成本，app.py low_confidence 分支兜底）
    - 0.90+ 本地可信不打扰（T2 混淆对除外——高置信照样可能认错，如宠物图 0.94）
    """
    if not top3:
        return False
    top1, conf = top3[0]
    if conf < LOW_CONF_BOUND[0]:
        return False
    if top1 in HIGH_RISK_PAIRS:                      # T2
        return True
    if conf < LOW_CONF_BOUND[1]:                     # T1
        return True
    if len(top3) >= 2 and conf - top3[1][1] < TOP_GAP:  # T3（Top-1 与 Top-2 差距小）
        return True
    return False


def _fix_exif(img: Image.Image) -> Image.Image:
    """修正 exif 旋转方向（手机实拍图常见 90°/270° 横竖翻转）。"""
    try:
        orient = img.getexif().get(0x0112, 1)
        if orient == 3:
            img = img.rotate(180, expand=True)
        elif orient == 6:
            img = img.rotate(270, expand=True)
        elif orient == 8:
            img = img.rotate(90, expand=True)
    except Exception:
        pass
    return img


def _encode_image(path: str | Path) -> str | None:
    """PIL 预处理 → base64 data URL：最长边 ≤1024、JPEG 质量自适应 ≤1.5MB。

    坏图/不可读返回 None（调用方按 unavailable 处理）。
    """
    try:
        with Image.open(path) as img:
            img = _fix_exif(img)
            img.thumbnail((1024, 1024))
            for quality in (85, 70, 55, 40):
                buf = io.BytesIO()
                img.convert("RGB").save(buf, "JPEG", quality=quality)
                if buf.tell() <= 1_500_000:
                    break
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except (OSError, ValueError, AttributeError):
        return None


def _has_banned(content: str) -> bool:
    """越权输出扫描：药性/功效/用量等词段出现即视为违规。"""
    return any(f in content for f in BANNED_FRAG)


def _build_prompt() -> str:
    """系统提示词（方案 §8.5：仅候选+裁决，禁药性/功效/用量/产地）。"""
    names = "、".join(_CLASS_NAMES) if _CLASS_NAMES else "（候选名单加载失败）"
    return (
        "你是中药饮片图像复核助手。请判断图片是否为下列 20 类果实种子类中药饮片之一："
        f"{names}。\n"
        "规则：\n"
        "1. 只输出 JSON，格式 {\"candidates\": [\"药材名\"], \"verdict\": \"none\" 或 \"non_herb\"}；\n"
        "2. candidates 只允许上述名单里的药材名（可多个，按匹配度排序）；\n"
        "3. 图片不是任何一类药材（如动物/风景/人像/其他物品）→ verdict 填 non_herb，candidates 留空；\n"
        "4. 看不出、不确定 → verdict 填 none，candidates 留空；\n"
        "5. 严禁输出药性、功效、用量、主治、产地等任何知识内容，只做图像归类判断。"
    )


def verify(image_path: str | Path) -> dict:
    """调用云端 VLM，返回规范化结果（绝不抛异常，fail-soft）。

    返回 dict：
      {"state": "ok", "candidates": [归一化中文名], "verdict": "none"|"non_herb"}
      {"state": "unavailable", "reason": "..."}     # 断网/超时/未配置/白名单校验失败
    """
    if not is_enabled():
        return {"state": "unavailable", "reason": "vision_disabled"}
    if not _CLASS_NAMES:
        return {"state": "unavailable", "reason": "class_map_missing"}

    data_url = _encode_image(image_path)
    if not data_url:
        return {"state": "unavailable", "reason": "image_decode_failed"}

    payload = {
        "model": config.VISION_MODEL,
        "messages": [
            {"role": "system", "content": _build_prompt()},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请按规则判断这张图片属于哪一类中药饮片（或不是药材）。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},  # 不支持的端点 400 → 去掉重试
    }

    def _post(use_format: bool) -> requests.Response:
        body = dict(payload)
        if not use_format:
            body.pop("response_format", None)
        return requests.post(
            config.VISION_API_URL,
            headers={
                "Authorization": f"Bearer {os.environ.get('VISION_API_KEY')}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=config.VISION_TIMEOUT,
        )

    resp = _post(True)
    if resp.status_code == 429:
        time.sleep(3)  # 单次退避重试（同聊天通道）
        resp = _post(True)
    if resp.status_code in (400, 404) and "response_format" in payload:
        # 端点不支持 json_object → 去掉 response_format 重试（部分兼容代理）
        resp = _post(False)
    if resp.status_code == 402:
        return {"state": "unavailable", "reason": "billing_402"}
    if resp.status_code != 200:
        return {"state": "unavailable", "reason": f"http_{resp.status_code}"}
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        return {"state": "unavailable", "reason": "bad_response"}

    parsed = _parse_structured(content)
    if parsed is None:
        return {"state": "unavailable", "reason": "parse_failed"}
    return parsed


def _parse_structured(content: str) -> dict | None:
    """解析 + 结构白名单校验（防幻觉/越权输出）。

    - verdict 必须 ∈ {none, non_herb}
    - candidates 元素必须 ∈ 20 类名 ∪ 别名（归一化）；有候选但全在名单外 → 整体不可用
    - 文本含药性/功效/用量等词段 → 整段丢弃
    """
    if not content or _has_banned(content):
        return None
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", content)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None
    verdict = data.get("verdict", "")
    if verdict not in VERDICTS:
        return None

    cands = data.get("candidates") or []
    if not isinstance(cands, list):
        cands = []
    names = [_ALIASES.get(str(c).strip()) for c in cands]
    names = [n for n in names if n]                # 名单外名字丢弃（归一化）
    if cands and not names:
        return None                                # 有候选但全在名单外 → 不可信
    return {"state": "ok", "candidates": names[:5], "verdict": verdict}


def decide(local_top3: list[tuple[str, float]], vlm: dict) -> dict:
    """双通道决策矩阵（方案 §8.5）。local_top3 = [(中文名, conf)]，vlm = verify() 返回值。

    返回 vision_meta（app.py 挂到 upload 响应）：
      {"state": "consistent", "vlm_top1", "verdict"}    # 双通道一致 → 提信
      {"state": "conflict",    "vlm_top1", "verdict"}   # 分歧 → 置信不足 + 双通道对比（级联鉴别压后）
      {"state": "non_herb", "vlm_top1"}                 # 域外图拒答（refuse_reason=域外图）
      {"state": "none", "vlm_top1"}                     # 都不像 → 置信不足 + 补拍建议
      {"state": "unavailable", "reason"}                # fail-soft 回退本地（含未启用/超时）
    """
    if vlm.get("state") != "ok":
        return dict(vlm)                                # unavailable 原样透传
    if not local_top3:
        return {"state": "unavailable", "reason": "no_local_result"}

    vlm_top1 = (vlm.get("candidates") or [""])[0]
    local_top1 = local_top3[0][0]
    # 优先级：域外图强裁定 > 候选匹配（含一致/分歧）> 不确定 > 无候选兜底
    if vlm.get("verdict") == "non_herb":
        return {"state": "non_herb", "vlm_top1": vlm_top1, "verdict": "non_herb"}
    if vlm_top1:
        if vlm_top1 == local_top1:
            return {"state": "consistent", "vlm_top1": vlm_top1, "verdict": vlm.get("verdict", "")}
        return {"state": "conflict", "vlm_top1": vlm_top1, "verdict": vlm.get("verdict", "")}
    if vlm.get("verdict") == "none":
        return {"state": "none", "vlm_top1": vlm_top1, "verdict": "none"}
    return {"state": "unavailable", "reason": "no_candidates"}


def vlm_verify_request(image_path: str | Path) -> str:
    """工具层薄包装：verify + 人类可读字符串（vlm_verify 工具返回给模型用）。"""
    r = verify(image_path)
    if r["state"] == "ok":
        cand = "、".join(r.get("candidates") or [])
        return f"云端 VLM 复核：候选 [{cand}]，verdict={r.get('verdict', '')}。"
    return f"云端 VLM 复核不可用（{r.get('reason', 'unknown')}），以本地识别结果为准。"
