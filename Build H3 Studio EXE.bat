@echo off
chcp 65001 >nul 2>&1
title Build H3 Studio EXE
cd /d "%~dp0electron"
echo ====================================================
echo    Dong goi H3 Studio thanh file EXE Windows
echo ====================================================
echo.
echo [*] Dang tien hanh dong goi file EXE...
call npm run dist
if %ERRORLEVEL% EQU 0 (
  echo.
  echo [OK] Build thanh cong file EXE!
  echo      Thu muc chua file: %~dp0electron\dist\
  explorer "%~dp0electron\dist"
) else (
  echo.
  echo [!] Co loi trong qua trinh dong goi EXE.
)
echo.
pause
