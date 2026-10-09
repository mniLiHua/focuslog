rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off
chcp 936 >nul
cd /d "%~dp0.."

echo ============================================================
echo   旧数据迁移（换电脑 / 从旧版本搬历史记录）
echo.
echo   最省事的用法：在资源管理器里把【旧版文件夹】拖到
echo   本文件（迁移旧数据.bat）的图标上，松手就开始搬。
echo   也可以按下面的提示手动输入路径。
echo ============================================================
echo.

if not "%~1"=="" (
  echo   [拖放模式] 旧目录：%~1
  echo.
  python "%~dp0..\工具\migrate.py" --from "%~1"
  echo.
  pause
  exit /b
)

set /p OLDDIR=请输入旧版文件夹路径（就是含 focus_tracker.exe 或 专注记录 的那个目录），回车确认: 
if "%OLDDIR%"=="" exit /b
python "%~dp0..\工具\migrate.py" --from "%OLDDIR%"
echo.
pause
