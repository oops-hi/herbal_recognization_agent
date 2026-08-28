@echo off
REM ============================================================
REM build-all.bat — 一键出包：Vue 前端构建 → 后端 PyInstaller → electron-builder NSIS 安装包
REM
REM 中文路径规避（关键）：项目路径含中文「多模态中草药识别智能体」，
REM electron-builder / NSIS 对非 ASCII 构建路径有历史缺陷，全部出包动作
REM 在纯英文暂存目录 C:\herbal_stage 进行，产物也落在其中。
REM
REM 产物：C:\herbal_stage\release\HerbalAgent-Setup-<version>.exe
REM ============================================================
setlocal
set "SRC=%~dp0\.."
set "STAGE=C:\herbal_stage"
set "STAGE_SRC=%STAGE%\src"

echo [1/5] 构建 Vue 前端
cd /d "%SRC%\frontend"
call npm run build || goto :err

echo [2/5] 暂存到英文路径（robocopy 只拷贝运行时需要的）
REM ⚠️ robocopy 成功退出码是 1（非 0），不能用 || goto :err，必须 GEQ 8 才算失败
if exist "%STAGE%" rd /s /q "%STAGE%"
mkdir "%STAGE%"
robocopy "%SRC%\frontend\out\renderer" "%STAGE_SRC%\frontend\out\renderer" /E /NFL /NDL /NJH /NJS
if %ERRORLEVEL% GEQ 8 goto :err
robocopy "%SRC%\models"        "%STAGE_SRC%\models"    /E /NFL /NDL /NJH /NJS
if %ERRORLEVEL% GEQ 8 goto :err
robocopy "%SRC%\kg\data"       "%STAGE_SRC%\kg\data"   /E /NFL /NDL /NJH /NJS
if %ERRORLEVEL% GEQ 8 goto :err
copy /y "%SRC%\app.py"                      "%STAGE_SRC%\app.py" >nul
copy /y "%SRC%\config.py"                   "%STAGE_SRC%\config.py" >nul
copy /y "%SRC%\herbal-backend.spec"         "%STAGE_SRC%\herbal-backend.spec" >nul
xcopy /e /i /q /y "%SRC%\agent"             "%STAGE_SRC%\agent" >nul
xcopy /e /i /q /y "%SRC%\classifier"        "%STAGE_SRC%\classifier" >nul
xcopy /e /i /q /y "%SRC%\kg"                "%STAGE_SRC%\kg" >nul
if not exist "%STAGE_SRC%\models" goto :err

echo [3/5] PyInstaller 打包后端（15~40 分钟，磁盘预留 15GB）
cd /d "%STAGE_SRC%"
D:\CondaEnv\task\python.exe -m PyInstaller --clean --noconfirm herbal-backend.spec --distpath "%STAGE%\dist-backend" || goto :err

echo [4/5] 前端工程 + 后端 onedir 合入 electron-builder
REM ⚠️ 排除 out 后必须补拷整个 out（main+preload+renderer 缺一不可：
REM    electron-vite 的 asar 入口 out\main\index.js 缺失会报 asar corrupted）
robocopy "%SRC%\frontend" "%STAGE%\frontend" /E /XD dist out release /NFL /NDL /NJH /NJS
if %ERRORLEVEL% GEQ 8 goto :err
robocopy "%SRC%\frontend\out" "%STAGE%\frontend\out" /E /NFL /NDL /NJH /NJS
if %ERRORLEVEL% GEQ 8 goto :err
if not exist "%STAGE%\dist-backend\herbal-backend\herbal-backend.exe" goto :err

echo [5/5] electron-builder 出 NSIS 安装包
cd /d "%STAGE%\frontend"
call node_modules\.bin\electron-builder.cmd --win --x64 || goto :err

echo.
echo 完成！安装包：%STAGE%\frontend\release\HerbalAgent-Setup-*.exe
exit /b 0

:err
echo 出包失败，见上方错误输出
exit /b 1
