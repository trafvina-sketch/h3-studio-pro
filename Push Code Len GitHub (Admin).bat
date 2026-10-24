@echo off
chcp 65001 >nul 2>&1
title Push H3 Studio Pro Len GitHub (Admin)
cd /d "%~dp0"
echo ====================================================
echo    DAY BAN CAP NHAT H3 STUDIO PRO LEN GITHUB (ADMIN)
echo ====================================================
echo.

set "PY_EXE=python"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if exist "C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe" set "PY_EXE=C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe"

"%PY_EXE%" -X utf8 -c "import dashboard, json; cfg = dashboard.load_github_update_config(); repo = cfg.get('repo_url') or input('Nhap link GitHub Repo (VD: https://github.com/username/h3-studio-pro): ').strip(); msg = input('Nhap ghi chu ban cap nhat (Enter = Update H3 Studio Pro): ').strip() or 'Update H3 Studio Pro'; res = dashboard.perform_github_push({'repo_url': repo, 'commit_message': msg}); print('\n[KET QUA]:', json.dumps(res, ensure_ascii=False, indent=2))"
echo.
pause
