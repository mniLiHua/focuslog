rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off
chcp 936 >nul
cd /d "%~dp0.."
echo   打发布包：程序 + 文档 + exe + 空数据骨架，不含任何真实日志
echo.
python "%~dp0..\工具\make_release.py"
if errorlevel 1 (
  echo   [失败] 见上面的错误信息。
  pause
  exit /b
)
pause
