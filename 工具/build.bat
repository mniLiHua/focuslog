rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off
chcp 936 >nul
cd /d "%~dp0.."

echo ============================================================
echo   一键重打包三个 exe（改了 .py 必须跑，否则跑的还是旧版）
echo ============================================================
echo.

set DIST=%~dp0..\_build\dist
set WORK=%~dp0..\_build\work
set SPEC=%~dp0..\_build\spec
set COMMON=--onefile --clean --noconfirm --paths "%~dp0..\焦点监控" --paths "%~dp0..\专注记录" --hidden-import category_utils --hidden-import archive_utils --hidden-import summary --hidden-import chart_templates --hidden-import dashboard --distpath "%DIST%" --workpath "%WORK%" --specpath "%SPEC%"

echo [1/3] focus_tracker.exe ...
python -m PyInstaller %COMMON% --noconsole --name focus_tracker "%~dp0..\焦点监控\focus_tracker.py"
if errorlevel 1 goto fail

echo [2/3] summary.exe ...
python -m PyInstaller %COMMON% --console --name summary "%~dp0..\专注记录\summary.py"
if errorlevel 1 goto fail

echo [3/3] focuspanel.exe ...
python -m PyInstaller %COMMON% --console --name focuspanel "%~dp0..\专注记录\panel_server.py"
if errorlevel 1 goto fail

copy /y "%DIST%\focus_tracker.exe" "%~dp0..\焦点监控\focus_tracker.exe" >nul
copy /y "%DIST%\summary.exe" "%~dp0..\专注记录\summary.exe" >nul
copy /y "%DIST%\focuspanel.exe" "%~dp0..\专注记录\focuspanel.exe" >nul
rmdir /s /q "%~dp0..\_build"

echo.
echo   [完成] 三个 exe 已更新
echo   提示：监控在跑的话，先双击根目录「停止监控.bat」再重打包
pause
exit /b

:fail
echo.
echo   [失败] 见上面的错误信息（通常是没装 PyInstaller：pip install pyinstaller）
pause
