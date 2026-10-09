rem FocusLog —— 本地时间记录  |  Copyright (C) 2026 冰叁狼
rem SPDX-License-Identifier: GPL-3.0-only
@echo off

chcp 936 >nul

cd /d "%~dp0"



echo ============================================================

echo   时间面板

echo   浏览器会自动打开；这个黑窗口是服务本体，关掉它就停止服务。

echo   （记录不受影响，记录由「开始记录.bat」负责）

echo.

echo   面板右上角「工具」里：分类助手 / 分类管理 / 导入旧数据 /

echo     系统设置（开机自启、提醒开关、每日目标）/ 生成周报 / 导出 CSV

echo ============================================================

echo.



if exist "%~dp0专注记录\focuspanel.exe" (

  "%~dp0专注记录\focuspanel.exe"

  echo.

  echo   [本次窗口结束 —— 面板服务仍在后台运行，网页可正常使用]

echo   10 秒后自动关闭本窗口，按任意键立即关闭...

  timeout /t 10

  exit /b

)



where python >nul 2>nul

if errorlevel 1 (

  echo   [缺少运行环境] 没找到 focuspanel.exe，也没找到 python。

  echo   请先双击「更新.bat」或从发布包重新获取。

  timeout /t 10

  exit /b

)

python "%~dp0专注记录\panel_server.py"

echo.

echo   [面板服务已停止 —— 下次双击本文件可重新打开]

echo   10 秒后自动关闭本窗口，按任意键立即关闭...

echo   10 秒后自动关闭本窗口，按任意键立即关闭...

timeout /t 10

