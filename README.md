# 🌿 多模态中草药识别智能体

融合**视觉与文本多模态 AI**：图像分类（YOLOv8n-cls，20 类中药饮片）+ 知识图谱（NetworkX + JSON）+ 多模态交互（手写 Function Calling 智能体，DeepSeek API）。

上传一张饮片照片 → 自动识别 → 用自然语言追问药性、配伍禁忌与经典方剂，**每条结论可溯源到知识图谱节点与典籍出处**。

## 与同类产品/项目的区别（为什么做智能体而不是流水线）

市面上多数"识别 + LLM"项目是固定流水线——LLM 只做末端文案生成。本项目是**真正的智能体**：

```
你问：「这个能和菊花一起泡水吗？我最近眼睛干」
智能体自主决定：识别 → 查枸杞子档案 → 查菊花档案 → 查配伍禁忌 → 查方剂 → 汇总回答
```

**调用顺序与调用次数由问题决定，不由代码决定。** 这是本项目答辩时的核心差异点（详见 `docs/需求分析.md`）。

## 功能特性

- 🖼️ 上传/拖拽图片 → CNN 识别 → Top-3 中文名 + 置信度
- ⚠️ 置信度 < 0.75 时**拒绝下结论**并给出补拍建议（不硬答）
- 📖 单味药完整档案：性味/归经/功效/主治/用量/禁忌/毒性（带《中国药典》出处）
- ⛔ 配伍禁忌知识提示：十八反/十九畏（**歌诀 + 药典双源**，差异处如实说明）/ 毒性分级 / 妊娠禁忌
- 🌐 知识图谱可视化（D3 交互式力导向图，**离线可用**，断网自动降级静态渲染）
- 🤖 智能体对话：展示每步工具调用链（SSE 流式）
- 🖥️ 桌面版：Vue 3 前端 + Electron + PyInstaller 全打包（见下文）
- ⚖️ 免责声明：本系统仅供学习研究，不构成任何诊疗建议

## 项目结构

```
├── config.py            # 全局配置（含 PyInstaller frozen 分支）
├── train.py             # YOLOv8n-cls 训练（→ models/best.pt）
├── evaluate.py          # 跨域测试（药房实拍集）
├── prepare_dataset.py   # 数据集切分
├── classifier/          # 模型加载与 Top-k 预测
├── kg/                  # 知识图谱：数据 + 构建 + 查询 + 可视化
├── agent/               # 智能体：工具 + Prompt + ReAct 循环 ★
├── demo_agent.py        # 命令行智能体（先于 Web 调试）
├── app.py               # Flask 后端（动态端口 + Vue 产物托管）
├── templates/ static/   # 旧 Web 版前端（保留兜底）
├── frontend/            # ★ Vue 3 + Electron 桌面版工程
├── herbal-backend.spec  # PyInstaller 打包配置
├── scripts/             # 打包脚本（build-backend / build-all）
├── docs/需求分析.md      # 产品调研 + 需求规格（v0.9）
└── data/                # 数据集（手动下载，见 data/raw/README.md）
```

## 环境要求

- Python 3.10（conda 环境 `task`，解释器 `D:\CondaEnv\task\python.exe`）
- torch 2.12 **cu128** + RTX 5060（Blackwell sm_120）
- 依赖安装**务必按 CLAUDE.md「环境红线」操作**，直接 `pip install -r requirements.txt` 会破坏显卡

## 快速开始

```bash
# 1. 配置 DeepSeek 密钥（可选：不配则仅识别+图谱功能可用，对话降级）
copy .env.example .env        # 然后编辑填入 DEEPSEEK_API_KEY=sk-xxx

# 2. 下载数据集（浏览器） → data/raw/，见 data/raw/README.md

# 3. 准备数据 + 训练（→ models/best.pt）
conda run -n task python prepare_dataset.py
conda run -n task python train.py

# 4. 命令行体验智能体
conda run -n task python demo_agent.py --image <图片> --q "这个能和菊花一起泡水吗？我最近眼睛干"

# 5. 启动 Web
conda run -n task python app.py    # http://localhost:5000
```

## 🖥️ 桌面版（Vue 3 + Electron + EXE）

前端已用 **Vue 3 SPA（Vite）** 重写，并以 **Electron** 桌面壳 + **PyInstaller 全打包** 产出 Windows 安装包（内置 torch / YOLO / 知识图谱，单机运行，不依赖 Python 环境）。

```
frontend/                 # electron-vite 工程（Vue3 + TS + D3 力导向图谱）
  ├── src/main/           # Electron 主进程：spawn 后端 → stdout 握手端口 → 窗口
  ├── src/renderer/       # Vue3 SPA（#/ 主页、#/graph 图谱，hash 路由）
  └── electron-builder.yml# 安装包配置（后端 onedir 经 extraResources 嵌入）
herbal-backend.spec       # PyInstaller spec（onedir，含 models/kg.json/Vue 产物）
scripts/build-backend.bat # 仅打包后端 exe
scripts/build-all.bat     # 一键出包（自动走 C:\herbal_stage 英文路径）
```

### 开发模式（热更新）

```bash
cd frontend
npm install                # 首次（.npmrc 已配 npmmirror 镜像）
npm run dev                # 自动拉起 Conda task python 后端 + Electron 窗口
```

### 出 Windows 安装包

```bash
scripts\build-all.bat      # 前端构建 → 后端 PyInstaller → NSIS 安装包
# 产物：C:\herbal_stage\frontend\release\HerbalAgent-Setup-<version>.exe
```

- 安装后运行：双击快捷方式「中草药识别智能体」→ 自动启动后端（控制台窗口可见握手端口）
- DeepSeek 密钥：在安装目录（exe 同目录）放一个 `.env`（`DEEPSEEK_API_KEY=sk-xxx`），不配则识别/图谱可用、对话降级
- 环境红线不变：PyInstaller 必须用 `D:\CondaEnv\task\python.exe`；出包动作在英文路径（项目路径含中文，NSIS 有缺陷）

## 相关资料

- 数据集：NB-TCM-CHM（宁波大学，CC BY 4.0）— [Mendeley Data](https://data.mendeley.com/datasets/2kjmzjyrmd/2)（DOI: 10.17632/2kjmzjyrmd.3）
- 药品标准：《中国药典》2020 年版一部
- 参考项目：[pet_recognition](D:/code/py/pet_recognition)（Flask 上传→分类→DeepSeek 讲解，本项目沿用其工程约定）

> ⚠️ 本系统仅供学习与科普参考，不构成医疗、诊断或用药建议；识别结果请以执业药师或专业机构鉴定为准。
