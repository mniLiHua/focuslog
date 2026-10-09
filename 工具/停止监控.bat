rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off
chcp 936 >nul
taskkill /IM focus_tracker.exe /F >nul 2>nul
if errorlevel 1 (
  echo   记录本来就没在跑。
) else (
  echo   已停止记录（数据都在；双击根目录「开始记录.bat」随时继续）。
)
timeout /t 3 >nul
