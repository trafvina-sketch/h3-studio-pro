@echo off
chcp 65001 >nul 2>&1
title H3 Studio Desktop
cd /d "%~dp0electron"
echo ====================================================
echo    H3 Studio - AI Video Generator (Electron App)
echo ====================================================
echo.
echo [*] Dang khoi dong ung dung H3 Studio Desktop...
call npm start
if %ERRORLEVEL% NEQ 0 (
  echo.
  echo [!] Ung dung da dong hoac gap loi.
  pause
)
