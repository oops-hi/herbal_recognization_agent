"""
evaluate.py
跨域测试：Dataset2（药房实拍手机图，400 张）整体作测试集（与数据集原论文协议一致）。

输出：Top-1/Top-3 准确率、per-class Precision/Recall/F1 表格、
     混淆矩阵 PNG（中文正常渲染）→ runs/evaluate/confusion_matrix.png
     三组易混对（砂仁/豆蔻/草豆蔻、苦杏仁/桃仁、山楂/金樱子）交叉混淆统计。
"""
import json
from pathlib import Path

from ultralytics import YOLO  # ⚠️ 必须最先导入 torch 系：matplotlib/scipy 先加载会与 torch DLL 冲突（WinError 1114）

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import classification_report, confusion_matrix

from config import BASE_DIR, CLASS_MAP, CLS_DIR, IMG_SIZE, MODEL_PATH

# Windows 中文字体（无此设置中文全变方框）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

EVAL_DIR = BASE_DIR / "runs" / "evaluate"

# 三组易混对（docs 7.2）：输出统计时重点观察
CONFUSE_GROUPS = [
    ("砂仁", "豆蔻", "草豆蔻"),
    ("苦杏仁", "桃仁"),
    ("山楂", "金樱子"),
]


def load_class_names() -> list[str]:
    data = json.loads(CLASS_MAP.read_text(encoding="utf-8"))
    return [entry["name_cn"] if isinstance(entry, dict) else entry
            for entry in data.values()]


def collect_predictions(model, class_names: list[str]) -> tuple[list[int], list[list[int]]]:
    """遍历 test/ 每类目录做预测，返回 (真实标签, 每图 Top-5 索引)。"""
    y_true, y_topk = [], []
    test_root = CLS_DIR / "test"
    class_dirs = sorted(p for p in test_root.iterdir() if p.is_dir())
    assert len(class_dirs) == len(class_names), "test 类别数 != class_map 类别数"

    for dir_id, cls_dir in enumerate(class_dirs):
        imgs = sorted(cls_dir.iterdir())
        for img in imgs:
            result = model.predict(str(img), imgsz=IMG_SIZE, verbose=False)[0]
            probs = result.probs
            if probs is None:
                continue
            y_true.append(dir_id)
            y_topk.append(list(probs.top5))
    return y_true, y_topk


def print_confuse_stats(cm: np.ndarray, class_names: list[str]) -> None:
    """打印三组易混对的相互误判次数（对称计数，各算一次）。"""
    idx = {name: i for i, name in enumerate(class_names)}
    print("\n[易混对交叉误判]（对角线上为正确，越集中在对角线越好）")
    for group in CONFUSE_GROUPS:
        ids = [idx[n] for n in group if n in idx]
        if len(ids) < 2:
            continue
        header = "/".join(group)
        stats = []
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                ca, cb = ids[a], ids[b]  # ⚠️ 必须转成真实类别索引，不能直接用组内位置
                stats.append(f"{group[a]}↔{group[b]}={int(cm[ca, cb] + cm[cb, ca])}")
        print(f"  {header}: " + "  ".join(stats))


def plot_confusion_matrix(cm: np.ndarray, class_names: list[str], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(len(class_names)), class_names, rotation=90)
    ax.set_yticks(range(len(class_names)), class_names)
    ax.set_xlabel("预测类别"); ax.set_ylabel("真实类别")
    ax.set_title("跨域测试混淆矩阵（Dataset2 药房实拍，400 张）")
    for i in range(len(class_names)):
        for j in range(len(class_names)):
            v = cm[i, j]
            if v:
                ax.text(j, i, str(v), ha="center", va="center",
                        fontsize=7, color="white" if v > cm.max() / 2 else "black")
    fig.colorbar(im, shrink=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"混淆矩阵已保存: {path}")


def main() -> None:
    if not MODEL_PATH.exists():
        raise SystemExit(f"模型不存在: {MODEL_PATH}（先运行 train.py）")
    if not CLASS_MAP.exists():
        raise SystemExit(f"类别映射不存在: {CLASS_MAP}（先运行 prepare_dataset.py）")

    class_names = load_class_names()
    print(f"加载模型: {MODEL_PATH}")
    model = YOLO(str(MODEL_PATH))
    import torch  # ultralytics 已导入，此处仅做设备检测
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"推理设备: {device}\n")

    y_true, y_topk = collect_predictions(model, class_names)
    n = len(y_true)
    top1 = sum(t[0] == g for g, t in zip(y_true, y_topk)) / n
    top3 = sum(g in t[:3] for g, t in zip(y_true, y_topk)) / n

    print(f"样本数: {n}（期望 400）")
    print(f"Top-1 准确率: {top1:.4f}   Top-3 准确率: {top3:.4f}")

    # per-class 指标（列按 class_names 顺序，行按出现顺序，用 labels 固定顺序）
    report = classification_report(
        y_true, [t[0] for t in y_topk], labels=range(len(class_names)),
        target_names=class_names, digits=3, zero_division=0,
    )
    print("\n[per-class 指标]\n" + report)

    cm = confusion_matrix(y_true, [t[0] for t in y_topk],
                          labels=range(len(class_names)))
    print_confuse_stats(cm, class_names)

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    plot_confusion_matrix(cm, class_names, EVAL_DIR / "confusion_matrix.png")


if __name__ == "__main__":
    main()  # Windows 多进程/DataLoader 需 __main__ 保护
