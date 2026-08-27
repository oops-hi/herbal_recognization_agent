"""
classifier/predictor.py
YOLOv8n-cls 加载 → predict_topk() → [(中文名, 置信度), ...]。

⚠️ best.pt 为训练产物，尚未存在时 predict_topk 返回错误信息（fail-soft），
   不让调用方 500；模型就绪后即插即用，无需改代码。
"""
from pathlib import Path

from config import MODEL_PATH, CLASS_MAP, IMG_SIZE

_model = None
_class_names: list[str] = []


def _load():
    global _model, _class_names
    if _model is not None:
        return _model
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"模型文件不存在：{MODEL_PATH}（请先运行 train.py 训练）")
    if not CLASS_MAP.exists():
        raise FileNotFoundError(f"类别映射不存在：{CLASS_MAP}")

    from ultralytics import YOLO  # 延迟导入：无模型时无需 ultralytics

    import json

    data = json.loads(CLASS_MAP.read_text(encoding="utf-8"))
    # class_map.json: {class_id: {name_cn, aliases}} 或 {class_id: name_cn}，两者兼容
    names = []
    for i in range(len(data)):
        entry = data[str(i)]
        names.append(entry["name_cn"] if isinstance(entry, dict) else entry)
    _class_names = names

    _model = YOLO(str(MODEL_PATH))
    return _model


def predict_topk(image_path: str | Path, k: int = 3) -> list[tuple[str, float]]:
    """返回 [(中文名, 置信度), ...]（Top-k）。模型/类别映射缺失时抛出带说明的异常。"""
    model = _load()
    results = model.predict(
        str(image_path),
        imgsz=IMG_SIZE,
        verbose=False,
        device="cuda" if model.predictor.device.type == "cuda" else "cpu",
    )[0]
    probs = results.probs  # ultralytics 分类结果：probs.top1 / probs.data
    if probs is None:
        return []
    top_indices = probs.top5[:k]
    return [
        (_class_names[i], round(float(probs.data[i]), 4))
        for i in top_indices
        if i < len(_class_names)
    ]


def is_ready() -> tuple[bool, str]:
    """模型是否就绪；未就绪时返回 (False, 原因)。"""
    if not MODEL_PATH.exists():
        return False, f"模型未就绪：{MODEL_PATH} 不存在（先训练）"
    if not CLASS_MAP.exists():
        return False, f"类别映射未就绪：{CLASS_MAP} 不存在（先训练）"
    return True, "ok"
