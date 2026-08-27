"""
config.py
全局配置：路径、阈值、模型名统一在此管理。
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

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
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL   = "deepseek-chat"
LLM_TIMEOUT      = 60       # 秒（多轮 ReAct 单次调用上限）
MAX_TURNS        = 8        # 智能体单次回答最大工具轮次

# ---- Web ----
UPLOAD_DIR    = BASE_DIR / "uploads"
MAX_UPLOAD_MB = 16          # 上传上限，与 pet_recognition 一致
ALLOWED_EXT   = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
