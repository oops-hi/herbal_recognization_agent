"""
prepare_dataset.py
原始 NB-TCM-CHM 数据 → ultralytics 分类格式 + class_map.json。

- Dataset1（3384 张网络图）按类 9:1 分层切分 → data/herbs_cls/{train,val}/<class_id>/
- Dataset2（400 张药房实拍手机图）整体 → data/herbs_cls/test/<class_id>/（跨域测试）
- 生成 models/class_map.json：{class_id: {name_cn, aliases}}（Web 层按 id 映射中文名）

布局遵循 ultralytics 分类数据集规范：<root>/{train,val}/<类别目录>/ 图片。
类别 id = 文件夹名排序后的序号；类别目录用 0~19 补零命名，保证
ultralytics 按目录名排序得到的类别索引与 class_map.json 的 id 一致。
"""
import json
import random
import shutil
from pathlib import Path

from config import CLS_DIR, MODELS_DIR, RAW_DIR

# 20 类映射：镜像文件夹名（含镜像侧拼写错误，如实照录）→ (中文名, 常用别名)
# 中文名/别名依据 docs/需求分析.md 7.2（《中国药典》2020 年版一部）
CLASS_TABLE: dict[str, tuple[str, list[str]]] = {
    "Alpiniae_Katsumadai_Semen": ("草豆蔻", ["草蔻"]),
    "Amomi_Fructus":              ("砂仁", ["春砂仁"]),
    "Amomi_Fructus_Rotundus":     ("豆蔻", ["白豆蔻"]),
    "Armeniacae_Semen_Amarum":    ("苦杏仁", ["杏仁"]),
    "Chaenomelis_Fructus":        ("木瓜", []),
    "Corni_Fructus":              ("山茱萸", ["山萸肉"]),
    "Crataegi_Fructus":           ("山楂", []),
    "Foeniculi_Fructus":          ("小茴香", ["茴香"]),
    "Forsythiae_Fructus":         ("连翘", ["连翘壳"]),
    "Gardeniae_Fructus":          ("栀子", ["山栀子"]),
    "Kochiae_Fructus":            ("地肤子", []),
    "Lycii_Fructus":              ("枸杞子", ["枸杞"]),
    "Mume_Fructus":               ("乌梅", []),
    "Persicae_Semen":             ("桃仁", []),
    "Psoraleae_Fructus":          ("补骨脂", ["破故纸"]),
    "Rosae_Laevigatae_Frucyus":   ("金樱子", []),
    "Rubi_Fructus":               ("覆盆子", []),
    "Schisandrae_Chinensis_Fructus": ("五味子", ["北五味子"]),
    "Toosendan_Fructus":          ("川楝子", ["金铃子"]),
    "Trichosanthis_Pericarpoium": ("瓜蒌皮", ["栝楼皮"]),  # 栝楼皮=瓜蒌皮，与 kg 别名索引一致
}


def _images(class_dir: Path) -> list[Path]:
    """按文件名排序取出类目录下的全部图片（保证划分可复现）。"""
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    return sorted(p for p in class_dir.iterdir() if p.suffix.lower() in exts)


def _validate(dataset_dir: Path, label: str) -> None:
    missing = [d for d in CLASS_TABLE if not (dataset_dir / d).is_dir()]
    if missing:
        raise SystemExit(
            f"[错误] {label} 缺少类别目录: {missing}\n"
            f"期望位置: {dataset_dir}（解压结构见 data/raw/README.md）"
        )


def _split_copy(src: Path, split_root: Path, cls_name: str,
                val_ratio: float = 0.1, seed: int = 42) -> None:
    """类内 9:1 分层：val = round(n*0.1)（至少 1 张），其余 train。
    图片落到 split_root/{train,val}/{cls_name}/（ultralytics 分类规范）。仅复制不移动。"""
    imgs = _images(src)
    rng = random.Random(seed)
    rng.shuffle(imgs)
    n_val = max(1, round(len(imgs) * val_ratio))
    splits = {"train": imgs[n_val:], "val": imgs[:n_val]}
    for split, files in splits.items():
        dst = split_root / split / cls_name
        dst.mkdir(parents=True, exist_ok=True)
        for img in files:
            shutil.copy2(img, dst / img.name)


def main() -> None:
    d1 = RAW_DIR / "Dataset_1_Cleaned"
    d2 = RAW_DIR / "Dataset_2_Cleaned"
    _validate(d1, "Dataset1"); _validate(d2, "Dataset2")

    # 类别 id = 文件夹名排序
    folder_names = sorted(CLASS_TABLE)

    # 清空重建输出目录
    if CLS_DIR.exists():
        shutil.rmtree(CLS_DIR)
    class_map: dict[str, dict] = {}
    counts: dict[str, dict[str, int]] = {}

    # 顶层 split 目录（ultralytics 分类规范：<root>/{train,val}/<类别>/ 图片）
    for split in ("train", "val", "test"):
        (CLS_DIR / split).mkdir(parents=True)

    for cls_id, folder in enumerate(folder_names):
        name_cn, aliases = CLASS_TABLE[folder]

        # Dataset1 → train/val（9:1）
        _split_copy(d1 / folder, CLS_DIR, f"{cls_id:02d}")
        counts[cls_id] = {
            "train": len(list((CLS_DIR / "train" / f"{cls_id:02d}").glob("*.*"))),
            "val":   len(list((CLS_DIR / "val" / f"{cls_id:02d}").glob("*.*"))),
        }
        # Dataset2 → test（整体，跨域）
        test_dir = CLS_DIR / "test" / f"{cls_id:02d}"
        test_dir.mkdir(parents=True)
        for img in _images(d2 / folder):
            shutil.copy2(img, test_dir / img.name)
        counts[cls_id]["test"] = len(list(test_dir.glob("*.*")))

        class_map[str(cls_id)] = {"name_cn": name_cn, "aliases": aliases}

    # class_map.json（models/ 目录可能尚未创建）
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / "class_map.json").write_text(
        json.dumps(class_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 汇总打印
    cn_names = [CLASS_TABLE[f][0] for f in folder_names]
    print(f"类别映射已写入: {MODELS_DIR / 'class_map.json'}")
    print(f"{'id':>3} {'train':>6} {'val':>4} {'test':>4}  中文名")
    for cls_id in range(len(folder_names)):
        c = counts[cls_id]
        print(f"{cls_id:>3} {c['train']:>6} {c['val']:>4} {c['test']:>4}  {cn_names[cls_id]}")
    t = sum(c["train"] for c in counts.values()); v = sum(c["val"] for c in counts.values())
    te = sum(c["test"] for c in counts.values())
    print(f"合计: train={t} val={v} test={te}（期望 3384/约377/400）")


if __name__ == "__main__":
    main()
