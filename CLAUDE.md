# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**多模态中草药识别智能体**（课程/毕设项目）：融合视觉与文本多模态 AI —— 图像分类（自训练 YOLOv8n-cls，20 类中药饮片）+ 知识图谱（NetworkX + JSON）+ 多模态交互（手写 Function Calling / ReAct 智能体，DeepSeek API）。

- **核心亮点（答辩卖点）**：真正的**智能体**——工具调用链（识别→查药性→查禁忌→找方剂）及调用次数由问题决定，不由代码决定；不是"识别后 LLM 写段文案"的固定流水线
- **多模态落点**：图像模态走 CNN，文本模态走用户提问，两者在**工具调用层融合**进入同一条推理链
- **合规边界**：不输出诊断/辨证/剂量/疗效结论；禁忌提示定位为"知识展示"而非"用药指导"
- 需求文档：`docs/需求分析.md`（v0.9 初稿，医学数据待人工核对，清单见该文档 8.4）

## 环境信息（★ 有雷区，先读再动手）

- Conda 环境 **`task`**（`D:\CondaEnv\task`，Python 3.10.19）＝ PyCharm 项目 SDK；解释器 `D:\CondaEnv\task\python.exe`
- torch **2.12.0.dev+cu128**（`cuda.is_available()==True`）、torchvision 0.27.0.dev+cu128 —— GPU：RTX 5060 Laptop 8GB，Blackwell **sm_120**（必须 cu128+）
- numpy **1.23.5（锁定值）**、scipy 1.10.1、pandas 2.0.3、networkx 3.4.2、matplotlib 3.7.2、scikit-learn 1.7.2、psutil 7.2.2、pillow 12.3
- ⚠️ **裸 `python` 指向 base Anaconda（其 pandas/scipy 已损坏）**——一律 `conda run -n task python ...` 或 PyCharm 打开，不要裸用
- ⚠️ **opencv 已修复（2026-08-27）**：原 `cv2` 来自 conda 包 `opencv`（4.12.0），与 numpy 1.23.5 ABI 不匹配（import 报 DLL load failed）；修复 = `conda remove -n task opencv` + `pip install opencv-python==4.9.0.80`。opencv-python ≥4.10 要求 numpy≥2，**禁止升级**；装包用 `--ignore-installed` 覆盖残留 dist-info

### 环境红线（违反=显卡直接不可用，训练全挂）

**禁止** 裸 `pip install ultralytics` / `pip install timm` / 裸 `pip install torch torchvision` / `conda update --all` —— 任何一项都可能把 cu128 nightly 换成稳定版，导致 Blackwell sm_120 变 CPU-only。正确姿势：

1. 永远 `--no-deps` 安装（ultralytics 的纯 Python 依赖缺失时逐个补装）
2. 装任何包前先 `pip install --dry-run <包名>` 确认输出里**没有** torch/torchvision/numpy 的变更

## 项目结构

```
├── docs/需求分析.md        # 产品调研 + 需求规格（v0.9，第一版）
├── config.py              # 全局配置：路径、阈值、模型名
├── requirements.txt
├── .env.example / .env    # DEEPSEEK_API_KEY（绝不硬编码）
├── prepare_dataset.py     # 原始数据 → train/val（Dataset1 9:1）+ 跨域 test（Dataset2）
├── train.py               # ultralytics yolo8n-cls 训练（镜像 pet_recognition 写法）
├── evaluate.py            # 跨域测试：Dataset2 per-class F1 + 混淆矩阵
├── models/best.pt  class_names.json  class_cn.json
├── classifier/predictor.py  # YOLO 加载 → predict_topk() → [(中文名, 置信度)]
├── kg/
│   ├── data/kg.json       # 单文件：nodes[] + edges[]
│   ├── builder.py         # JSON → networkx.MultiDiGraph
│   ├── query.py           # 图查询（智能体工具的底层）
│   └── viz.py             # matplotlib 静态图 → PNG（离线可用，依赖本地中文字体）
├── agent/
│   ├── tools.py           # 6 个工具函数 + JSON Schema
│   ├── prompts.py         # System Prompt（中文，含溯源/拒答/合规规则）
│   └── core.py            # ReAct 循环 ★核心
├── demo_agent.py          # 命令行智能体（先于 Flask 跑通调试）
├── app.py                 # Flask
├── templates/  static/  uploads/
└── data/raw/  data/herbs_cls/
```

## 数据

- **NB-TCM-CHM**（宁波大学，CC BY 4.0，DOI 10.17632/2kjmzjyrmd）：20 类**果实种子类饮片**，共 3784 张
  - Dataset1（3384 张网络爬取图）→ 按 9:1 切训练集 / 同域验证集
  - Dataset2（400 张药房实拍手机图）→ 整体作**跨域测试集**（与数据集原论文协议一致）
- 20 类中文名/性味/归经全表见 `docs/需求分析.md` 第 7.2 节；三个易混组：砂仁/豆蔻/草豆蔻、苦杏仁/桃仁、山楂/金樱子
- **医学数据红线**：全部药性/方剂/禁忌条目必须以《中国药典》2020 年版为准并带 `source` 字段；**LLM 起草 ≠ 定稿**，人工逐条核对（核对清单：docs 8.4）
- **禁忌边双源字段**：`verse`（十八反/十九畏歌诀）+ `pharmacopoeia`（药典是否认定）；⚠️「甘草+海藻」「人参+五灵脂」「硫黄+朴硝」三对为**歌诀有、药典未认定**——前端必须分别展示两种依据
- 目录名一致性：Kaggle/Mendeley 各镜像文件夹命名有拼写错误（如 `Trichosanthis_Pericarpoium`），`class_map.json` 每类带 `aliases` 数组兜底

## 关键设计决策

1. **分类**：**yolov8n-cls** 预训练权重（⚠️ 名字带 `v`，`yolo8n-cls.pt` 是错的会 FileNotFound）——已下载至项目根目录，无需再下载；与 pet_recognition 的 yolo11n-cls 同栈，train.py/app.py 写法可直接照抄
2. **智能体**：手写 ReAct 循环（不用 LangChain），`MAX_TURNS=8`；⚠️`function.arguments` 是 JSON **字符串**必须 `json.loads()`；⚠️assistant 消息 `content` 为 None 也要原样回填 messages；⚠️分发前 `REGISTRY` 白名单校验工具名；⚠️工具异常以 `role:"tool"` 回填让模型自纠，**不让请求 500**
3. **会话记忆**：服务端 dict + localStorage `client_id`（Flask cookie session 约 4KB 上限，装不下工具返回）
4. **上传文件不删除**（智能体需跨轮次重复识别），新图上传时清理旧图 + 启动时清理 24h 前文件（与 pet 项目的 finally-unlink 刻意不同）
5. **知识图谱可视化**：matplotlib 静态 PNG（pyvis 依赖 vis.js CDN，答辩教室断网即白屏）；Windows 必须设 `plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]` + `axes.unicode_minus=False`，否则中文全是方框
6. **SSE 流式**：`fetch` + `ReadableStream`（POST 语义，EventSource 用不了）；让评委实时看到工具逐个触发
7. **合规**：不输出诊断/辨证/具体剂量/疗效；禁忌=知识展示；页面固定免责声明面板
8. **DeepSeek**：密钥只从 `DEEPSEEK_API_KEY` 读（支持 .env），绝不硬编码；429 单次退避重试、402 不重试、断网 fail-soft（识别+图谱可用，仅对话降级）
9. **低置信度**：Top-1 < 0.6 拒绝下结论，给补拍建议（干燥饮片特写、光照均匀、纯色背景），不硬答
10. Windows DataLoader 需 `if __name__ == "__main__": main()` 包主逻辑

## 常用命令

```bash
# 一切 Python 命令都用 task 环境
conda run -n task python train.py            # 训练（yolo8n-cls → best.pt）
conda run -n task python evaluate.py         # 跨域测试（Dataset2）+ per-class F1
conda run -n task python -m kg.query --herb 枸杞子 --compat 瓜蒌皮,川乌 --formulas 山楂
conda run -n task python demo_agent.py --image <图片> --q "这个能和菊花一起泡水吗？我最近眼睛干"
conda run -n task python app.py              # http://localhost:5000
```

## 验证方法（验收=答辩演示 7 步，见 docs/需求分析.md 第 10 章）

1. 传药房实拍枸杞子 → Top-1 枸杞子 + 置信度 + 图谱出处
2. 问"能和菊花一起泡水吗？眼睛干" → 5~6 次工具调用链 + 带溯源回答 + 杞菊地黄丸
3. 追问"那它用量多少" → 指代消解，不重传图片；用量以知识条目展示
4. 问"瓜蒌皮能和川乌一起用吗" → 主动提示十八反（歌诀+药典双源）
5. 传宠物照片 → 拒绝下结论 + 补拍建议
6. **拔网线** → 识别与图谱页仍可用，对话区友好降级
7. 图谱可视化页离线渲染

## 当前状态

- [x] docs/需求分析.md v0.9（待人工核对医学数据）
- [ ] M0：修 opencv → 装依赖 → 手动下载数据集 → config.py/.env
- [ ] M1~M6（见实施计划阶段二）
