@echo off
cd /d "%~dp0"
rem 预建 streamlit 凭据，跳过首次运行时的 Email 引导提示
if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    echo [general] > "%USERPROFILE%\.streamlit\credentials.toml"
    echo email = "" >> "%USERPROFILE%\.streamlit\credentials.toml"
)
echo Starting Pet Adoption Platform (Streamlit)...
"D:\CondaEnv\task\python.exe" -m streamlit run pet_app.py --browser.gatherUsageStats false
pause
