# kg/data/records —— 二期 P0 药材档案记录（schema v2 数据源）

每味一个 JSON 文件（文件名 = 节点 id，中文），是 `kg.json` 中 herb 节点的**唯一事实来源**。
`kg/v2_merge.py` 把 records + whitelist + 原 kg.json（formula/minor/edges 透传）合并生成 `kg/data/kg.json`（schema_version=2）与 `kg/data/review_ledger.json`（版本台账，派生产物，勿手改）。

## 字段规范（黄金模板：`枸杞子.json`，人工核定）

```json
{
  "id": "枸杞子",            // 必须与文件名一致
  "category": "herb",        // records 只承载 herb
  "name_cn": "枸杞子",
  "latin": "Lycii Fructus",
  "aliases": ["枸杞"],       // upgraded 药必须 ⊇ v1 已声明别名（只增不删，防别名断链）
  "source": "《中国药典》2020 年版一部",   // 展示串，不得含「（待人工核对）」
  "meta": {
    "version": 2,            // existing 升级=2（L2 已核 + 新增 L1）；upgraded/new 首稿=1
    "updated": "2026-08-28",
    "review_status": "draft", // draft | reviewed（节点整体口径：以最未定稿部分为准）
    "reviewed_by": "",
    "review_date": "",
    "provenance_level": "A"   // 主口径档位（§6.1）；产地等 B 档在 L1.来源标注 行内标注
  },
  "profile": {
    "source_edition": "《中国药典》2020 年版一部",  // L2 口径（2026-08-28 用户拍板维持 2020 版）
    "性味": "...", "归经": ["..."], "功效": "...", "主治": "...",
    "用量": "...", "毒性": "...", "禁忌": "...",      // 7 个扁平键名/型与 v1 完全一致（query.py 零破坏前提）
    "L1": {
      "性状": "...", "炮制": "...", "产地": "...", "鉴别要点": "...",
      "similar_herbs": [
        {"herb": "五味子", "reason": "外观相近，干燥浆果类易混淆",
         "points": ["本品（枸杞子）：...", "对比药（五味子）：..."]}
      ],
      "来源标注": {"性状": "《中国药典》2020 年版一部·枸杞子【性状】（A 档）", "产地": "《中药学》教材（B 档）", "炮制": "...", "鉴别要点": "...", "similar_herbs": "..."}
    }
  }
}
```

**L1 规则**：性状/炮制/产地/鉴别要点四字段非空（豁免见 whitelist.l1_exemptions，人工批准后登记）；`来源标注` 为每个非空 L1 字段提供「书名·条目（档位）」；similar_herbs.reason 只写外观/来源/形态差异，**禁止「功效相近」**；required_similar_pairs（9 对）必须双侧互录。

## 批次状态表

| 味 id | 批次 | tier | 起草日 | 核对人 | 状态 |
|---|---|---|---|---|---|
| 枸杞子 | 黄金模板 | existing | 2026-08-28 | — | draft |
| 五味子 金樱子 山茱萸 乌梅 覆盆子 | B1-1 | existing | 2026-08-28 | | draft |
| 桃仁 苦杏仁 川楝子 砂仁 豆蔻 草豆蔻 | B1-2 | existing | 2026-08-28 | | draft |
| 山楂 木瓜 小茴香 地肤子 栀子 连翘 | B1-3 | existing | 2026-08-28 | | draft |
| 菊花 甘草 补骨脂 川乌 草乌 瓜蒌皮 | B1-4 | existing | 2026-08-28 | | draft |
| 熟地黄 山药 茯苓 泽泻 牡丹皮 陈皮 | B2-1 | upgraded | 2026-08-28 | | draft |
| 金银花 薄荷 荆芥穗 桔梗 牛蒡子 淡豆豉 | B2-2 | upgraded | 2026-08-28 | | draft |
| 麦冬 人参 桂枝 附子 细辛 黄连 | B2-3 | upgraded | 2026-08-28 | | draft |
| 当归 半夏 白术 杜仲 核桃仁 大枣 生姜 莱菔子 | B2-4 | upgraded | 2026-08-28 | | draft |
| 黄芪 决明子 胖大海 罗汉果 百合 | B3-1 | new | 2026-08-28 | | draft |
| 薏苡仁 赤小豆 莲子 芡实 龙眼肉 | B3-2 | new | 2026-08-28 | | draft |
| 酸枣仁 荷叶 玫瑰花 桑椹 | B3-3 | new | 2026-08-28 | | draft |

（2026-08-28 全部起草完成：64/64，validate --records --strict 全绿；merged 生成 kg.json v2 + review_ledger 64 行，validate --strict 全绿。）

## 人工核对清单（Step 7，用户主责，红线「LLM 起草 ≠ 定稿」）

**每味核对范围**（对照《中国药典》2020 年版一部原文）：

| 字段 | 核什么 | 注意 |
|---|---|---|
| 性味/归经/功效/主治/用量/毒性/禁忌 | L2 七字段**逐字**对照药典【性味与归经】【功能与主治】【用法与用量】【注意】 | existing 24 味已由 merge 强制与 v1 逐字 diff（防抄写漂移）；upgraded/new 26+14 味为本次新起草，必须人工对照 |
| 性状/炮制 | 药典【性状】【炮制】条目（L1 起草时做了归纳压缩，核对是否有误引/漏引） | 炮制原文过长处可接受归纳，但不可改剂量/工序关键数字 |
| 产地 | 道地产区 + 基原（拉丁学名），核对教材口径 | 基原拉丁名必须与药典【来源】一致 |
| 鉴别要点 | 外观鉴别特征是否准确、对比药描述是否属实 | 与相似对对方 record 的 points 交叉核对（对称性） |
| similar_herbs | reason 只能外观/来源/形态差异（禁止功效）；points 本品/对比药对称 | 9 对 required 必须双侧互录（validate 自动查） |
| 来源标注 | 每条标注档位是否与实际来源一致（A=药典原文、B=教材） | 产地/鉴别要点含教材口径的应为 B 档 |
| aliases | upgraded 26 味 ⊇ v1 已声明别名（只增不删） | validate 自动查；新增别名需确认无撞名 |

**高危 8 味双人复核**（用量/毒性/禁忌 三项必须本人 + 导师/评审双签，登记进 `review_ledger.json` 的 `review_items`）：
附子（先煎久煎/孕妇禁用）、细辛（1～3g 剂量/马兜铃酸限量）、川乌、草乌（大毒/炮制/先煎久煎）、半夏（有毒/十八反）、苦杏仁、桃仁（毒性微量氰苷口径）、人参（十八反藜芦/十九畏五灵脂双源口径）

**核对工作流**：
1. 逐味 `git diff kg/data/records/<味>.json`（或对照 README 清单）只读核对
2. 通过 → 改该文件 `meta.review_status="reviewed"` + `reviewed_by` + `review_date`
3. `PYTHONIOENCODING=utf-8 D:/CondaEnv/task/python.exe -m kg.v2_merge --strict`（ledger 自动追加历史行）
4. 高危 8 味在 review_ledger.review_items 登记双人复核记录
5. 全部 reviewed 后 `python -m kg.validate --strict` 全绿 → 冻结版本（git commit）

**验收口径（方案 §12.1）**：档案药 64/64、L2 448/448、L1 256/256、source 带全率 64/64、「待人工核对」残留 0、ledger 一致性——validate 输出即为验收统计。
