@echo off
chcp 65001 >nul 2>&1
title H3 Studio Pro - AI Video Generator
cd /d "%~dp0"
echo ====================================================
echo    H3 STUDIO PRO - AI VIDEO GENERATOR (LUXURY UI)
echo ====================================================
echo   Dang khoi dong may chu tai: http://127.0.0.1:8030
echo   Trinh duyet se tu dong mo sau 2 giay...
echo   Nhan Ctrl+C de dung.
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8030"

if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -X utf8 -u "%~dp0dashboard.py"
) else if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" -X utf8 -u "%~dp0dashboard.py"
) else if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" -X utf8 -u "%~dp0dashboard.py"
) else if exist "C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe" (
    "C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe" -X utf8 -u "%~dp0dashboard.py"
) else (
    python -X utf8 -u "%~dp0dashboard.py"
)
pause
