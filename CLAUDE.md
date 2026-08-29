# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**多模态中草药识别智能体**（课程/毕设项目）：融合视觉与文本多模态 AI —— 图像分类（自训练 YOLOv8n-cls，20 类中药饮片）+ 知识图谱（NetworkX + JSON）+ 多模态交互（手写 Function Calling / ReAct 智能体，DeepSeek API）。

- **核心亮点（答辩卖点）**：真正的**智能体**——工具调用链（识别→查药性→查禁忌→找方剂）及调用次数由问题决定，不由代码决定；不是"识别后 LLM 写段文案"的固定流水线
- **多模态落点**：图像模态走 CNN，文本模态走用户提问，两者在**工具调用层融合**进入同一条推理链
- **合规边界**：不输出诊断/辨证/剂量/疗效结论；禁忌提示定位为"知识展示"而非"用药指导"
- 需求文档：`docs/需求分析.md`（v0.9 初稿，医学数据待人工核对，清单见该文档 8.4）
- **答辩要点持续维护**：`docs/答辩要点.md` —— 本项目中每产生一个可讲/可引用的结论、实测数据、设计决策、评审问答，**都追加到该文档**（用户要求，活文档，勿脱更）

## 环境信息（★ 有雷区，先读再动手）

- Conda 环境 **`task`**（`D:\CondaEnv\task`，Python 3.10.19）＝ PyCharm 项目 SDK；解释器 `D:\CondaEnv\task\python.exe`
- torch **2.12.0.dev+cu128**（`cuda.is_available()==True`）、torchvision 0.27.0.dev+cu128 —— GPU：RTX 5060 Laptop 8GB，Blackwell **sm_120**（必须 cu128+）
- numpy **1.23.5（锁定值）**、scipy 1.10.1、pandas 2.0.3、networkx 3.4.2、matplotlib 3.7.2、scikit-learn 1.7.2、psutil 7.2.2、pillow 12.3
- ⚠️ **裸 `python` 指向 base Anaconda（其 pandas/scipy 已损坏）**——一律 `conda run -n task python ...` 或 PyCharm 打开，不要裸用
- ⚠️ **opencv 已修复（2026-08-27）**：原 `cv2` 来自 conda 包 `opencv`（4.12.0），与 numpy 1.23.5 ABI 不匹配（import 报 DLL load failed）；修复 = `conda remove -n task opencv` + `pip install opencv-python==4.9.0.80`。opencv-python ≥4.10 要求 numpy≥2，**禁止升级**；装包用 `--ignore-installed` 覆盖残留 dist-info
- ⚠️ **polars 1.44.1（2026-08-27 补装）**：ultralytics 8.4.130 的 `read_results_csv`（保存检查点）**硬依赖** polars，缺它训练第一个 epoch 就挂；二进制与主包拆分，须 `pip install --no-deps polars polars-runtime-32`（后者包名不是 polars-polars）
- ⚠️ **训练启动会下载 yolo26n.pt 做 AMP 检查**：GitHub 直连极慢（30KB/s 级），表现为"启动后 GPU 0%、进程卡死"；已缓存到项目根 `weights/yolo26n.pt`（已 gitignore），且训练脚本设 `YOLO_OFFLINE=true` 会跳过。勿删 weights/，删了下次训练又要卡十几分钟
- ⚠️ **Windows DLL 加载顺序**：`import torch` 必须先于 matplotlib/scipy（否则 c10.dll WinError 1114）；evaluate.py 的 ultralytics import 必须放文件最顶部
- ⚠️ **PyInstaller 打包雷（2026-08-28 修复）**：PyInstaller 会把 conda 环境的旧版 VC++ 运行时（vcruntime140/msvcp140 14.27、ucrtbase 10.0.22621）收进 `_internal`，其 DLL 搜索顺序抢在 System32 之前 → 打包后 exe `import torch` 报 **WinError 1114**（c10.dll 初始化失败）。`herbal-backend.spec` 已有 `RUNTIME_EXCLUDE` 过滤（依赖系统 14.51+/26100+ 运行时）；**改 spec 时不要移除该过滤**，也不要手工往 `_internal` 里补 vcruntime/msvcp DLL
- ⚠️ **conda run 缓冲输出**：后台跑长任务时 `conda run -n task python` 的 stdout 会缓冲到进程结束才落盘，看不到中途进度；长任务用 `D:\CondaEnv\task\python.exe -u` 直调 + `PYTHONUNBUFFERED=1`

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
├── train.py               # ultralytics yolov8n-cls 训练（镜像 pet_recognition 写法）
├── evaluate.py            # 跨域测试：Dataset2 per-class F1 + 混淆矩阵
├── models/best.pt  class_map.json   # class_map: id → {name_cn, aliases}
├── classifier/predictor.py  # YOLO 加载 → predict_topk() → [(中文名, 置信度)]
├── kg/
│   ├── data/kg.json       # 单文件：nodes[] + edges[]（schema v2：herb 64 味带档案）
│   ├── data/whitelist.json     # 唯一人工权威清单（64 味 tier + 9 对 required 相似对）
│   ├── data/records/*.json     # 每味一个完整节点（唯一事实来源，review_status: draft|reviewed）
│   ├── data/review_ledger.json # 版本化核对台账（merge 派生产物，勿手写）
│   ├── builder.py         # JSON → networkx.MultiDiGraph
│   ├── query.py           # 图查询（智能体工具的底层；get_profile 含 L1 鉴别参考段；retrieve_doc 混合检索）
│   ├── v2_merge.py        # records + whitelist + v1 → kg.json v2 + ledger（幂等/--strict）
│   ├── validate.py        # 起草期 --records / 全量 --strict 校验（验收口径 12.1 统计）
│   ├── build_docs.py      # records → data/tcm_docs/<version>/ 语料（674 条，带 source+sha256）
│   ├── retrieval.py       # 自研 numpy 向量检索（bge-small-zh 本地 embedding，幂等入库/fail-soft）
│   └── viz.py             # matplotlib 静态图 → PNG（离线可用，依赖本地中文字体）
├── agent/
│   ├── tools.py           # 6 个工具函数 + JSON Schema
│   ├── prompts.py         # System Prompt（中文，含溯源/拒答/合规规则）
│   └── core.py            # ReAct 循环 ★核心
├── demo_agent.py          # 命令行智能体（先于 Flask 跑通调试）
├── eval_rag.py            # P1 混合检索评测（20 问 top-5 命中 ≥ 0.9）
├── app.py                 # Flask（动态端口握手 + Vue 产物托管 + 旧模板兜底）
├── frontend/              # ★ Vue3 + Electron 桌面版（electron-vite 工程）
│   ├── src/main/          # 主进程：spawn 后端 → stdout HERB_READY_PORT 握手 → 窗口
│   ├── src/renderer/      # Vue3 SPA（hash 路由 #/ #/graph，d3 npm 依赖，0 外部请求）
│   └── electron-builder.yml  # NSIS 安装包（extraResources 挂后端 onedir）
├── herbal-backend.spec    # PyInstaller onedir（datas：models/kg.json/frontend/out/renderer）
├── scripts/               # build-backend.bat（仅后端）/ build-all.bat（一键出包，staging 英文路径）
├── templates/  static/  uploads/
└── data/raw/  data/herbs_cls/  data/tcm_docs/（P1 检索语料，版本目录+MANIFEST）
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
5. **知识图谱可视化（v2）**：`/graph` 页 = **D3 手写力导向交互图**（vendor 本地 `static/vendor/d3.v7.min.js`，**页面 0 外部请求**）：拖拽 / 缩放平移 / 悬停邻接高亮 / 点击节点聚焦 + 档案面板联动 / 图例筛选 / 复位；数据源 `GET /api/graph`（`kg.query.graph_dataset()`：84 节点 + 84 边，禁忌双向边去重为 9 条，`ensure_ascii=False` 防中文转义）；**失败 / 断网自动降级为 matplotlib 静态 PNG**（`static/kg_graph.png`，离线渲染语义保住）；Windows matplotlib 须设 `plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]` + `axes.unicode_minus=False`，否则中文全是方框
6. **SSE 流式**：`fetch` + `ReadableStream`（POST 语义，EventSource 用不了）；让评委实时看到工具逐个触发
7. **合规**：不输出诊断/辨证/具体剂量/疗效；禁忌=知识展示；页面固定免责声明面板
8. **DeepSeek**：密钥只从 `DEEPSEEK_API_KEY` 读（支持 .env），绝不硬编码；429 单次退避重试、402 不重试、断网 fail-soft（识别+图谱可用，仅对话降级）
9. **低置信度**：Top-1 < LOW_CONF_THRESHOLD（现 0.75，调优记录见 config.py 注释）拒绝下结论，给补拍建议（干燥饮片特写、光照均匀、纯色背景），不硬答
10. Windows DataLoader 需 `if __name__ == "__main__": main()` 包主逻辑

## 常用命令

```bash
# 一切 Python 命令都用 task 环境
conda run -n task python train.py            # 训练（yolo8n-cls → best.pt）
conda run -n task python evaluate.py         # 跨域测试（Dataset2）+ per-class F1
conda run -n task python -m kg.query --herb 枸杞子 --compat 瓜蒌皮,川乌 --formulas 山楂
conda run -n task python demo_agent.py --image <图片> --q "这个能和菊花一起泡水吗？我最近眼睛干"
conda run -n task python app.py              # http://localhost:5000（旧 Web 版）

# 桌面版（vue-electron-exe 分支）
cd frontend && npm run dev                   # 开发：自动拉起 task python 后端 + Electron 窗口
scripts\build-backend.bat                    # 仅打包后端 exe（PyInstaller）
scripts\build-all.bat                        # 一键出包 → C:\herbal_stage\frontend\release\*.exe
```

**桌面版运行机制**：Electron 主进程 spawn 后端（dev=Conda python app.py / prod=resources 里 PyInstaller exe），stdout 打印 `HERB_READY_PORT=<port>` 握手，窗口 loadURL `http://127.0.0.1:<port>/`（Flask 托管 Vue 产物，同源零 CORS、SSE 原样可用）。退出时 `taskkill /T` 整树杀。**打包红线**：PyInstaller 必须 `D:\CondaEnv\task\python.exe -m`；出包动作走 `C:\herbal_stage` 英文路径（项目路径含中文，PyInstaller/NSIS 有缺陷）；`herbal-backend.spec` 里 excludes 已排 train/evaluate/sklearn/matplotlib/polars（防 DLL 顺序雷 + 省体积）。

## 验证方法（验收=答辩演示 7 步，见 docs/需求分析.md 第 10 章）

1. 传药房实拍枸杞子 → Top-1 枸杞子 + 置信度 + 图谱出处
2. 问"能和菊花一起泡水吗？眼睛干" → 5~6 次工具调用链 + 带溯源回答 + 杞菊地黄丸
3. 追问"那它用量多少" → 指代消解，不重传图片；用量以知识条目展示
4. 问"瓜蒌皮能和川乌一起用吗" → 主动提示十八反（歌诀+药典双源）
5. 传宠物照片 → 拒绝下结论 + 补拍建议
6. **拔网线** → 识别与图谱页仍可用，对话区友好降级
7. 图谱可视化页：本地服务在 → 交互式力导向图；断网 → 自动降级静态 PNG + 提示条（离线渲染语义不变）

## 当前状态

- [x] docs/需求分析.md v0.9（待人工核对医学数据）
- [x] M0 环境侧：opencv 修复、ultralytics 8.4.130 + flask + dotenv 安装（全部 `--no-deps`）、torch cu128 验证全绿；`yolov8n-cls.pt` 已下载到项目根目录；config.py / requirements.txt / .env.example / .gitignore / README.md / data/raw/README.md 已写完（.env 由用户复制）
- [ ] M0 用户侧（阻塞训练/识别）：① 浏览器手动下载 NB-TCM-CHM → `data/raw/`（步骤见 data/raw/README.md）② `copy .env.example .env` 并填入自己的 DEEPSEEK_API_KEY（建议直接从 pet_recognition 复制 .env）
- [x] 知识图谱层（2026-08-27，不依赖数据）：`kg/data/kg.json`（22 味带档案药材 = 20 主药 + 菊花/甘草扩展档案、10 禁忌网络节点、45 方剂组成节点、12 首经典方剂、9 条禁忌边）；`kg/builder.py`（JSON→MultiDiGraph + 别名索引）、`kg/query.py`（档案/禁忌双源/方剂反查/症状找方/反向检索/相似药，CLI 已验证）、`kg/viz.py`（matplotlib 静态 PNG → `static/kg_graph.png`，离线可渲染，已验证）
- [x] 智能体层（2026-08-27）：`agent/tools.py`（6 工具 + JSON Schema + REGISTRY 白名单 + 入参校验）、`agent/prompts.py`（溯源/合规 System Prompt，支持注入当前识别药材）、`agent/core.py`（手写 ReAct：MAX_TURNS=8、arguments json.loads、content=None 原样回填、429 单次退避/402 不重试/断网 fail-soft）；`demo_agent.py` CLI（无密钥时友好提示退出，已验证）
- [x] Web 层（2026-08-27）：`app.py`（/ /graph /upload /chat(SSE) /api/herb /api/health + 413/404/500 兜底 + 启动清理 24h 上传 + 会话 dict + 低置信度拒答 + 模型未就绪 fail-soft）；`templates/index.html`（上传区+对话区+时间轴+固定免责面板）、`templates/graph.html`（离线图谱 + 档案检索）、`static/css/style.css`、`static/js/main.js`（fetch+ReadableStream 解析 SSE、localStorage client_id、拖拽上传、时间轴折叠）。已验证：页面 200、上传校验 400/413、无模型 fail-soft 200、无密钥 SSE error、图谱/档案 API
- [x] 真实调用链实测（2026-08-27，DeepSeek 密钥已在系统环境变量）：用例 4「瓜蒌皮×川乌」→ 3 次工具调用 + 十八反双源 ✅；用例 2「枸杞子+菊花泡水/眼睛干」（注入 current_herb 模拟上传后）→ 4 次工具调用 + 杞菊地黄丸 ✅；工具 schema 需 `{"type":"function"}` 包裹（已修）；方剂症状匹配加 `keywords` 同义词层（「眼睛干」→ 杞菊地黄丸，已修）；川乌/草乌补最小档案（毒性溯源）
- [x] 模型层（2026-08-27，M1~M2 完成）：`prepare_dataset.py`（Dataset1 按类 9:1 → `data/herbs_cls/{train,val}/00~19`，Dataset2 整体 → test，生成 `models/class_map.json`）；`train.py`（yolov8n-cls，150 epochs/patience 30/batch 64，**val top-1 93.8% / top-5 99.7%**，best.pt → `models/best.pt`）；`evaluate.py`（跨域 Dataset2 400 张：**top-1 85.75% / top-3 96.75%**，per-class F1 表 + 混淆矩阵 PNG → `runs/evaluate/` + 易混对统计）。冒烟测试：药房实拍枸杞子 → 枸杞子 0.94 ✅（演示用例 1 数据就绪）；顺手修了 `classifier/predictor.py` 首次调用 `model.predictor is None` 的 500 bug
- [ ] 已知弱点（答辩可展开）：桃仁 recall 仅 0.30（14/20 被误判，主要流向苦杏仁/川楝子）、草豆蔻 recall 0.45（8 张误判为砂仁）、砂仁/川楝子 precision 偏低（0.59/0.61）；山楂↔金樱子跨域无混淆（好于预期）。改进方向：类加权 / 更高 imgsz / 延长训练
- [x] kg.json 医学数据人工核对（2026-08-27 用户核定，review_status 已更新；核对清单留档 review_items）
- [x] Web 整体验收（2026-08-27，对照 10.2 演示脚本 7 步全过）：① 药房实拍枸杞子 → top1 0.94 + 图谱出处 ✅ ② 菊花泡水/眼睛干 → 4 次工具调用 + 杞菊地黄丸 ✅ ③ 追问用量 → 指代消解 + 知识条目 ✅ ④ 瓜蒌皮×川乌 → 十八反双源 ✅ ⑤ 宠物照片（Bombay/Birman/British_Shorthair）→ 拒答+补拍建议 ✅ ⑥ 断网模拟 → 识别+图谱可用、对话友好降级 ✅ ⑦ 图谱页离线渲染 + 档案 API ✅
- [x] 验收期修复（2026-08-27）：`app.py` **debug=True → False**（werkzeug reloader 子进程里 import torch 触发 2.12.0.dev 的 dsl_registry 循环导入，上传识别 500）；`LOW_CONF_THRESHOLD 0.6 → 0.75`（宠物等域外图高置信误判最高 0.94，见 config.py 注释）；`classifier/predictor.py` 首调 `model.predictor is None` bug
- [x] 图谱页 v2（2026-08-27）：`/graph` 升级为 **D3 手写力导向交互图**（Obsidian 双向链接风格、浅色画布）：vendor 本地 d3.v7.9.0（279KB，`static/vendor/`，页面 0 外部请求）；`GET /api/graph`（`kg.query.graph_dataset()`：节点带 degree/has_profile/aliases，禁忌双向 18 条去重为 9，`ensure_ascii=False`）；`static/js/graph.js`（IIFE，暴露 `window.GraphPage.showProfile`）：拖拽 / zoom(0.3~4) / 悬停邻接高亮 + tooltip（禁忌边双源：歌诀+药典）/ 点击聚焦(1.6×) + 档案联动（formula 节点升级显示君臣佐使）/ legend 筛选 / 复位；失败/断网降级静态 PNG + 提示条。**顺带修复数据 bug**：「五仁丸→杏仁」边与节点组成数组 `杏仁`→`苦杏仁`（builder.py 组成边过别名索引双保险；`formulas_by_herb("苦杏仁")` 不再漏报五仁丸，孤立节点 9→8）。已验证：全路由 200、/api/graph 84/84、中文不转义、禁忌 9 条双源齐全；浏览器交互与断网降级待答辩前人工复验
- [ ] 待办（答辩前可选）：清理 kg.json 档案出处里残留的「待人工核对」字样（用户已核对完）；「甘草+海藻」口径决策待用户答复（歌诀有、药典未认定的展示口径，review_items 留档）
- [x] 桌面版 vue-electron-exe 分支（2026-08-28，已推送 GitHub）：Vue3+Electron（electron-vite）全量迁移 + 后端 frozen 改造（动态端口握手/托管 Vue 产物）+ PyInstaller onedir（1114 修复：`RUNTIME_EXCLUDE` 剔除 conda 旧版 VC++ 运行时 14.27，依赖系统 14.51+）+ electron-builder NSIS 安装包 **1.93GB**（`C:\herbal_stage\release\HerbalAgent-Setup-1.0.0.exe`）。后端瘦身跨 2GB NSIS mmap 线：剔除 cudnn_adv/cusolverMg/curand/nvperf/cufftw/nvrtc.alt + scipy/pyarrow（**⚠️ cusparse 直接依赖 nvJitLink、torch_cuda 直接依赖 cufft/cusolver/cusparse，这三组永远不能删**）
- [x] 桌面版迁移 bug 修复（2026-08-28，`python app.py` + 浏览器同现）：Vue 迁移遗留三连 —— ① `style.css` 残留 5 处 `display:none`（旧 Jinja 版 JS toggle 时代的默认隐藏，Vue 改 `v-if` 后没删），图谱查询结果/识别卡/补拍建议/错误提示/断网降级图全被压住；② ChatPanel 时间轴对象未 `reactive` 创建（普通对象 push 进 ref 数组后原引用修改绕过代理 → 工具链不逐条弹出）；③ 时间轴分数组后置渲染（顺序错）+ 缺 `data-collapsed` 折叠 CSS。已修 + 重建，验证通过；排查假线索：`/favicon.ico` 404 误导 + 4 个 app.py 实例争抢 5000 端口（含裸 python base 实例），详见 人机交互.md 第 6 条
- [ ] 待办（答辩前）：**装机验证**（安装包已就绪，静默装 `/S` 装到 %LOCALAPPDATA%\Programs\HerbalAgent 后跑通识别+图谱）；GitHub 推送走 `ssh://git@ssh.github.com:443/...`（HTTPS 的 .gitconfig socks5 代理端口仍是 1080，clash 实际在 7890 且节点未连——直连 github.com 被墙不可达，SSH 443 已验证可用）

### 二期（2026-08-28，对齐立项报告 14 章，聚焦知识侧 + 智能体侧）

- [x] **二期升级实施方案文档**（2026-08-28）：`docs/二期升级实施方案.md`（对齐参考立项报告的 14 章结构；R01–R12 差距表、五道闸门、六子 Agent 编排、P0–P5 里程碑、验收口径、附录 B 评审问题；含**云端 VLM 辅助验证 V2-A6 可选增强**完整设计 §8.5——触发灰区三条件 / 双通道决策矩阵 / 断网 fail-soft / 结构白名单 / 独立 VISION 配置，默认关闭）。详细排期与验收见该文档，本节只列要点待办
- [x] **二期 P0 知识底座**（2026-08-28）：档案药 22→**64 味**（24 existing + 26 upgraded + 14 new，白名单 `kg/data/whitelist.json`）；kg.json 升 `schema_version=2`（`kg/v2_merge.py` 程序化合并 records + whitelist + v1，幂等/--strict/--check-idempotent，`builder.py`/query.py 其余 8 函数**零改动**）；records 64 味全 draft（L2 七字段 + L1 性状/炮制/产地/鉴别要点/similar_herbs/来源标注，口径=《中国药典》2020 年版一部，**2025 切换走台账重核机制**）；review_ledger 64 行版本台账；`kg/validate.py` 起草期/全量双模式（验收 12.1：L2 448/448、L1 256/256、source 64/64、残留 0）；get_profile 追加【鉴别参考】段 + 核对状态行（query.py 唯一改动）。**已知纠偏**：菊花/甘草 v1 缺毒性键已补、甘草禁忌临时注记已清理（`KNOWN_L2_DRIFT` 显式放行）；**修复 merge bug**：upgraded 26 味从 minor 升级后透传重复节点（v2_merge 跳过白名单 id）。⚠️ **人工核对待办（用户主责，红线）**：64 味 L1/L2 逐字对照药典核对 + 高危 8 味（附子/细辛/川乌/草乌/半夏/苦杏仁/桃仁/人参）双人复核，清单见 `kg/data/records/README.md`，通过后改 meta.review_status=reviewed 并重跑 merge
- [x] **二期 P1 混合检索**（2026-08-28，知识侧）：③+④ 完成——**选型偏离**：不装 chromadb（1.5.9 拖 40+ 依赖含 kubernetes/onnxruntime 且代理下载不稳），自研 numpy 向量检索 `kg/retrieval.py`（bge-small-zh-v1.5 本地 embedding，transformers 4 轻包 `--no-deps` 已装，CPU 可跑 ~1s 加载 / 674 条 16s 入库；归一化内积 + 多 query 同义词扩展并集取 max + MIN_SCORE=0.45 拒答 + 幂等指纹入库 + fail-soft）；语料 `data/tcm_docs/2026-08-28_v1/`（`kg/build_docs.py` 从 records 派生 64 味 674 条，每条带 source/source_edition/sha256，MANIFEST 版本指纹 + source_records_hash 失配即拒收）；`kg/query.py` 新增 `retrieve_doc`（FR-12 混合检索：图谱精确优先→别名解析→向量召回，无证据拒答走知识缺口）；`eval_rag.py` 评测 **20/20 通过（100% ≥ 90%）**。模型 `models/bge-small-zh/`（95MB，gitignore）与向量库 `kg_rag/`（gitignore，可重建）不入库。⚠️ 口径：向量召回文本为「依据知识库语料，非药典原文」（P2 已由药性/学习 Agent 挂载 `retrieve_doc`，证据链条目 UI 已上线）
- [x] **二期 P2 智能体侧**（2026-08-28，方案 §8 全落地）：**六子 Agent 编排**（`agent/tools.py` 10 工具重组：识药/鉴别/药性/方剂/安全/学习，新增 `similar_compare`（FR-13 鉴别对比，互录相似对 + 结论等级）+ `record_feedback`（学习 Agent 台账 `kg/data/feedback.json`，人工核对队列）；`agent/core.py` **轻量 Router**（`classify_agent` 规则优先：图片→识药、LEARN_PAT→学习、DIFF_KW→鉴别、SAFETY_KW→安全、FORMULA_KW→方剂、默认药性，单跳快速失败不级联）+ **配额制**（max_turns = min(MAX_TURNS, AGENTS quota)，识药3/鉴别4/药性4/方剂4/安全4/学习3）+ 子 Agent 工具子集 schema + 聚焦 System Prompt（`agent/prompts.py` BASE 底座 + AGENT_SECTIONS 六段）；**五道闸门**：① 输入（app.py 已有）② 识别（upload low_confidence → `refuse_reason=置信不足`）③ 知识（未收录→模型转述知识缺口）④ 业务（prompts 剂量模板：『你应该用 X 克』句式禁用、禁忌=知识展示）⑤ 输出（`_gate_answer`：DOSAGE_PAT/DIAG_PAT 兜底追加合规提示 → `refuse_reason=合规边界`）+ **证据链**（SOURCE_RE 提取版本化出处《…》+年版含（A 档）等级，去重 ≤6 条）；SSE 协议向后兼容扩展（tool 事件加 `agent` 字段、answer 事件加 `refuse_reason/evidence`）；**前端 UI**：时间轴 agent 徽章、回答下 refuse 标签（「知识缺口，未下结论」）+ 证据链块（ChatMessage.vue/TimelineItem.vue/style.css）。**真实 LLM 验证**：用例 2 安全 Agent（双药档案+禁忌双源+证据链 6 条+合规口径）✅ 用例 8 鉴别 Agent（similar_herbs×2+similar_compare，拒识原因=知识缺口——演示 12.2#8 换用藏红花演示此口径）✅ 用例 12 学习 Agent（核对→record_feedback 落盘 feedback.json）✅ 用例 10 剂量闸门（药典知识条目 6～12g，零违规句式）✅；**eval_safety.py 安全专项 10/10 通过**（剂量 3/禁忌 2/人群 2/相互作用 2/诊断 1，复用 core 断言模式，拒答 1 条=知识缺口）✅；前端 npm run build 通过
- [x] **二期 V2-A6 云端 VLM 辅助验证**（2026-08-28 落地 → 08-29 真实 key 评测 + 两轮口径修订，默认关闭）：`config.py` 独立 VISION 组（`VISION_API_URL` 默认阿里云百炼 compatible-mode `qwen-vl-max`/`KEY`/`TIMEOUT=5`/`ENABLED` 默认 false）+ `save_vision_config()/vision_state()`（复用 LLM 组热更新模式）；`agent/vision.py`（零 torch 依赖：PIL 预处理 ≤1024/≤1.5MB；verify = OpenAI 兼容 image_url base64 + json_object + **结构白名单**（20 类名∪别名归一、verdict∈{none,non_herb}、药性功效词段整段丢弃）+ 429 退避/402/断网 fail-soft；decide 决策矩阵；**域外粗分类 `category`**：non_herb 时 VLM 输出人类可读粗分类（如"猫科动物"，限 12 字/禁药名/禁知识词，纯展示不进知识链路））；`app.py` upload 二段裁决（`current_herb` 仅放行时写入防带偏；non_herb 拒答文案带「疑似『猫科动物』」hint）+ `/api/vision-config` GET/POST/test（1x1 像素图连通性测试）；`agent/tools.py` 新增 `vlm_verify` 工具（识药 Agent，`_inject_image_context` 提示同步替换——**顺带修复死提示**：原提示模型调 similar_compare 但该工具不在识药子集）；前端设置页"视觉验证"卡片（URL/Key/模型/开关/测试）+ 识别卡 vision 徽章（绿一致/黄分歧·维持本地/红域外图·带粗分类/灰未参与）；`eval_vision.py` 评测集（混淆对 20 张 + 宠物 6 张 + fail-soft 回归）。⚠️ **实测口径修订（两轮，关键）**：① conflict 不再拒答——qwen-vl-max 域内细粒度弱于本地（砂仁/豆蔻→草豆蔻、苦杏仁→乌梅系统性偏差），**VLM 只在域外拒绝上有否决权**（弱证据不能否决强证据），双通道 5/20 → **14/20 = 本地不劣化 ✓**；② **触发口径全量修订**——本地闭集分类器对域外图给任意置信度（0.49 的猫、0.94 的猫都有），原灰区三条件（T1 0.60~0.90 / T2 混淆对 / T3 候选差）把域外图全挡在门外，0.90+ 域外还被本地错误放行（宠物图实测 0.94 木瓜 status=ok）→ **should_use_vision 改为全量触发**（决策矩阵不改，域内图复核零副作用；成本 ~¥0.02/次+<5s 可接受），配套 app.py：conf<0.60 一律拒答（VLM 一致也不放行，防 0.49 猫图被误认药材后提信放行）。**全量触发后评测：宠物 6/6 全触发全拒答**（低置信 3 张 cat→猫科动物；beagle 0.94 木瓜/boxer 0.91 乌梅→**犬科动物**，高置信域外从「错误放行」变「域外否决」；WARN 0），混淆对 14/20 不回归 ✓，fail-soft ✓。顺带记录：识药→similar_compare 级联（方案 §8.3）用户已拍板**压后**
- [ ] 待办（二期 · 评审待确认，见报告附录 B）：60+ 味白名单范围（**已定**：就地取材 64 味 = 24 现有 + 26 minor 升级 + 14 新增调补茶饮药，见 `kg/data/whitelist.json`）、高风险相似对 ≤10 对清单（**已定 9 对**：见 whitelist.required_similar_pairs）、未收录药拒答口径（⚠️ 演示用例 12.2#8「决明子」已收录，改用例或换未收录药）、12.2 新增演示用例（第 8~13 条）是否作答辩验收、药典版本口径（**已拍板**：维持 2020 版）、三期（视觉侧/工程侧）是否纳入范围、VLM 供应商与密钥管理