rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off
chcp 936 >nul
cd /d "%~dp0.."
REM 只看一眼：生成静态快照，不起服务（也就不能用工具按钮）
if exist "%~dp0..\专注记录\focuspanel.exe" (
  "%~dp0..\专注记录\focuspanel.exe" --static
  exit /b
)
where python >nul 2>nul
if errorlevel 1 (
  echo   [缺少运行环境] 没找到 focuspanel.exe，也没找到 python。
  pause
  exit /b
)
echo   正在生成面板...
python "%~dp0..\专注记录\dashboard.py"
if errorlevel 1 (
  echo   [生成失败] 见上面的错误信息。
  pause
  exit /b
)
start "" "%~dp0..\专注记录\面板.html"
