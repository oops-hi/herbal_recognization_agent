@echo off
REM ============================================================
REM build-backend.bat — PyInstaller onedir 打包 Flask 后端（含 torch）
REM 红线：必须用 task 环境的 python（裸 python 是坏的 base Anaconda）
REM 用法：scripts\build-backend.bat
REM 产物：dist-backend\herbal-backend\herbal-backend.exe
REM ============================================================
setlocal
cd /d "%~dp0\.."

echo [1/3] 构建 Vue 前端（frontend/dist）
cd frontend
call npm run build || goto :err
cd ..

echo [2/3] PyInstaller 打包后端（约 15~40 分钟，磁盘预留 15GB）
D:\CondaEnv\task\python.exe -m PyInstaller --clean --noconfirm herbal-backend.spec --distpath dist-backend || goto :err

echo [3/3] 完成：dist-backend\herbal-backend\herbal-backend.exe
exit /b 0

:err
echo 打包失败，见上方错误输出
exit /b 1
