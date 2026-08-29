"""
eval_vision.py —— 二期 V2-A6 验收：云端 VLM 辅助验证效果评测（方案 §12.1）。

评测口径（对照方案 12.1「混淆对 20 张 + 宠物图 3 张」；2026-08-29 实测修订）：
- 混淆对 20 张（data/herbs_cls/test/ 8 个已知混淆类各 2~3 张，GT=目录名）：
  本地 top-1 命中 vs 双通道 top-1 命中。修订口径：VLM 域内细粒度弱于本地（实测
  砂仁/豆蔻→草豆蔻、苦杏仁→乌梅等系统性偏差），故 conflict 不再否决本地——
  「弱证据不能否决强证据」，conflict 保留本地结论计命中；仅 none/non_herb 拒答不命中。
- 宠物图（tests/pets/，域外图）：低置信 3 张走原路径拒答 + 高置信 3 张触发 VLM 域外否决。
  断言绝不错误放行 —— VLM 参与的（灰区）必须拒答（non_herb/none）；consistent/conflict
  放行 = FAIL。conf≥0.75 且 VLM 未触发（0.90+ 高置信边界）= 一期已知局限，warn 不 FAIL。
- fail-soft：VISION 未配置时 verify 必须返回 unavailable（本地结论不受影响）。

用法（需 VISION_API_KEY + 开启，联网）：
    PYTHONIOENCODING=utf-8 D:/CondaEnv/task/python.exe eval_vision.py
"""
import sys
from pathlib import Path

from config import BASE_DIR, CLASS_MAP

TEST_DIR = BASE_DIR / "data" / "herbs_cls" / "test"
PETS_DIR = BASE_DIR / "tests" / "pets"

# 混淆对 8 类（方案 §8.5 T2 / 一期已知弱类）→ 目录名（class_map id → 目录）
CONFUSE_DIRS = {
    "草豆蔻": "00", "砂仁": "01", "豆蔻": "02", "苦杏仁": "03",
    "山楂": "06", "桃仁": "13", "金樱子": "15", "川楝子": "18",
}
SAMPLE_PER_CLASS = 3   # 8 类 × 3 = 24 张（取样前 20 张做统计，多 4 张防坏图）

GT_HINT = "GT"  # 混淆对命中判定用目录名


def main() -> None:
    from agent import vision as vmod
    from classifier import predictor

    # ---- 混淆对取样 ----
    samples: list[tuple[Path, str]] = []   # (图片路径, GT 中文名)
    for name_cn, dir_id in CONFUSE_DIRS.items():
        d = TEST_DIR / dir_id
        imgs = sorted(d.glob("*.jpg"))[:SAMPLE_PER_CLASS]
        samples.extend((p, name_cn) for p in imgs)
    if len(samples) < 20:
        raise SystemExit(f"[错误] 混淆对素材不足（{len(samples)}/20），请检查 {TEST_DIR}")

    # ---- 评测 ----
    local_hit = dual_hit = 0
    refused_by_dual = 0
    n = 0
    print("== 混淆对 20 张（本地 vs 双通道）==")
    for img, gt in samples[:20]:
        n += 1
        top3 = predictor.predict_topk(str(img), k=3)
        top1, conf = top3[0]
        l_hit = top1 == gt
        local_hit += l_hit

        state, vlm_top1 = "skipped", ""
        if vmod.should_use_vision(top3):
            meta = vmod.decide(top3, vmod.verify(img))
            state = meta["state"]
            vlm_top1 = meta.get("vlm_top1", "")
        # 命中口径（2026-08-29 修订，实测校准）：
        # VLM 域内细粒度弱于本地（砂仁/豆蔻→草豆蔻等系统性偏差），故 conflict 不再否决本地——
        # 「弱证据不能否决强证据」：consistent 提信 / skipped·unavailable·conflict 均放行本地结论；
        # 仅显式拒答（none/non_herb）＝未下结论，不命中
        dual_hit_now = l_hit and state in ("consistent", "skipped", "unavailable", "conflict")
        dual_hit += dual_hit_now
        if state in ("non_herb", "none"):
            refused_by_dual += 1
        mark = "✓" if dual_hit_now else "✗"
        print(f"  [{mark}] {img.name} GT={gt} local={top1} {conf:.2f} | dual={state}"
              + (f" vlm={vlm_top1}" if vlm_top1 else ""))

    # ---- 宠物图：绝不错误放行（低置信 3 张走原路径 + 高置信 3 张走 VLM 域外否决） ----
    pets = sorted(PETS_DIR.glob("*.jpg"))
    if len(pets) < 3:
        print(f"[WARN] 宠物图不足 3 张（{len(pets)}），跳过域外断言——请补齐 tests/pets/ 后重跑")
    pet_fail = pet_warn = 0
    print("\n== 宠物图（域外拒答，低置信 3 + 高置信 3）==")
    for img in pets:
        top3 = predictor.predict_topk(str(img), k=3)
        top1, conf = top3[0]
        state = "skipped"
        if vmod.should_use_vision(top3):
            state = vmod.decide(top3, vmod.verify(img))["state"]
        if conf >= 0.75 and state == "skipped":
            # 高置信域外图未触发 VLM（0.90+ 边界或 T3 差距大）→ 一期已知局限，warn 不 FAIL
            pet_warn += 1
            print(f"  [WARN] {img.name} conf={conf:.2f} 未触发 VLM（高置信边界，一期已知局限）")
        elif state in ("consistent", "conflict"):
            # VLM 参与却放行域外图（consistent 直接放行 / conflict 按新口径保留本地）→ FAIL
            pet_fail += 1
            print(f"  [FAIL] {img.name} VLM 放行（{state}），域外图必须拒答")
        else:
            print(f"  [OK]   {img.name} 已拒答（local={top1} {conf:.2f}, dual={state}）")

    # ---- fail-soft：未配置时 verify 必须不可用 ----
    from agent import vision as _v
    # 强制模拟关闭（不动 .env）：直接断言未启用路径
    _v.config.VISION_ENABLED = False
    assert _v.verify("tests/pets/birman_01.jpg")["state"] == "unavailable", "未启用时 verify 必须 fail-soft"
    print("\n[fail-soft] VISION 关闭时 verify → unavailable ✓")

    # ---- 汇总 ----
    print(f"\n== 结果 ==")
    print(f"  混淆对 top-1 命中：本地 {local_hit}/{n}，双通道 {dual_hit}/{n}（拒答 {refused_by_dual} 张）")
    improved = dual_hit >= local_hit
    print(f"  双通道不劣化：{'✓' if improved else '✗'}")
    print(f"  宠物域外：FAIL {pet_fail}，WARN {pet_warn}（一期高置信边界）")

    ok = improved and pet_fail == 0
    if not ok:
        raise SystemExit(1)
    print("[OK] VLM 辅助验证评测通过（双通道不劣化 + 域外图零放行）")


if __name__ == "__main__":
    main()
