"""
config.py
全局配置：路径、阈值、模型名统一在此管理。

打包（PyInstaller frozen）分支：
- BASE_DIR 指向 sys._MEIPASS（只读资产根：models/、kg/data/kg.json 随 datas 打进去）
- 可写目录（uploads/）与用户密钥（.env）改到 exe 旁 —— _MEIPASS 是临时目录，退出即删
"""
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    BASE_DIR   = Path(sys._MEIPASS)                     # 只读资产根（datas 落点）
    EXE_DIR    = Path(sys.executable).resolve().parent  # exe 所在目录（可持久写）
    UPLOAD_DIR = EXE_DIR / "uploads"
    ENV_PATH   = EXE_DIR / ".env"                       # 用户自放密钥；.env 绝不打包
else:
    BASE_DIR   = Path(__file__).resolve().parent
    UPLOAD_DIR = BASE_DIR / "uploads"
    ENV_PATH   = BASE_DIR / ".env"

# 加载 .env（统一在此加载一次；agent/core.py 不再自管）。
# 幂等：已有系统环境变量优先（load_dotenv 默认 override=False）。
load_dotenv(ENV_PATH)

# ---- 数据 ----
DATA_DIR = BASE_DIR / "data"
RAW_DIR  = DATA_DIR / "raw"          # NB-TCM-CHM 解压目录（手动下载，见 data/raw/README.md）
CLS_DIR  = DATA_DIR / "herbs_cls"    # prepare_dataset.py 产物：train/val/test

# ---- 模型 ----
MODELS_DIR   = BASE_DIR / "models"
MODEL_PATH   = MODELS_DIR / "best.pt"        # 训练产物（app.py 加载）
CLASS_MAP    = MODELS_DIR / "class_map.json" # 类别名 + aliases（镜像目录名不一致兜底）
PRETRAINED   = "yolov8n-cls.pt"              # 预训练权重（带 v！不是 yolo8n-cls.pt），已下载到项目根目录

# ---- 识别 ----
IMG_SIZE          = 224
# FR-03 拒答阈值：0.6 → 0.75（2026-08-27 实测调优）
#   Dataset2 400 张：0.6 保留 92%/答对率 90%；0.75 保留 86%/答对率 94%
#   宠物等域外图高置信误判（最高 0.94 = 木瓜，beagle 也 0.938），0.75 能拒掉约 6/10 类宠物图；
#   单阈值无法全分离（枸杞子演示图 0.94 必须过），演示 5 选低置信宠物图（Bombay 0.28 / Birman 0.487 / British_Shorthair 0.468）
LOW_CONF_THRESHOLD = 0.75

# ---- 大模型 ----
# env 优先 + 默认兜底：设置页保存的 .env 值在此生效，且后续 save_llm_config()
# 会直接改模块属性实现热重载。URL/MODEL 以前只认默认值（.env 里手写的被无视），
# 现在与设置页同源。LLM_TIMEOUT/MAX_TURNS 不开放配置，保持固定。
DEEPSEEK_API_URL = os.environ.get("DEEPSEEK_API_URL") or "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"
LLM_TIMEOUT      = 60       # 秒（多轮 ReAct 单次调用上限）
MAX_TURNS        = 8        # 智能体单次回答最大工具轮次

# ---- LLM 配置持久化（设置页 · 应用内热更新） ----
_EP_SUFFIX = "/chat/completions"


def normalize_endpoint(base_url):
    """用户输入的 API 地址 → 完整 chat/completions 端点。

    - 兼容两种输入：https://api.deepseek.com（补后缀）或完整 URL（原样）
    - 必须 http(s) 前缀，否则 ValueError（调用方转 400 中文提示）
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("API 地址不能为空")
    if not url.startswith(("http://", "https://")):
        raise ValueError("API 地址必须以 http:// 或 https:// 开头")
    return url if url.endswith(_EP_SUFFIX) else url + _EP_SUFFIX


def _mask_key(key):
    """掩码展示：sk-abc...wxyz，短 key 只留首字符。"""
    if not key:
        return ""
    return key[:3] + "****" + key[-4:] if len(key) > 8 else key[:1] + "****"


def llm_state():
    """当前 LLM 配置快照（设置页回显 + 掩码，绝不返回明文 key）。"""
    return {
        "configured": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "base_url": DEEPSEEK_API_URL[: -len(_EP_SUFFIX)] if DEEPSEEK_API_URL.endswith(_EP_SUFFIX) else DEEPSEEK_API_URL,
        "model": DEEPSEEK_MODEL,
        "key_masked": _mask_key(os.environ.get("DEEPSEEK_API_KEY", "")),
        "defaults": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    }


def _rewrite_env(entries):
    """把 {键: 值} 合入 .env 文本：命中的键覆盖（值 None = 删除该行），其余行原样保留。"""
    lines = (ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else "").splitlines()
    key_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    out, seen = [], set()
    for line in lines:
        m = key_re.match(line.strip())
        if m and m.group(1) in entries:
            seen.add(m.group(1))
            value = entries[m.group(1)]
            if value is not None:
                out.append(f"{m.group(1)}={value}")
            continue
        out.append(line)
    for k, v in entries.items():  # 文件里没有的键 → 追加
        if v is not None and k not in seen:
            out.append(f"{k}={v}")
    return "\n".join(out) + "\n"


def save_llm_config(base_url, api_key, model, clear_key=False):
    """保存 LLM 配置：写 .env → 更新模块属性 + os.environ（立即生效，无需重启）。

    - 空 api_key（未勾清除）＝保留已保存的 key，防「只改地址就把 key 误清」
    - clear_key=True → 从 .env 删除 key 行
    - 写文件失败（如装到 Program Files 只读目录）→ ValueError，调用方转 400
    """
    global DEEPSEEK_API_URL, DEEPSEEK_MODEL
    ep = normalize_endpoint(base_url)
    model = (model or "").strip()
    if not model:
        raise ValueError("模型名不能为空")

    current_key = os.environ.get("DEEPSEEK_API_KEY", "")
    new_key = (api_key or "").strip() if not clear_key else ""
    if not new_key and not clear_key:
        new_key = current_key  # 留空 = 保留现有 key

    entries = {"DEEPSEEK_API_URL": ep, "DEEPSEEK_MODEL": model}
    if new_key or clear_key:
        entries["DEEPSEEK_API_KEY"] = new_key or None  # None → 删行
    try:
        ENV_PATH.write_text(_rewrite_env(entries), encoding="utf-8")
    except OSError as e:
        raise ValueError(f"配置文件不可写：{ENV_PATH}（{e}）。请检查安装目录权限后重试。")

    # 模块属性 + 环境变量同步（热重载：agent/core.py 每次调用现读 os.environ）
    DEEPSEEK_API_URL = ep
    DEEPSEEK_MODEL = model
    if new_key:
        os.environ["DEEPSEEK_API_KEY"] = new_key
    elif clear_key:
        os.environ.pop("DEEPSEEK_API_KEY", None)

# ---- Web ----
MAX_UPLOAD_MB = 16          # 上传上限，与 pet_recognition 一致
ALLOWED_EXT   = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# ---- 云端 VLM 辅助验证（二期 V2-A6，方案 §8.5） ----
# 与聊天 LLM 配置完全独立（VISION_* 组）：OpenAI 兼容端点 + 独立 key。
# 仅灰区触发（agent/vision.py should_use_vision），默认关闭（VISION_ENABLED=false）。
VISION_API_URL = os.environ.get(
    "VISION_API_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",  # 阿里云百炼（qwen-vl-max）
)
VISION_MODEL   = os.environ.get("VISION_MODEL") or "qwen-vl-max"
VISION_TIMEOUT = 5          # 秒；超时即跳过（fail-soft），识别/图谱/对话不受影响
VISION_ENABLED = os.environ.get("VISION_ENABLED", "false").lower() == "true"


def vision_state():
    """VLM 配置快照（设置页回显 + 掩码，绝不返回明文 key）。"""
    return {
        "enabled": VISION_ENABLED,
        "configured": bool(os.environ.get("VISION_API_KEY")),
        "base_url": VISION_API_URL[: -len(_EP_SUFFIX)] if VISION_API_URL.endswith(_EP_SUFFIX) else VISION_API_URL,
        "model": VISION_MODEL,
        "key_masked": _mask_key(os.environ.get("VISION_API_KEY", "")),
        "defaults": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-vl-max",
        },
    }


def save_vision_config(base_url, api_key, model, enabled, clear_key=False):
    """保存 VLM 配置：写 .env（VISION_* 键）→ 更新模块属性 + os.environ（立即生效）。

    与 save_llm_config 同模式：空 api_key（未勾清除）＝保留已保存 key。
    enabled 由前端复选框布尔值传入，写入 .env 后下次启动同样生效。
    """
    global VISION_API_URL, VISION_MODEL, VISION_ENABLED
    ep = normalize_endpoint(base_url)
    model = (model or "").strip()
    if not model:
        raise ValueError("模型名不能为空")

    current_key = os.environ.get("VISION_API_KEY", "")
    new_key = (api_key or "").strip() if not clear_key else ""
    if not new_key and not clear_key:
        new_key = current_key  # 留空 = 保留现有 key

    entries = {
        "VISION_API_URL": ep,
        "VISION_MODEL": model,
        "VISION_ENABLED": "true" if enabled else "false",
    }
    if new_key or clear_key:
        entries["VISION_API_KEY"] = new_key or None  # None → 删行
    try:
        ENV_PATH.write_text(_rewrite_env(entries), encoding="utf-8")
    except OSError as e:
        raise ValueError(f"配置文件不可写：{ENV_PATH}（{e}）。请检查安装目录权限后重试。")

    # 模块属性 + 环境变量同步（热重载：agent/vision.py 每次调用现读）
    VISION_API_URL = ep
    VISION_MODEL = model
    VISION_ENABLED = bool(enabled)
    if new_key:
        os.environ["VISION_API_KEY"] = new_key
    elif clear_key:
        os.environ.pop("VISION_API_KEY", None)
