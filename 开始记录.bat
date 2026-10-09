rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off
chcp 936 >nul
cd /d "%~dp0"

echo ============================================================
echo   开始记录
echo     1) 先清理旧版本残留（旧进程 / 旧开机自启）
echo     2) 立刻开始记录你在用什么程序
echo     3) 顺便设为"开机自动记录"（以后开机就自动开始）
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0工具\清理旧版.ps1"

tasklist /FI "IMAGENAME eq focus_tracker.exe" | find /i "focus_tracker.exe" >nul
if not errorlevel 1 (
  echo   [提示] 记录已经在跑了
) else (
  start "" "%~dp0焦点监控\focus_tracker.exe"
  echo   [OK] 已开始记录
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "FocusTracker" /t REG_SZ /d "%~dp0焦点监控\focus_tracker.exe" /f >nul
if errorlevel 1 (
  echo   [警告] 开机自启没设上（可能被安全软件拦了）
) else (
  echo   [OK] 已设为开机自动记录
)

echo.
echo   关掉这个窗口就行。所有数据在 专注记录\ 里，纯文本、不联网。
timeout /t 6 >nul
