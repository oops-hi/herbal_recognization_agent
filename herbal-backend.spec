# -*- mode: python ; coding: utf-8 -*-
"""
herbal-backend.spec — PyInstaller onedir 打包 Flask 后端（含 torch / YOLO / 知识图谱）。

用法（红线：必须用 task 环境的 python，裸 python 是坏的 base Anaconda）：
    D:\\CondaEnv\\task\\python.exe -m PyInstaller --clean --noconfirm herbal-backend.spec --distpath dist-backend

产物：dist-backend/herbal-backend/herbal-backend.exe（console 窗口，答辩可见启动日志/握手端口）
"""
import os
from PyInstaller.utils.hooks import collect_all

# ultralytics 动态加载多（cfg/字体等资源），collect_all 全收
dg, db, dh = collect_all("ultralytics")

# ⚠️ 旧版 VC++ 运行时雷（WinError 1114 修复，2026-08-28）：
# PyInstaller 会把 conda 环境的 vcruntime140/msvcp140（14.27，2019 年）与
# ucrtbase（10.0.22621）收进 _internal；Windows DLL 搜索顺序里 _internal 抢在
# System32 之前，导致 torch 2.12 cu128 的 c10.dll 加载到过旧运行时 → 1114
# DLL 初始化失败。Windows 10/11 系统级内置 14.51+/26100+ 版本才满足要求，
# 故打包时剔除这些 DLL，让 exe 运行时从 System32 加载。
RUNTIME_EXCLUDE = {
    "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_threads.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",  # 全部小写：下方按文件名 lower() 后匹配
    "ucrtbase.dll",
}

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=db,
    datas=[
        ("models/best.pt", "models"),        # YOLO 权重（2.9MB）
        ("models/class_map.json", "models"), # 类别映射
        ("kg/data/kg.json", "kg/data"),      # 知识图谱单文件
        ("frontend/out/renderer", "frontend_dist"),  # Vue 构建产物（HERB_SERVE_DIST=1 时托管）
    ] + dg,
    hiddenimports=dh + ["flask", "PIL._tkinter_finder"],
    excludes=[
        # 训练/评估链脚本（其 sklearn/matplotlib 依赖绝不卷入，防止 DLL 顺序雷）
        "train", "evaluate", "prepare_dataset", "demo_agent", "kg.viz",
        # 重型分析依赖（app 链路零引用）
        "tkinter", "matplotlib", "sklearn", "pandas", "polars",
    ],
    noarchive=False,
)

# 剔除旧版 VC++/UCRT 运行时（见 RUNTIME_EXCLUDE 注释）：
# binaries 元素 = (源路径, 目标名, typecode)，按文件名过滤
a.binaries = [
    b for b in a.binaries
    if os.path.basename(b[0]).lower() not in RUNTIME_EXCLUDE
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="herbal-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # torch DLL 体积大，UPX 压缩慢且易误报，关掉
    console=True,       # 保留控制台：答辩现场可见握手端口/启动日志
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="herbal-backend",
)
