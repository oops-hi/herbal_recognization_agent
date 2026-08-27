"""
train.py
yolov8n-cls 预训练权重微调（镜像 pet_recognition 写法，但走 GPU）。

数据：data/herbs_cls（prepare_dataset.py 产物，类目录为 0~19 补零命名）
输出：runs/herb_cls/weights/best.pt → 复制到 models/best.pt（app.py 加载）

注意：预训练权重必须是 yolov8n-cls.pt（带 v！），已在项目根目录，勿重新下载。
"""
import os
from pathlib import Path

os.environ.setdefault("YOLO_OFFLINE", "true")  # 断网环境：跳过更新检查/字体下载（否则启动挂死）

from ultralytics import YOLO

from config import BASE_DIR, CLS_DIR, IMG_SIZE, MODELS_DIR, PRETRAINED


def main() -> None:
    assert Path(PRETRAINED).exists(), f"预训练权重不存在: {PRETRAINED}"

    model = YOLO(PRETRAINED)

    results = model.train(
        data=str(CLS_DIR),          # 分类格式数据集根目录
        epochs=150,
        imgsz=IMG_SIZE,             # 224，与推理端一致
        batch=64,                   # yolov8n-cls 在 8GB 显存下很宽裕
        patience=30,                # 提前停止
        device=0,                   # RTX 5060 Laptop（cu128，sm_120）
        project=str(BASE_DIR / "runs"),
        name="herb_cls",
        pretrained=True,
        seed=42,
        verbose=True,
        # 温和增强（饮片纹理细节重要，且存在三组易混对，不过度增强）
        fliplr=0.5,
        scale=0.3,
        hsv_h=0.015,
        hsv_s=0.3,
        hsv_v=0.3,
    )

    # 训练产物 → models/best.pt
    best = Path(results.save_dir) / "weights" / "best.pt"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dst = MODELS_DIR / "best.pt"
    dst.write_bytes(best.read_bytes())
    print(f"\n模型已保存: {dst}")


if __name__ == "__main__":
    main()  # Windows DataLoader 必须包在 __main__ 里
